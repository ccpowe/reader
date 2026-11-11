"""Reading routes: /feed, /feed/summary, /articles/{id} and /saved."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import AwareDatetime, BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from reader.accounts.auth import CurrentUser, current_user
from reader.core.db import get_session
from reader.core.errors import error_responses
from reader.core.pagination import Page, decode_cursor, encode_cursor
from reader.core.server_access import document_server_token
from reader.core.settings import Settings, get_settings
from reader.ingestion.x_relations import XPreview, short_text, x_preview
from reader.models import Article, Source, Subscription
from reader.reading import service
from reader.reading.service import FeedScope
from reader.subscriptions.api import source_icon_url
from reader.translation.titles import TitleTranslation, title_translations

feed_router = APIRouter(
    prefix="/feed", tags=["reading"], dependencies=[Depends(document_server_token)]
)
articles_router = APIRouter(
    prefix="/articles", tags=["reading"], dependencies=[Depends(document_server_token)]
)
saved_router = APIRouter(
    prefix="/saved", tags=["reading"], dependencies=[Depends(document_server_token)]
)

_SUMMARY_LIMIT = 280
_ARTICLE_NOT_FOUND = (404, "article_not_found")
_SUBSCRIPTION_NOT_FOUND = (404, "subscription_not_found")
_BAD_CURSOR = (422, "invalid_cursor")


class SourceRef(BaseModel):
    """Where an article came from; empty for items saved from a board."""

    source_id: str
    # Empty when the user no longer follows the source (e.g. a saved article).
    subscription_id: str | None
    type: str
    name: str | None
    icon_url: str | None


class TitleTranslationOut(BaseModel):
    text: str
    engine_fingerprint: str


class FeedItem(BaseModel):
    id: str
    source: SourceRef | None
    kind: str
    title: str
    url: str
    author: str | None
    published_at: datetime | None
    first_seen_at: datetime
    # Plain text, shortened.
    summary: str | None
    image_url: str | None
    # Badge sequence; empty for first-scan baseline items and board items.
    sequence: int | None
    is_saved: bool
    # The title in your target language, once translated with your current engine.
    # Missing ones can be asked for with POST /translations (purpose "title").
    title_translation: TitleTranslationOut | None = None
    x: XPreview | None = None


class SavedItem(FeedItem):
    saved_at: datetime


class ArticleDetail(FeedItem):
    body_html: str | None
    media: list[dict[str, Any]]
    # For an X thread root: the later posts of the thread Reader has, oldest first.
    thread: list[FeedItem] = []


class SummaryEntry(BaseModel):
    subscription_id: str
    source_id: str
    type: str
    name: str | None
    group: str | None
    count: int


class FeedSummary(BaseModel):
    since: datetime
    until: datetime
    total: int
    # Only subscriptions with at least one item in the window, busiest first.
    subscriptions: list[SummaryEntry]


def _source_ref(
    source: Source | None, subscription: Subscription | None, request: Request, settings: Settings
) -> SourceRef | None:
    if source is None:
        return None
    return SourceRef(
        source_id=str(source.id),
        subscription_id=str(subscription.id) if subscription else None,
        type=source.kind,
        name=(subscription.custom_name if subscription else None) or source.display_name,
        icon_url=source_icon_url(source, request, settings),
    )


def _first_image(article: Article) -> str | None:
    for media in article.media or []:
        if media.get("type") == "image" and media.get("url"):
            return media["url"]
    return None


def _item_fields(
    article: Article,
    source: Source | None,
    subscription: Subscription | None,
    *,
    is_saved: bool,
    thread_count: int,
    request: Request,
    settings: Settings,
    translations: dict[str, TitleTranslation],
    full: bool = False,
) -> dict[str, Any]:
    summary = article.summary
    if summary and not full:
        summary = short_text(summary, _SUMMARY_LIMIT)
    is_x = source is not None and source.kind == "x"
    return {
        "id": str(article.id),
        "source": _source_ref(source, subscription, request, settings),
        "kind": article.kind,
        "title": article.title,
        "url": article.url,
        "author": article.author,
        "published_at": article.published_at,
        "first_seen_at": article.first_seen_at,
        "summary": summary,
        "image_url": _first_image(article),
        "sequence": article.sequence,
        "is_saved": is_saved,
        "title_translation": TitleTranslationOut(**vars(found))
        if (found := translations.get(article.title))
        else None,
        "x": x_preview(article.extra or {}, thread_count, quote_limit=None if full else 80)
        if is_x
        else None,
    }


def _after(cursor: str | None) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    values = decode_cursor(cursor, at=datetime, id=UUID)
    return values["at"], values["id"]


@feed_router.get(
    "",
    response_model=Page[FeedItem],
    responses=error_responses(_SUBSCRIPTION_NOT_FOUND, _BAD_CURSOR),
)
async def get_feed(
    request: Request,
    subscription: UUID | None = Query(default=None, description="Only this subscription."),
    group: str | None = Query(default=None, max_length=120, description="Only this group."),
    since: AwareDatetime | None = Query(
        default=None, description="Updates seen from this time; leaves out first-scan items."
    ),
    until: AwareDatetime | None = Query(default=None, description="Seen before this time."),
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=30, ge=1, le=100),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    """Newest first by when Reader first saw each article.

    Without `subscription` or `group`: the enabled subscriptions shown on home.
    An X self-reply thread appears once, as its first post, with `x.thread_count`.
    """
    scope = FeedScope(subscription_id=subscription, group=group, since=since, until=until)
    rows, has_more = await service.feed_page(db, user.id, scope, after=_after(cursor), limit=limit)
    translations = await title_translations(
        db, user.id, settings, [row.article.title for row in rows]
    )
    items = [
        FeedItem(
            **_item_fields(
                row.article,
                row.source,
                row.subscription,
                is_saved=row.is_saved,
                thread_count=row.thread_count,
                request=request,
                settings=settings,
                translations=translations,
            )
        )
        for row in rows
    ]
    last = rows[-1].article if rows and has_more else None
    return Page[FeedItem](
        items=items,
        next_cursor=encode_cursor(at=last.first_seen_at, id=last.id) if last else None,
    )


@feed_router.get("/summary", response_model=FeedSummary, responses=error_responses())
async def get_feed_summary(
    since: AwareDatetime = Query(description="Count updates seen from this time."),
    until: AwareDatetime | None = Query(default=None, description="Up to this time; default now."),
    group: str | None = Query(default=None, max_length=120),
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """How many feed items each subscription has in a time window (same rules as /feed)."""
    until = until or datetime.now(UTC)
    rows = await service.feed_summary(db, user.id, FeedScope(group=group, since=since, until=until))
    return FeedSummary(
        since=since,
        until=until,
        total=sum(row.count for row in rows),
        subscriptions=[
            SummaryEntry(
                subscription_id=str(row.subscription.id),
                source_id=str(row.source.id),
                type=row.source.kind,
                name=row.subscription.custom_name or row.source.display_name,
                group=row.subscription.group_name,
                count=row.count,
            )
            for row in rows
        ],
    )


@articles_router.get(
    "/{article_id}", response_model=ArticleDetail, responses=error_responses(_ARTICLE_NOT_FOUND)
)
async def get_article(
    article_id: UUID,
    request: Request,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    """One article in full. Opens when you follow its source or have saved it."""
    view = await service.get_article(db, user.id, article_id)
    translations = await title_translations(
        db, user.id, settings, [view.article.title, *(post.title for post in view.thread)]
    )

    def fields(article: Article, *, thread_count: int = 1) -> dict[str, Any]:
        return _item_fields(
            article,
            view.source,
            view.subscription,
            is_saved=view.is_saved
            if article is view.article
            else article.id in view.saved_in_thread,
            thread_count=thread_count,
            request=request,
            settings=settings,
            translations=translations,
            full=True,
        )

    return ArticleDetail(
        **fields(view.article, thread_count=1 + len(view.thread)),
        body_html=view.article.body_html,
        media=view.article.media or [],
        thread=[FeedItem(**fields(post)) for post in view.thread],
    )


@saved_router.get("", response_model=Page[SavedItem], responses=error_responses(_BAD_CURSOR))
async def list_saved(
    request: Request,
    q: str | None = Query(default=None, max_length=200, description="Search title and summary."),
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=30, ge=1, le=100),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    """Saved articles, most recently saved first."""
    rows, has_more = await service.saved_page(
        db, user.id, q=(q or "").strip() or None, after=_after(cursor), limit=limit
    )
    translations = await title_translations(
        db, user.id, settings, [row.article.title for row in rows]
    )
    items = [
        SavedItem(
            **_item_fields(
                row.article,
                row.source,
                row.subscription,
                is_saved=True,
                thread_count=1,
                request=request,
                settings=settings,
                translations=translations,
            ),
            saved_at=row.saved_at,
        )
        for row in rows
    ]
    last = rows[-1] if rows and has_more else None
    return Page[SavedItem](
        items=items,
        next_cursor=encode_cursor(at=last.saved_at, id=last.article.id) if last else None,
    )


@saved_router.put("/{article_id}", status_code=204, responses=error_responses(_ARTICLE_NOT_FOUND))
async def save(
    article_id: UUID,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Save an article from a source you follow. Saving again changes nothing."""
    await service.save_article(db, user.id, article_id)
    return Response(status_code=204)


@saved_router.delete("/{article_id}", status_code=204, responses=error_responses())
async def unsave(
    article_id: UUID,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Remove from saved. Not saved is not an error."""
    await service.unsave_article(db, user.id, article_id)
    return Response(status_code=204)
