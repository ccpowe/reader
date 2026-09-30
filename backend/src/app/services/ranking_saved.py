"""Persist explicitly saved ranking items independently of the shared snapshot cache."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import CacheStatus, MediaType, SourceVisibility
from app.ingestion.source_identity import canonical_reddit_identity
from app.services.ranking_snapshots import (
    RankingRequest,
    get_snapshot,
    ranking_item_payload,
    user_has_active_reddit_subscription,
)
from app.services.rankings import RankingItem
from app.storage.models import (
    Content,
    ContentMedia,
    FeedSource,
    SourceEntry,
    SourceSubscription,
    UserSavedContent,
)
from app.storage.profiles import ensure_profile


def accessible_url_query(user_id: UUID, url: str):
    """Never reveal another account's private or unsubscribed content identity."""
    subscribed = exists(
        select(SourceSubscription.id).where(
            SourceSubscription.source_id == Content.authority_source_id,
            SourceSubscription.user_id == user_id,
            SourceSubscription.is_enabled.is_(True),
        )
    )
    saved = exists(
        select(UserSavedContent.id).where(
            UserSavedContent.content_id == Content.id,
            UserSavedContent.user_id == user_id,
        )
    )
    return (
        select(Content.id)
        .where(Content.canonical_url == url, or_(subscribed, saved))
        .order_by(saved.desc(), Content.id)
        .limit(1)
    )


async def persist_ranking_item(
    session: AsyncSession, *, user_id: UUID, request: RankingRequest, url: str
) -> UUID:
    from app.translation.lifecycle import lock_shared_lifecycle

    if request.kind == "reddit":
        identity = canonical_reddit_identity(request.subreddit)
        subreddit = identity.canonical_key.removeprefix("reddit:")
        request = RankingRequest(
            kind="reddit", subreddit=subreddit, sort=request.sort, time_filter=request.time_filter
        )
        if not await user_has_active_reddit_subscription(session, user_id, subreddit):
            raise HTTPException(403, "Subscribe to this subreddit before saving its ranking.")
    data = await get_snapshot(session, request)
    item = next((item for item in data.items if item.url == url), None) if data else None
    if item is None:
        raise HTTPException(
            404, "Ranking item no longer available. Refresh the ranking and try again."
        )
    # get_snapshot commits its last-access timestamp; lock the actual archive
    # transaction afterwards so concurrent native-ID/URL conflicts stay atomic.
    await lock_shared_lifecycle(session)
    content_id = await session.scalar(accessible_url_query(user_id, url))
    if content_id is None:
        # Reddit shares the ingestion authority, so later admissions upgrade the
        # same Content. Other boards use an archive source that is never subscribed.
        source_key = (
            identity.canonical_key
            if request.kind == "reddit"
            else f"ranking-archive:{request.kind}"
        )
        if request.kind != "reddit":
            await session.execute(
                pg_insert(FeedSource)
                .values(
                    canonical_key=source_key,
                    canonical_url=(
                        "https://news.ycombinator.com/"
                        if request.kind == "hacker_news"
                        else "https://www.techtwitter.com/"
                        if request.kind == "techtwitter"
                        else "https://github.com/trending"
                    ),
                    kind="hackernews" if request.kind == "hacker_news" else "web",
                    display_name=data.title,
                    visibility=SourceVisibility.SHARED,
                    status="paused",
                    config={"ranking_archive": True},
                )
                .on_conflict_do_nothing(index_elements=[FeedSource.canonical_key])
            )
        source_id = await session.scalar(
            select(FeedSource.id).where(FeedSource.canonical_key == source_key)
        )
        if source_id is None:
            raise HTTPException(404, "Ranking source not found.")
        native_id = item.native_id or url
        existing_native_id = (
            await session.scalar(
                select(SourceEntry.content_id).where(
                    SourceEntry.source_id == source_id,
                    SourceEntry.native_id == native_id,
                )
            )
            if request.kind == "techtwitter"
            else None
        )
        if existing_native_id is not None:
            content_id = existing_native_id
        else:
            url_hash = hashlib.sha256(url.encode()).hexdigest()
            await session.execute(
                pg_insert(Content)
                .values(
                    authority_source_id=source_id,
                    kind="post" if request.kind in ("reddit", "techtwitter") else "article",
                    canonical_url=url,
                    url_hash=url_hash,
                    title=item.title[:1000],
                    author_name=(item.author or "")[:300] or None,
                    published_at=item.published_at,
                    excerpt=item.description,
                    body_text=item.body_text if request.kind == "techtwitter" else None,
                    extraction_status="not_needed",
                )
                .on_conflict_do_nothing(
                    index_elements=[Content.authority_source_id, Content.url_hash],
                    index_where=Content.url_hash.is_not(None),
                )
            )
            content_id = await session.scalar(
                select(Content.id).where(
                    Content.authority_source_id == source_id, Content.url_hash == url_hash
                )
            )
            now = datetime.now(UTC)
            await session.execute(
                pg_insert(SourceEntry)
                .values(
                    source_id=source_id,
                    content_id=content_id,
                    native_id=native_id,
                    external_url=url,
                    title=item.title[:1000],
                    author_name=(item.author or "")[:300] or None,
                    published_at=item.published_at,
                    feed_sort_at=item.published_at or now,
                    raw_metadata=(
                        {"ranking_kind": request.kind, "ranking_item": ranking_item_payload(item)}
                        if request.kind == "techtwitter"
                        else {"ranking_kind": request.kind}
                    ),
                )
                .on_conflict_do_nothing(
                    index_elements=[SourceEntry.source_id, SourceEntry.native_id]
                )
            )
            # Native identity can already exist under an older canonical URL.
            # Always save the entry's actual Content after concurrent upserts.
            content_id = await session.scalar(
                select(SourceEntry.content_id).where(
                    SourceEntry.source_id == source_id,
                    SourceEntry.native_id == native_id,
                )
            )
        if content_id is None:
            raise RuntimeError("Ranking archive entry was not persisted.")
    if request.kind == "techtwitter":
        await _persist_techtwitter_details(session, content_id=content_id, item=item, url=url)
    await ensure_profile(session, user_id)
    await session.execute(
        pg_insert(UserSavedContent)
        .values(user_id=user_id, content_id=content_id)
        .on_conflict_do_nothing(
            index_elements=[UserSavedContent.user_id, UserSavedContent.content_id]
        )
    )
    await session.commit()
    return content_id


