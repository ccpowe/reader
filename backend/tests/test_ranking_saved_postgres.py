"""Ranking save integration on the guarded, migrated disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine
from test_postgres_migrations import _assert_disposable_database, _test_dsn, _test_guard_token

from app.api.feed import get_article
from app.api.saved import _query_saved_page, get_ranking_saved_state, save_content, unsave_content
from app.core.auth import AuthenticatedUser
from app.services.ranking_saved import persist_ranking_item
from app.services.ranking_snapshots import RankingRequest
from app.storage.database import build_session_factory
from app.storage.models import (
    Content,
    RankingSnapshot,
    SourceEntry,
    SourceSubscription,
    UserSavedContent,
)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_ranking_archive_concurrency_durability_and_user_isolation():
    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    first, second = (AuthenticatedUser(id=uuid4(), email=None, claims={}) for _ in range(2))
    request = RankingRequest(kind="github")
    url = f"https://github.com/reader-test/{uuid4()}"
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        for user in (first, second):
            await connection.execute("INSERT INTO profiles (id) VALUES ($1)", user.id)
            await connection.execute(
                "INSERT INTO user_translation_preferences (user_id, is_enabled) "
                "VALUES ($1, false) ON CONFLICT (user_id) DO UPDATE SET is_enabled = false",
                user.id,
            )
        async with factory() as session:
            await session.execute(
                delete(RankingSnapshot).where(RankingSnapshot.cache_key == request.cache_key)
            )
            now = datetime.now(UTC)
            session.add(
                RankingSnapshot(
                    cache_key=request.cache_key,
                    kind=request.kind,
                    parameters=request.parameters,
                    payload={
                        "title": "GitHub",
                        "subtitle": "",
                        "items": [{"rank": 1, "title": "Durable ranking repository", "url": url}],
                    },
                    fetched_at=now,
                    next_refresh_at=now,
                    is_ready=True,
                )
            )
            await session.commit()

        async def save(user):
            async with factory() as session:
                return await persist_ranking_item(
                    session, user_id=user.id, request=request, url=url
                )

        ids = await asyncio.gather(save(first), save(first), save(second))
        assert len(set(ids)) == 1
        content_id = ids[0]
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Content).where(Content.canonical_url == url)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceEntry)
                    .where(SourceEntry.content_id == content_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(UserSavedContent)
                    .where(UserSavedContent.content_id == content_id)
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceSubscription)
                    .where(SourceSubscription.user_id.in_([first.id, second.id]))
                )
                == 0
            )
            await session.execute(
                delete(RankingSnapshot).where(RankingSnapshot.cache_key == request.cache_key)
            )
            await session.commit()
        # Fresh sessions simulate reopening after the shared ranking has expired.
        async with factory() as session:
            items, cursor = await _query_saved_page(
                session, current_user=first, limit=20, cursor=None
            )
            assert cursor is None and len(items) == 1
            assert items[0].ranking_kind == "github" and items[0].external_url == url
        async with factory() as session:
            assert (
                await get_article(content_id, current_user=first, session=session)
            ).external_url == url
        async with factory() as session:
            await unsave_content(content_id, current_user=first, session=session)
        async with factory() as session:
            state = await get_ranking_saved_state(url=url, current_user=first, session=session)
            assert state.is_saved is False and state.content_id is None
        async with factory() as session:
            assert (
                await get_ranking_saved_state(url=url, current_user=second, session=session)
            ).is_saved is True
        async with factory() as session:
            with pytest.raises(HTTPException) as error:
                await get_article(content_id, current_user=first, session=session)
            assert error.value.status_code == 404
        async with factory() as session:
            assert (
                await save_content(content_id, current_user=first, session=session)
            ).is_saved is True
    finally:
        await engine.dispose()
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_reddit_saved_identity_is_reused_by_normal_ingestion():
    from app.domain.enums import ContentKind
    from app.ingestion.models import DiscoveredContent
    from app.ingestion.source_identity import canonical_reddit_identity
    from app.services.subscriptions import subscribe_to_reddit_source
    from app.workers.candidates import _get_or_create_content

    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    user_id = uuid4()
    subreddit = f"test{uuid4().hex[:12]}"
    request = RankingRequest(kind="reddit", subreddit=subreddit)
    url = f"https://www.reddit.com/r/{subreddit}/comments/abc123/post/"
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        await connection.execute("INSERT INTO profiles (id) VALUES ($1)", user_id)
        async with factory() as session:
            source, *_ = await subscribe_to_reddit_source(
                session,
                user_id=user_id,
                identity=canonical_reddit_identity(subreddit),
                folder_name=None,
                limit=20,
            )
            source_id = source.id
            now = datetime.now(UTC)
            snapshot = await session.get(RankingSnapshot, request.cache_key)
            snapshot.payload = {
                "title": subreddit,
                "subtitle": "",
                "items": [
                    {
                        "rank": 1,
                        "title": "Reddit post",
                        "url": url,
                        "native_id": "abc123",
                    }
                ],
            }
            snapshot.fetched_at = now
            snapshot.next_refresh_at = now
            snapshot.is_ready = True
            await session.commit()
        async with factory() as session:
            archived_id = await persist_ranking_item(
                session, user_id=user_id, request=request, url=url
            )
        async with factory() as session:
            content = await _get_or_create_content(
                session,
                DiscoveredContent(
                    native_id="abc123",
                    kind=ContentKind.POST,
                    title="Reddit post",
                    external_url=url,
                    published_at=None,
                ),
                source_id=source_id,
            )
            assert content.id == archived_id
            assert content.authority_source_id == source_id
            await session.commit()
        # The same URL entering from the ranking again reuses its subscribed Content.
        async with factory() as session:
            assert (
                await persist_ranking_item(session, user_id=user_id, request=request, url=url)
                == archived_id
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(UserSavedContent)
                    .where(
                        UserSavedContent.user_id == user_id,
                        UserSavedContent.content_id == archived_id,
                    )
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceSubscription)
                    .where(
                        SourceSubscription.user_id == user_id,
                    )
                )
                == 1
            )
    finally:
        await engine.dispose()
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_reddit_subscription_rolls_back_snapshot_failure_and_reuses_quota(monkeypatch):
    from app.ingestion.source_identity import canonical_reddit_identity
    from app.services import subscriptions
    from app.services.ranking_snapshots import ensure_reddit_snapshots
    from app.services.subscriptions import (
        SourceSubscriptionLimitExceeded,
        subscribe_to_reddit_source,
    )

    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    user_id = uuid4()
    subreddit = f"test{uuid4().hex[:12]}"
    identity = canonical_reddit_identity(subreddit)
    board_keys = [
        RankingRequest(
            kind="reddit", subreddit=subreddit, sort=sort, time_filter=time_filter
        ).cache_key
        for sort, time_filter in (("hot", None), ("rising", None), ("top", "week"))
    ]

    async def fail_after_snapshots(session, name):
        await ensure_reddit_snapshots(session, name)
        raise RuntimeError("snapshot setup failed")

    try:
        await _assert_disposable_database(connection, _test_guard_token())
        with monkeypatch.context() as patch:
            patch.setattr(subscriptions, "ensure_reddit_snapshots", fail_after_snapshots)
            async with factory() as session:
                with pytest.raises(RuntimeError, match="snapshot setup failed"):
                    await subscribe_to_reddit_source(
                        session, user_id=user_id, identity=identity, folder_name=None, limit=1
                    )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM feed_sources WHERE canonical_key = $1", identity.canonical_key
            )
            == 0
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM source_subscriptions WHERE user_id = $1", user_id
            )
            == 0
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM ranking_snapshots WHERE cache_key = ANY($1::text[])",
                board_keys,
            )
            == 0
        )

        async with factory() as session:
            source, subscription, _, created = await subscribe_to_reddit_source(
                session, user_id=user_id, identity=identity, folder_name=None, limit=1
            )
            assert created
        async with factory() as session:
            again, same_subscription, _, created = await subscribe_to_reddit_source(
                session, user_id=user_id, identity=identity, folder_name=None, limit=1
            )
            assert not created
            assert again.id == source.id and same_subscription.id == subscription.id
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM ranking_snapshots WHERE cache_key = ANY($1::text[])",
                board_keys,
            )
            == 3
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM source_subscriptions WHERE user_id = $1", user_id
            )
            == 1
        )
        async with factory() as session:
            with pytest.raises(SourceSubscriptionLimitExceeded):
                await subscribe_to_reddit_source(
                    session,
                    user_id=user_id,
                    identity=canonical_reddit_identity(f"test{uuid4().hex[:12]}"),
                    folder_name=None,
                    limit=1,
                )
    finally:
        await engine.dispose()
        await connection.close()
