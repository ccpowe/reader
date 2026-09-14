"""Shared-source retention and collection protection on guarded PostgreSQL."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine
from test_postgres_migrations import _assert_disposable_database, _test_dsn, _test_guard_token

from app.storage.database import build_session_factory
from app.storage.models import (
    Content,
    FeedSource,
    IngestionCandidate,
    SourceEntry,
    SourceSubscription,
    SourceSyncState,
    UserSavedContent,
)
from app.workers.content_retention import purge_retained_contents


@asynccontextmanager
async def _database():
    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    await _assert_disposable_database(connection, _test_guard_token())
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    users = [uuid4(), uuid4()]
    source_id = uuid4()
    try:
        for user in users:
            await connection.execute("INSERT INTO profiles (id) VALUES ($1)", user)
        async with factory() as session:
            session.add(
                FeedSource(
                    id=source_id,
                    kind="rss",
                    canonical_key=f"retention:{source_id}",
                    canonical_url="https://example.com/feed",
                    display_name="Retention",
                )
            )
            await session.commit()
        yield factory, source_id, users
    finally:
        async with factory() as session:
            await session.execute(delete(FeedSource).where(FeedSource.id == source_id))
            await session.commit()
        for user in users:
            await connection.execute("DELETE FROM profiles WHERE id = $1", user)
        await connection.close()
        await engine.dispose()


async def _add_contents(session, source_id, count):
    now = datetime.now(UTC)
    contents = [
        Content(
            authority_source_id=source_id,
            kind="article",
            title=f"Article {index}",
            # Deliberately contradict the feed time: retention follows the feed.
            published_at=now - timedelta(days=index),
        )
        for index in range(count)
    ]
    session.add_all(contents)
    await session.flush()
    for index, content in enumerate(contents):
        session.add(
            SourceEntry(
                id=UUID(int=index + 1),
                source_id=source_id,
                content_id=content.id,
                native_id=str(index),
                title=content.title,
                external_url=f"https://example.com/{index}",
                feed_sort_at=now + timedelta(seconds=index // 2),
            )
        )
        session.add(
            IngestionCandidate(
                source_id=source_id,
                native_id=str(index),
                content_id=content.id,
                payload={"body": "old payload"},
                payload_hash="a" * 64,
                observed_at=now,
                suggested_feed_sort_at=now,
                status="completed",
            )
        )
    await session.flush()
    return [content.id for content in contents], now


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_default_500_quota_exempts_all_users_saves_and_deduplicates_entries():
    async with _database() as (factory, source_id, users):
        async with factory() as session:
            ids, now = await _add_contents(session, source_id, 505)
            session.add(SourceSubscription(user_id=users[0], source_id=source_id, is_enabled=False))
            session.add_all(
                [
                    UserSavedContent(user_id=user, content_id=ids[index])
                    for index, user in enumerate(users)
                ]
            )
            # An additional entry for a retained item must not consume a slot.
            session.add(
                SourceEntry(
                    source_id=source_id,
                    content_id=ids[-1],
                    native_id="duplicate",
                    title="Duplicate",
                    external_url="https://example.com/duplicate",
                    feed_sort_at=now + timedelta(days=1),
                )
            )
            session.add(
                SourceSyncState(
                    source_id=source_id,
                    initial_sync_completed=True,
                    committed_checkpoint={"boundary": "keep-me"},
                )
            )
            await session.commit()
        async with factory() as session:
            assert await purge_retained_contents(session) == 3
            # Caller owns atomic commit; rollback restores content and payloads.
            await session.rollback()
            assert await session.get(Content, ids[2]) is not None
            assert await purge_retained_contents(session) == 3
            await session.commit()
        async with factory() as session:
            remaining = set(
                await session.scalars(
                    select(Content.id).where(Content.authority_source_id == source_id)
                )
            )
            assert remaining == set(ids[:2] + ids[5:])
            candidates = set(
                await session.scalars(
                    select(IngestionCandidate.native_id).where(
                        IngestionCandidate.source_id == source_id
                    )
                )
            )
            assert candidates == {str(index) for index in [0, 1, *range(5, 505)]}
            state = await session.get(SourceSyncState, source_id)
            assert state.initial_sync_completed
            assert state.committed_checkpoint == {"boundary": "keep-me"}
            assert await purge_retained_contents(session) == 0


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_unsubscribed_source_keeps_only_saved_items_and_defers_active_ingestion():
    async with _database() as (factory, source_id, users):
        async with factory() as session:
            ids, _ = await _add_contents(session, source_id, 4)
            session.add(UserSavedContent(user_id=users[1], content_id=ids[0]))
            candidate = await session.scalar(
                select(IngestionCandidate).where(
                    IngestionCandidate.source_id == source_id, IngestionCandidate.native_id == "1"
                )
            )
            candidate.status = "processing"
            await session.commit()
        async with factory() as session:
            assert await purge_retained_contents(session) == 2
            await session.commit()
            assert await session.get(Content, ids[0]) is not None
            assert await session.get(Content, ids[1]) is not None
            candidate = await session.scalar(
                select(IngestionCandidate).where(
                    IngestionCandidate.source_id == source_id, IngestionCandidate.native_id == "1"
                )
            )
            candidate.status = "completed"
            await session.commit()
            assert await purge_retained_contents(session) == 1
            await session.commit()
            assert await session.get(FeedSource, source_id) is not None


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_in_progress_save_is_skipped_then_preserved_after_commit():
    async with _database() as (factory, source_id, users):
        async with factory() as session:
            ids, _ = await _add_contents(session, source_id, 1)
            await session.commit()
        async with factory() as saving, factory() as cleaning:
            # The FK check takes KEY SHARE on the parent content until commit.
            saving.add(UserSavedContent(user_id=users[0], content_id=ids[0]))
            await saving.flush()
            assert await purge_retained_contents(cleaning) == 0
            await cleaning.commit()
            await saving.commit()
            assert await purge_retained_contents(cleaning) == 0
            assert await cleaning.get(Content, ids[0]) is not None
