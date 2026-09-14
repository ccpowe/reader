"""The authenticated user's chronological reading feed."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.core.auth import AuthenticatedUser, require_current_user
from app.domain.enums import MediaType
from app.services.x_feed_groups import grouped_feed_entries
from app.services.x_relations import XPreview, x_preview
from app.storage.database import get_session
from app.storage.models import (
    Content,
    FeedSource,
    SourceEntry,
    SourceSubscription,
    UserSavedContent,
)
from app.translation.domain import TranslationProjection
from app.translation.projection import (
    TitleInput,
    TitleTranslationContext,
    get_title_translation_context,
    project_titles,
    translation_response_values,
)

router = APIRouter(prefix="/feed", tags=["feed"])


class FeedItemResponse(BaseModel):
    content_id: str
    source_id: str
    source_name: str | None
    source_avatar_url: str | None
    source_kind: str
    title: str
    translated_title: str | None
    translation_locale: str | None
    translation_status: str | None
    excerpt: str | None
    external_url: str
    author_name: str | None
    published_at: datetime | None
    fetched_at: datetime
    thumbnail_url: str | None
    is_saved: bool
    x_preview: XPreview | None = None


class ArticleResponse(BaseModel):
    content_id: str
    title: str
    translated_title: str | None
    translation_locale: str | None
    translation_status: str | None
    body_html: str | None
    body_text: str | None
    external_url: str
    author_name: str | None
    published_at: datetime | None


class FeedPageResponse(BaseModel):
    items: list[FeedItemResponse]
    next_cursor: str | None


@router.get("", response_model=list[FeedItemResponse])
async def list_feed(
    limit: int = Query(default=50, ge=1, le=100),
    folder_name: str | None = Query(default=None, max_length=120),
    source_id: UUID | None = Query(default=None),
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[FeedItemResponse]:
    """Return content only from sources to which the caller is subscribed."""
    items, _next_cursor = await _query_feed_page(
        session,
        current_user=current_user,
        folder_name=folder_name,
        source_id=source_id,
        limit=limit,
        cursor=None,
    )
    return items


@router.get("/page", response_model=FeedPageResponse)
async def list_feed_page(
    limit: int = Query(default=20, ge=1, le=50),
    folder_name: str | None = Query(default=None, max_length=120),
    source_id: UUID | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=512),
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> FeedPageResponse:
    """Return a stable, deduplicated page of the caller's feed."""
    items, next_cursor = await _query_feed_page(
        session,
        current_user=current_user,
        folder_name=folder_name,
        source_id=source_id,
        limit=limit,
        cursor=_decode_feed_cursor(cursor) if cursor else None,
    )
    return FeedPageResponse(items=items, next_cursor=next_cursor)


async def _query_feed_page(
    session: AsyncSession,
    *,
    current_user: AuthenticatedUser,
    folder_name: str | None,
    source_id: UUID | None,
    limit: int,
    cursor: tuple[datetime, UUID] | None,
) -> tuple[list[FeedItemResponse], str | None]:
    """Read one page with SQL-level content de-duplication and keyset paging."""
    translation_context = await get_title_translation_context(session, current_user.id)
    filters = _feed_scope_filters(
        user_id=current_user.id,
        folder_name=folder_name,
        source_id=source_id,
    )

    # One source-owned Content can still be reached through multiple native
    # entries from that authoritative source. Rank entries per Content before
    # applying a page boundary so those duplicates do not consume slots.
    sort_at = SourceEntry.feed_sort_at
    ranked_entries = (
        select(
            SourceEntry.id.label("entry_id"),
            sort_at.label("sort_at"),
            SourceEntry.source_id.label("source_id"),
            SourceEntry.native_id.label("native_id"),
            FeedSource.kind.label("source_kind"),
            SourceEntry.raw_metadata["x"]["author"]["id"].astext.label("author_id"),
            SourceEntry.raw_metadata["x"]["reply_to"]["tweet_id"].astext.label("reply_id"),
            SourceEntry.raw_metadata["x"]["reply_to"]["author_id"].astext.label("reply_author_id"),
            SourceEntry.raw_metadata["x"]["is_repost"].astext.label("is_repost"),
            func.row_number()
            .over(
                partition_by=SourceEntry.content_id,
                order_by=(sort_at.desc(), SourceEntry.id.desc()),
            )
            .label("content_rank"),
        )
        .join(FeedSource, SourceEntry.source_id == FeedSource.id)
        .join(
            SourceSubscription,
            and_(
                SourceSubscription.source_id == FeedSource.id,
                SourceSubscription.user_id == current_user.id,
                SourceSubscription.is_enabled.is_(True),
            ),
        )
        .where(*filters)
        .subquery()
    )
    groups = grouped_feed_entries(ranked_entries)
    page_filters = []
    if cursor is not None:
        cursor_at, cursor_entry_id = cursor
        page_filters.append(
            or_(
                SourceEntry.feed_sort_at < cursor_at,
                and_(
                    SourceEntry.feed_sort_at == cursor_at,
                    SourceEntry.id < cursor_entry_id,
                ),
            )
        )
    rows = await session.execute(
        select(
            SourceEntry,
            Content,
            FeedSource,
            SourceSubscription,
            UserSavedContent,
            groups.c.loaded_count,
        )
        .options(selectinload(Content.media))
        .join(Content, SourceEntry.content_id == Content.id)
        .join(FeedSource, SourceEntry.source_id == FeedSource.id)
        .join(
            SourceSubscription,
            and_(
                SourceSubscription.source_id == FeedSource.id,
                SourceSubscription.user_id == current_user.id,
                SourceSubscription.is_enabled.is_(True),
            ),
        )
        .join(groups, groups.c.entry_id == SourceEntry.id)
        .outerjoin(
            UserSavedContent,
            and_(
                UserSavedContent.content_id == Content.id,
                UserSavedContent.user_id == current_user.id,
            ),
        )
        .where(*page_filters)
        .order_by(SourceEntry.feed_sort_at.desc(), SourceEntry.id.desc())
        .limit(limit + 1)
    )
    page_rows = list(rows)
    has_more = len(page_rows) > limit
    page_rows = page_rows[:limit]
    title_projections = await project_titles(
        session,
        [TitleInput(item_id=str(content.id), text=content.title) for _, content, *_ in page_rows],
        context=translation_context,
    )
    await session.commit()
    response = [
        _feed_item_response(
            entry,
            content,
            source,
            subscription,
            saved,
            translation_context,
            title_projections.get(str(content.id)),
            loaded_count=loaded_count,
        )
        for entry, content, source, subscription, saved, loaded_count in page_rows
    ]
    if not has_more or not page_rows:
        return response, None
    last_entry = page_rows[-1][0]
    last_sort_at = last_entry.feed_sort_at
    return response, _encode_feed_cursor(last_sort_at, last_entry.id)


