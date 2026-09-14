"""Endpoints for a user's saved reading list."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import AuthenticatedUser, require_current_user
from app.domain.enums import MediaType
from app.services.ranking_saved import accessible_url_query, persist_ranking_item
from app.services.ranking_snapshots import RankingRequest
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

router = APIRouter(prefix="/saved", tags=["saved"])


class SavedStateResponse(BaseModel):
    content_id: str
    is_saved: bool


class RankingSavedStateResponse(BaseModel):
    content_id: str | None
    is_saved: bool


class SaveRankingRequest(BaseModel):
    kind: Literal["hacker_news", "reddit", "github"]
    url: str = Field(min_length=1, max_length=8192)
    subreddit: str = Field(default="MachineLearning", min_length=2, max_length=100)
    sort: Literal["hot", "rising", "top"] = "hot"
    time_filter: Literal["day", "week", "month", "year"] = "week"


class SavedItemResponse(BaseModel):
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
    is_saved: bool = True
    ranking_kind: Literal["hacker_news", "reddit", "github"] | None = None


class SavedPageResponse(BaseModel):
    items: list[SavedItemResponse]
    next_cursor: str | None


async def _assert_accessible_content(
    content_id: UUID,
    current_user: AuthenticatedUser,
    session: AsyncSession,
) -> None:
    accessible = await session.scalar(
        select(Content.id)
        .join(SourceEntry, SourceEntry.content_id == Content.id)
        .join(SourceSubscription, SourceSubscription.source_id == SourceEntry.source_id)
        .where(
            Content.id == content_id,
            SourceSubscription.user_id == current_user.id,
            SourceSubscription.is_enabled.is_(True),
        )
        .limit(1)
    )
    if accessible is None:
        accessible = await session.scalar(
            select(Content.id)
            .join(FeedSource, Content.authority_source_id == FeedSource.id)
            .where(
                Content.id == content_id,
                FeedSource.visibility == "shared",
                FeedSource.config["ranking_archive"].as_boolean().is_(True),
            )
            .limit(1)
        )
    if accessible is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found.")


@router.get("", response_model=list[SavedItemResponse])
async def list_saved_content(
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SavedItemResponse]:
    """Return the caller's saved items, newest saved first."""
    items, _next_cursor = await _query_saved_page(
        session, current_user=current_user, limit=100, cursor=None
    )
    return items


@router.get("/page", response_model=SavedPageResponse)
async def list_saved_content_page(
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, max_length=200),
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SavedPageResponse:
    """Return a stable saved-items page with SQL-level source de-duplication."""
    items, next_cursor = await _query_saved_page(
        session,
        current_user=current_user,
        limit=limit,
        cursor=_decode_saved_cursor(cursor) if cursor else None,
        query=q.strip() if q and q.strip() else None,
    )
    return SavedPageResponse(items=items, next_cursor=next_cursor)


async def _query_saved_page(
    session: AsyncSession,
    *,
    current_user: AuthenticatedUser,
    limit: int,
    cursor: tuple[datetime, UUID] | None,
    query: str | None = None,
) -> tuple[list[SavedItemResponse], str | None]:
    """Choose one source presentation for each durable saved Content in SQL."""
    translation_context = await get_title_translation_context(session, current_user.id)
    active_subscription = and_(
        SourceSubscription.source_id == FeedSource.id,
        SourceSubscription.user_id == current_user.id,
        SourceSubscription.is_enabled.is_(True),
    )
    ranked_entries = (
        select(
            UserSavedContent.content_id.label("content_id"),
            UserSavedContent.saved_at.label("saved_at"),
            SourceEntry.id.label("entry_id"),
            func.row_number()
            .over(
                partition_by=UserSavedContent.content_id,
                order_by=(
                    SourceSubscription.id.is_not(None).desc(),
                    SourceEntry.published_at.desc(),
                    SourceEntry.id.desc(),
                ),
            )
            .label("source_rank"),
        )
        .join(SourceEntry, SourceEntry.content_id == UserSavedContent.content_id)
        .join(FeedSource, SourceEntry.source_id == FeedSource.id)
        .outerjoin(SourceSubscription, active_subscription)
        .where(UserSavedContent.user_id == current_user.id)
        .subquery()
    )
    filters = [ranked_entries.c.source_rank == 1]
    if cursor is not None:
        cursor_at, cursor_content_id = cursor
        filters.append(
            or_(
                ranked_entries.c.saved_at < cursor_at,
                and_(
                    ranked_entries.c.saved_at == cursor_at,
                    ranked_entries.c.content_id < cursor_content_id,
                ),
            )
        )
    if query:
        escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped_query}%"
        filters.append(
            or_(
                Content.title.ilike(pattern, escape="\\"),
                Content.excerpt.ilike(pattern, escape="\\"),
                SourceEntry.title.ilike(pattern, escape="\\"),
                SourceEntry.author_name.ilike(pattern, escape="\\"),
                FeedSource.display_name.ilike(pattern, escape="\\"),
                SourceSubscription.custom_name.ilike(pattern, escape="\\"),
            )
        )
    rows = await session.execute(
        select(
            UserSavedContent,
            SourceEntry,
            Content,
            FeedSource,
            SourceSubscription,
        )
        .options(selectinload(Content.media))
        .join(Content, UserSavedContent.content_id == Content.id)
        .join(SourceEntry, SourceEntry.content_id == Content.id)
        .join(FeedSource, SourceEntry.source_id == FeedSource.id)
        .outerjoin(SourceSubscription, active_subscription)
        .join(ranked_entries, ranked_entries.c.entry_id == SourceEntry.id)
        .where(UserSavedContent.user_id == current_user.id, *filters)
        .order_by(ranked_entries.c.saved_at.desc(), ranked_entries.c.content_id.desc())
        .limit(limit + 1)
    )
    page_rows = list(rows)
    has_more = len(page_rows) > limit
    page_rows = page_rows[:limit]
    title_projections = await project_titles(
        session,
        [
            TitleInput(item_id=str(content.id), text=content.title)
            for _, _, content, *_ in page_rows
        ],
        context=translation_context,
    )
    await session.commit()
    response = [
        _saved_item_response(
            saved,
            entry,
            content,
            source,
            subscription,
            translation_context,
            title_projections.get(str(content.id)),
        )
        for saved, entry, content, source, subscription in page_rows
    ]
    if not has_more or not page_rows:
        return response, None
    last_saved, _entry, content, _source, _subscription = page_rows[-1]
    return response, _encode_saved_cursor(last_saved.saved_at, content.id)