async def _persist_techtwitter_details(
    session: AsyncSession, *, content_id: UUID, item: RankingItem, url: str
) -> None:
    """Restore only archive-owned tweets; subscribed X entries remain untouched."""
    archive_source_id = await session.scalar(
        select(FeedSource.id)
        .join(Content, Content.authority_source_id == FeedSource.id)
        .where(
            Content.id == content_id,
            FeedSource.canonical_key == "ranking-archive:techtwitter",
        )
    )
    if archive_source_id is None:
        return
    entry = await session.scalar(
        select(SourceEntry)
        .where(
            SourceEntry.source_id == archive_source_id,
            SourceEntry.content_id == content_id,
            SourceEntry.native_id == (item.native_id or url),
        )
        .with_for_update()
    )
    if entry is None:
        raise RuntimeError("Ranking archive entry was not persisted.")
    if "ranking_item" not in entry.raw_metadata:
        entry.raw_metadata = {
            **entry.raw_metadata,
            "ranking_kind": "techtwitter",
            "ranking_item": ranking_item_payload(item),
        }
    else:
        stored = entry.raw_metadata["ranking_item"]
        if stored.get("url") != url:
            entry.raw_metadata = {
                **entry.raw_metadata,
                "ranking_item": {**stored, "url": url},
            }
    if entry.external_url != url:
        entry.external_url = url
        await session.execute(
            update(Content)
            .where(Content.id == content_id)
            .values(canonical_url=url, url_hash=hashlib.sha256(url.encode()).hexdigest())
        )
    if item.body_text:
        await session.execute(
            update(Content)
            .where(
                Content.id == content_id,
                or_(Content.body_text.is_(None), Content.body_text == ""),
            )
            .values(body_text=item.body_text)
        )
    media_urls = list(dict.fromkeys(item.image_urls))
    if item.video_thumbnail_url and not item.video_url:
        media_urls = list(dict.fromkeys((*media_urls, item.video_thumbnail_url)))
    for index, image_url in enumerate(media_urls):
        await session.execute(
            pg_insert(ContentMedia)
            .values(
                content_id=content_id,
                media_type=MediaType.IMAGE,
                original_url=image_url,
                cache_status=CacheStatus.EXTERNAL,
                sort_order=index,
                is_active=True,
            )
            .on_conflict_do_nothing(
                index_elements=[ContentMedia.content_id, ContentMedia.original_url]
            )
        )
    if item.video_url:
        await session.execute(
            pg_insert(ContentMedia)
            .values(
                content_id=content_id,
                media_type=MediaType.VIDEO,
                original_url=item.video_url,
                poster_url=item.video_thumbnail_url,
                cache_status=CacheStatus.EXTERNAL,
                sort_order=len(media_urls),
                is_active=True,
            )
            .on_conflict_do_nothing(
                index_elements=[ContentMedia.content_id, ContentMedia.original_url]
            )
        )