def _feed_scope_filters(
    *,
    user_id: UUID,
    folder_name: str | None,
    source_id: UUID | None,
) -> list[ColumnElement[bool]]:
    """Build an authenticated feed scope; source filtering never bypasses the subscription."""
    filters: list[ColumnElement[bool]] = [
        SourceSubscription.user_id == user_id,
        SourceSubscription.is_enabled.is_(True),
    ]
    if folder_name is not None:
        if folder_name == "__uncategorized__":
            filters.append(SourceSubscription.folder_name.is_(None))
        else:
            filters.append(SourceSubscription.folder_name == folder_name)
    if source_id is not None:
        filters.append(FeedSource.id == source_id)
    return filters


def _feed_item_response(
    entry: SourceEntry,
    content: Content,
    source: FeedSource,
    subscription: SourceSubscription,
    saved: UserSavedContent | None,
    translation_context: TitleTranslationContext,
    projection: TranslationProjection | None,
    loaded_count: int = 1,
) -> FeedItemResponse:
    translated_title, translation_locale, translation_status = translation_response_values(
        translation_context,
        projection,
    )
    return FeedItemResponse(
        content_id=str(content.id),
        source_id=str(source.id),
        source_name=subscription.custom_name or source.display_name,
        source_avatar_url=source.avatar_url,
        source_kind=str(source.kind),
        # Content is authoritative only for its owning Source. SourceEntry keeps
        # native-entry metadata and the external URL.
        title=getattr(content, "title", None) or entry.title,
        translated_title=translated_title,
        translation_locale=translation_locale,
        translation_status=translation_status,
        excerpt=content.excerpt,
        external_url=entry.external_url,
        author_name=entry.author_name,
        published_at=entry.published_at,
        fetched_at=entry.fetched_at,
        thumbnail_url=next(
            (
                media.poster_url or media.original_url
                for media in sorted(content.media, key=lambda media: media.sort_order)
                if media.media_type == MediaType.IMAGE and media.is_active
            ),
            None,
        ),
        is_saved=saved is not None,
        x_preview=x_preview(entry, content, loaded_count) if str(source.kind) == "x" else None,
    )


def _encode_feed_cursor(sort_at: datetime, entry_id: UUID) -> str:
    payload = json.dumps(
        {"at": sort_at.isoformat(), "entry_id": str(entry_id)}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_feed_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8"))
        cursor_at = datetime.fromisoformat(payload["at"])
        if cursor_at.tzinfo is None or cursor_at.utcoffset() is None:
            raise ValueError("feed cursor timestamp must include a timezone")
        return cursor_at.astimezone(UTC), UUID(payload["entry_id"])
    except (binascii.Error, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid feed cursor."
        ) from exc


@router.get("/{content_id}", response_model=ArticleResponse)
async def get_article(
    content_id: UUID,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> ArticleResponse:
    """Return an article from an active subscription or the caller's saved list."""
    translation_context = await get_title_translation_context(session, current_user.id)
    active_subscription = exists(
        select(SourceSubscription.id).where(
            SourceSubscription.source_id == SourceEntry.source_id,
            SourceSubscription.user_id == current_user.id,
            SourceSubscription.is_enabled.is_(True),
        )
    )
    row = await session.execute(
        select(
            Content,
            SourceEntry,
        )
        .join(SourceEntry, SourceEntry.content_id == Content.id)
        .outerjoin(
            UserSavedContent,
            and_(
                UserSavedContent.content_id == Content.id,
                UserSavedContent.user_id == current_user.id,
            ),
        )
        .where(
            Content.id == content_id,
            or_(active_subscription, UserSavedContent.content_id.is_not(None)),
        )
        .limit(1)
    )
    result = row.first()
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found.")
    content, entry = result
    title_projections = await project_titles(
        session,
        [TitleInput(item_id=str(content.id), text=content.title)],
        context=translation_context,
    )
    await session.commit()
    translated_title, translation_locale, translation_status = translation_response_values(
        translation_context,
        title_projections.get(str(content.id)),
    )
    return ArticleResponse(
        content_id=str(content.id),
        title=content.title,
        translated_title=translated_title,
        translation_locale=translation_locale,
        translation_status=translation_status,
        body_html=content.body_html,
        body_text=content.body_text,
        external_url=entry.external_url,
        author_name=content.author_name,
        published_at=content.published_at,
    )
