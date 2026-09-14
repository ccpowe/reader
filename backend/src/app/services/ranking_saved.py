"""Persist explicitly saved ranking items independently of the shared snapshot cache."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import SourceVisibility
from app.ingestion.source_identity import canonical_reddit_identity
from app.services.ranking_snapshots import (
    RankingRequest,
    get_snapshot,
    user_has_active_reddit_subscription,
)
from app.storage.models import (
    Content,
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

    await lock_shared_lifecycle(session)
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
                    canonical_url="https://news.ycombinator.com/"
                    if request.kind == "hacker_news"
                    else "https://github.com/trending",
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
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        await session.execute(
            pg_insert(Content)
            .values(
                authority_source_id=source_id,
                kind="post" if request.kind == "reddit" else "article",
                canonical_url=url,
                url_hash=url_hash,
                title=item.title[:1000],
                author_name=(item.author or "")[:300] or None,
                published_at=item.published_at,
                excerpt=item.description,
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
                native_id=item.native_id or url,
                external_url=url,
                title=item.title[:1000],
                author_name=(item.author or "")[:300] or None,
                published_at=item.published_at,
                feed_sort_at=item.published_at or now,
                raw_metadata={"ranking_kind": request.kind},
            )
            .on_conflict_do_nothing(index_elements=[SourceEntry.source_id, SourceEntry.native_id])
        )
        # Native identity can already exist under an older canonical URL.
        # Always save the entry's actual Content after concurrent upserts.
        content_id = await session.scalar(
            select(SourceEntry.content_id).where(
                SourceEntry.source_id == source_id,
                SourceEntry.native_id == (item.native_id or url),
            )
        )
        if content_id is None:
            raise RuntimeError("Ranking archive entry was not persisted.")
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
