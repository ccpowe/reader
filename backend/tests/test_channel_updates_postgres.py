"""Durable channel update behavior on guarded PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select, text
from test_candidate_recovery_postgres import _database

from app.api.feed import _channel_update_snapshot
from app.api.sources import (
    MarkSourceViewedRequest,
    UpdateSourceSubscriptionRequest,
    list_sources,
    mark_source_viewed,
    update_source_subscription,
)
from app.core.auth import AuthenticatedUser
from app.domain.enums import ContentKind
from app.ingestion.candidate_queue import upsert_candidates
from app.ingestion.models import DiscoveredContent
from app.services.ranking_snapshots import (
    RankingData,
    RankingItem,
    RankingRequest,
    _enqueue_reddit_hot_admissions,
)
from app.storage.models import (
    FeedSource,
    IngestionCandidate,
    SourceEntry,
    SourceSubscription,
    SourceSyncState,
)
from app.workers.candidates import process_candidates
from app.workers.content_retention import purge_retained_contents


def _item(native_id: str, *, url: str | None = None, age_days: int = 0) -> DiscoveredContent:
    return DiscoveredContent(
        native_id=native_id,
        kind=ContentKind.ARTICLE,
        title=f"Article {native_id}",
        external_url=url or f"https://example.com/{native_id}",
        published_at=datetime.now(UTC) - timedelta(days=age_days),
    )


async def _subscribe(factory, source_id, user_id, *, folder_name="Saved"):
    async with factory() as session:
        subscription = SourceSubscription(
            user_id=user_id,
            source_id=source_id,
            folder_name=folder_name,
        )
        session.add_all(
            [
                subscription,
                SourceSyncState(
                    source_id=source_id,
                    initial_sync_completed=True,
                ),
            ]
        )
        await session.commit()
        return subscription.id


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_worker_updates_are_snapshot_safe_scoped_and_content_deduplicated():
    async with _database() as (factory, source_id):
        user_id, other_user_id = uuid4(), uuid4()
        async with factory() as session:
            await session.execute(
                # Profiles are account-owned but this test does not need credentials.
                # Use the ORM's PostgreSQL connection so FK visibility shares a commit.
                text("INSERT INTO profiles (id) VALUES (:first), (:second)"),
                {"first": user_id, "second": other_user_id},
            )
            await session.commit()
        subscription_id = await _subscribe(factory, source_id, user_id)
        async with factory() as session:
            session.add(
                SourceSubscription(
                    user_id=other_user_id,
                    source_id=source_id,
                    folder_name="Other",
                )
            )
            await session.commit()

        # The initial candidate remains baseline even though the source state
        # already says its scan completed before Candidate processing catches up.
        async with factory() as session:
            await upsert_candidates(
                session,
                source_id=source_id,
                items=(_item("initial-baseline"),),
                observed_at=datetime.now(UTC),
                max_changes=None,
                counts_as_update=False,
            )
            await session.commit()
        statements: list[tuple[str, object]] = []
        engine = factory.kw["bind"]

        def capture_statement(_conn, _cursor, statement, parameters, _context, _many):
            statements.append((statement, parameters))

        event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
        try:
            await process_candidates(factory, limit=1)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)
        source_lock_index = next(
            index
            for index, (_, parameters) in enumerate(statements)
            if f"source-updates:{source_id}" in str(parameters)
        )
        source_row_index = next(
            index
            for index, (statement, _) in enumerate(statements[source_lock_index + 1 :], 1)
            if "FROM feed_sources" in statement and "FOR UPDATE" in statement
        ) + source_lock_index
        candidate_row_index = next(
            index
            for index, (statement, _) in enumerate(statements[source_row_index + 1 :], 1)
            if "FROM ingestion_candidates" in statement and "FOR UPDATE" in statement
        ) + source_row_index
        assert source_lock_index < source_row_index < candidate_row_index
        async with factory() as session:
            user = AuthenticatedUser(id=user_id, email=None, claims={})
            assert (await list_sources(current_user=user, session=session))[0].new_count == 0

        async with factory() as session:
            await upsert_candidates(
                session,
                source_id=source_id,
                items=(_item("one"), _item("two")),
                observed_at=datetime.now(UTC),
                max_changes=None,
                counts_as_update=True,
            )
            await session.commit()
        await asyncio.gather(
            process_candidates(factory, limit=1),
            process_candidates(factory, limit=1),
        )

        async with factory() as session:
            entries = list(
                await session.scalars(
                    select(SourceEntry)
                    .where(SourceEntry.source_id == source_id)
                    .order_by(SourceEntry.update_sequence)
                )
            )
            source = await session.get(FeedSource, source_id)
            update_entries = [entry for entry in entries if entry.update_sequence is not None]
            assert len(entries) == 3
            assert len(update_entries) == 2
            assert len({entry.update_sequence for entry in update_entries}) == 2
            assert source.latest_update_sequence == update_entries[-1].update_sequence

            user = AuthenticatedUser(id=user_id, email=None, claims={})
            other = AuthenticatedUser(id=other_user_id, email=None, claims={})
            assert (await list_sources(current_user=user, session=session))[0].new_count == 2
            assert (await list_sources(current_user=other, session=session))[0].new_count == 2
            token = await _channel_update_snapshot(session, user_id=user_id, source_id=source_id)
            assert token is not None

        duplicate_url = "https://example.com/shared-content"
        async with factory() as session:
            await upsert_candidates(
                session,
                source_id=source_id,
                items=(
                    _item("same-a", url=duplicate_url, age_days=30),
                    _item("same-b", url=duplicate_url, age_days=30),
                ),
                observed_at=datetime.now(UTC),
                max_changes=None,
                counts_as_update=True,
            )
            await session.commit()
        await process_candidates(factory, limit=10)

        async with factory() as session:
            same_entries = list(
                await session.scalars(
                    select(SourceEntry).where(
                        SourceEntry.source_id == source_id,
                        SourceEntry.native_id.in_(("same-a", "same-b")),
                    )
                )
            )
            assert len(same_entries) == 2
            assert sum(entry.update_sequence is not None for entry in same_entries) == 1
            user = AuthenticatedUser(id=user_id, email=None, claims={})
            response = await mark_source_viewed(
                str(subscription_id),
                MarkSourceViewedRequest(channel_update_token=token),
                current_user=user,
                session=session,
            )
            assert response.new_count == 1

            # The other account has its own cursor and remains unaffected.
            assert (await list_sources(current_user=other, session=session))[0].new_count == 3

            updated = await update_source_subscription(
                str(subscription_id),
                UpdateSourceSubscriptionRequest(include_in_home=False),
                current_user=user,
                session=session,
            )
            assert updated.include_in_home is False
            assert updated.folder_name == "Saved"

            latest_token = await _channel_update_snapshot(
                session, user_id=user_id, source_id=source_id
            )
            assert latest_token is not None
            removed = await purge_retained_contents(session, keep_per_source=3)
            assert removed == 1
            await session.commit()

            # Retention may delete the entry carrying the high-water sequence;
            # the durable source high-water still makes its token confirmable.
            response = await mark_source_viewed(
                str(subscription_id),
                MarkSourceViewedRequest(channel_update_token=latest_token),
                current_user=user,
                session=session,
            )
            assert response.new_count == 0

        async with factory() as session:
            await session.execute(
                delete(SourceSubscription).where(
                    SourceSubscription.user_id.in_((user_id, other_user_id))
                )
            )
            await session.commit()
            await session.execute(
                text("DELETE FROM profiles WHERE id IN (:first, :second)"),
                {"first": user_id, "second": other_user_id},
            )
            await session.commit()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_reddit_first_snapshot_stays_baseline_while_candidates_are_pending():
    async with _database() as (factory, source_id):
        user_id = uuid4()
        async with factory() as session:
            await session.execute(
                text("INSERT INTO profiles (id) VALUES (:user_id)"),
                {"user_id": user_id},
            )
            source = await session.get(FeedSource, source_id)
            source.kind = "reddit"
            source.canonical_key = "reddit:channelupdatetest"
            source.canonical_url = "https://www.reddit.com/r/channelupdatetest/"
            session.add_all(
                [
                    SourceSubscription(user_id=user_id, source_id=source_id),
                    SourceSyncState(
                        source_id=source_id,
                        provider_mode="reddit_snapshot",
                        initial_sync_completed=True,
                    ),
                ]
            )
            await session.commit()

        first = RankingItem(
            rank=1,
            title="First",
            url="https://reddit.com/r/channelupdatetest/comments/first/",
            native_id="t3_first",
        )
        second = RankingItem(
            rank=2,
            title="Second",
            url="https://reddit.com/r/channelupdatetest/comments/second/",
            native_id="t3_second",
        )
        request = RankingRequest(kind="reddit", subreddit="channelupdatetest", sort="hot")
        now = datetime.now(UTC)
        async with factory() as session:
            await _enqueue_reddit_hot_admissions(
                session,
                request,
                RankingData(
                    kind="reddit",
                    title="test",
                    subtitle="hot",
                    items=[first],
                    fetched_at=now,
                ),
                now,
            )
            await session.commit()
        async with factory() as session:
            await _enqueue_reddit_hot_admissions(
                session,
                request,
                RankingData(
                    kind="reddit",
                    title="test",
                    subtitle="hot",
                    items=[first, second],
                    fetched_at=now + timedelta(minutes=1),
                ),
                now + timedelta(minutes=1),
            )
            await session.commit()
            candidates = {
                candidate.native_id: candidate.counts_as_update
                for candidate in await session.scalars(
                    select(IngestionCandidate).where(IngestionCandidate.source_id == source_id)
                )
            }
            assert candidates == {"t3_first": False, "t3_second": True}

        async with factory() as session:
            await session.execute(
                delete(SourceSubscription).where(SourceSubscription.user_id == user_id)
            )
            await session.commit()
            await session.execute(
                text("DELETE FROM profiles WHERE id = :user_id"),
                {"user_id": user_id},
            )
            await session.commit()