def _saved_item_response(
    _saved: UserSavedContent,
    entry: SourceEntry,
    content: Content,
    source: FeedSource,
    subscription: SourceSubscription | None,
    translation_context: TitleTranslationContext | None = None,
    projection: TranslationProjection | None = None,
) -> SavedItemResponse:
    if translation_context is None:
        translation_context = TitleTranslationContext(
            target_locale="zh-CN",
            enabled=False,
            engine=None,
        )
    translated_title, translation_locale, translation_status = translation_response_values(
        translation_context,
        projection,
    )
    return SavedItemResponse(
        content_id=str(content.id),
        ranking_kind=(getattr(entry, "raw_metadata", None) or {}).get("ranking_kind")
        if not getattr(content, "body_html", None)
        else None,
        source_id=str(source.id),
        source_name=subscription.custom_name
        if subscription and subscription.custom_name
        else source.display_name,
        source_avatar_url=source.avatar_url,
        source_kind=str(source.kind),
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
    )


def _encode_saved_cursor(saved_at: datetime, content_id: UUID) -> str:
    payload = json.dumps(
        {"at": saved_at.isoformat(), "content_id": str(content_id)}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_saved_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8"))
        cursor_at = datetime.fromisoformat(payload["at"])
        if cursor_at.tzinfo is None or cursor_at.utcoffset() is None:
            raise ValueError("saved cursor timestamp must include a timezone")
        return cursor_at.astimezone(UTC), UUID(payload["content_id"])
    except (binascii.Error, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid saved cursor."
        ) from exc


@router.get("/ranking", response_model=RankingSavedStateResponse)
async def get_ranking_saved_state(
    url: str = Query(min_length=1, max_length=8192),
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> RankingSavedStateResponse:
    content_id = await session.scalar(accessible_url_query(current_user.id, url))
    is_saved = (
        content_id is not None
        and await session.scalar(
            select(UserSavedContent.id).where(
                UserSavedContent.user_id == current_user.id,
                UserSavedContent.content_id == content_id,
            )
        )
        is not None
    )
    return RankingSavedStateResponse(
        content_id=str(content_id) if content_id else None, is_saved=is_saved
    )


@router.put("/ranking", response_model=SavedStateResponse)
async def save_ranking(
    payload: SaveRankingRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SavedStateResponse:
    try:
        content_id = await persist_ranking_item(
            session,
            user_id=current_user.id,
            request=RankingRequest(
                kind=payload.kind,
                subreddit=payload.subreddit,
                sort=payload.sort,
                time_filter=payload.time_filter,
            ),
            url=payload.url,
        )
    except ValueError as exc:
        raise HTTPException(422, "Invalid ranking source.") from exc
    return SavedStateResponse(content_id=str(content_id), is_saved=True)


@router.put("/{content_id}", response_model=SavedStateResponse)
async def save_content(
    content_id: UUID,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SavedStateResponse:
    await _assert_accessible_content(content_id, current_user, session)
    await session.execute(
        pg_insert(UserSavedContent)
        .values(user_id=current_user.id, content_id=content_id)
        .on_conflict_do_nothing(
            index_elements=[UserSavedContent.user_id, UserSavedContent.content_id]
        )
    )
    await session.commit()
    return SavedStateResponse(content_id=str(content_id), is_saved=True)


@router.delete("/{content_id}", response_model=SavedStateResponse)
async def unsave_content(
    content_id: UUID,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SavedStateResponse:
    await session.execute(
        delete(UserSavedContent).where(
            UserSavedContent.user_id == current_user.id,
            UserSavedContent.content_id == content_id,
        )
    )
    await session.commit()
    return SavedStateResponse(content_id=str(content_id), is_saved=False)
