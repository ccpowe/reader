"""Orphan collection preserves subscriptions across real PostgreSQL transactions."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from test_candidate_recovery_postgres import _database as _orphan_database
from test_content_retention import _add_contents, _database

from app.ingestion.source_identity import SourceIdentity
from app.services.subscriptions import subscribe_to_source
from app.storage.models import FeedSource, SourceSubscription, UserSavedContent
from app.workers.cleanup import purge_orphan_sources

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def _subscribe(session, source, user_id):
    return await subscribe_to_source(
        session,
        user_id=user_id,
        identity=SourceIdentity(source.kind, source.canonical_url, source.canonical_key),
        display_name=source.display_name,
        folder_name=None,
    )


async def test_cleanup_skips_uncommitted_subscription_and_removes_other_orphan(monkeypatch):
    async with _database() as (factory, source_id, users), _orphan_database() as (_, orphan_id):
        async with factory() as subscribing, factory() as cleaning:
            source = await subscribing.get(FeedSource, source_id)
            ready, release = asyncio.Event(), asyncio.Event()
            commit = subscribing.commit

            async def hold_commit():
                ready.set()
                await release.wait()
                await commit()

            monkeypatch.setattr(subscribing, "commit", hold_commit)
            task = asyncio.create_task(_subscribe(subscribing, source, users[0]))
            try:
                await asyncio.wait_for(ready.wait(), 5)
                # Subscription owns the source row lock; cleanup must not wait
                # on it or stop collecting unrelated empty sources.
                await asyncio.wait_for(purge_orphan_sources(cleaning), 5)
                assert await cleaning.get(FeedSource, orphan_id) is None
                assert await cleaning.get(FeedSource, source_id) is not None
            finally:
                release.set()
                result = await asyncio.wait_for(task, 5)
            subscription_id = result[1].id
        async with factory() as session:
            await purge_orphan_sources(session)
            subscription = await session.get(SourceSubscription, subscription_id)
            assert subscription is not None and subscription.source_id == source_id
            assert await session.get(FeedSource, source_id) is not None


async def test_cleanup_rechecks_subscription_committed_after_selection_snapshot():
    async with _database() as (factory, source_id, users):
        async with factory() as gate, factory() as cleaning, factory() as subscribing:
            key = uuid4().int % (2**63)
            gate_pid = await gate.scalar(text("SELECT pg_backend_pid()"))
            cleaner_pid = await cleaning.scalar(text("SELECT pg_backend_pid()"))
            await gate.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            # A connection-local function pauses sorting after the statement's
            # snapshot is acquired, but before FOR UPDATE locks source rows.
            await cleaning.execute(
                text(f"""
                CREATE FUNCTION pg_temp.orphan_selection_barrier() RETURNS boolean
                LANGUAGE plpgsql VOLATILE AS $$
                BEGIN
                    PERFORM pg_advisory_xact_lock({key}::bigint);
                    RETURN true;
                END $$
            """)
            )

            def pause_source_selection(execution):
                statement = execution.statement
                if (
                    execution.is_select
                    and statement._for_update_arg is not None
                    and statement.column_descriptions[0]["entity"] is FeedSource
                ):
                    # Empty sources are not retention candidates, so only the
                    # orphan selection reaches this volatile sort expression.
                    execution.statement = statement.order_by(
                        func.pg_temp.orphan_selection_barrier()
                    )

            event.listen(cleaning.sync_session, "do_orm_execute", pause_source_selection)
            task = asyncio.create_task(purge_orphan_sources(cleaning))
            try:
                async with asyncio.timeout(5):
                    while not await gate.scalar(
                        text("SELECT :gate = ANY(pg_blocking_pids(:cleaner))"),
                        {"gate": gate_pid, "cleaner": cleaner_pid},
                    ):
                        if task.done():
                            await task
                            pytest.fail("Cleanup finished before the snapshot barrier")
                        await asyncio.sleep(0.01)
                source = await subscribing.get(FeedSource, source_id)
                result = await _subscribe(subscribing, source, users[0])
                subscription_id = result[1].id
                await gate.commit()
                await asyncio.wait_for(task, 5)
            finally:
                await gate.rollback()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                event.remove(cleaning.sync_session, "do_orm_execute", pause_source_selection)
                # pg_temp belongs to the connection; fixture engine disposal
                # removes it even though commit returns that connection to the pool.
        async with factory() as session:
            assert await session.get(FeedSource, source_id) is not None
            subscription = await session.get(SourceSubscription, subscription_id)
            assert subscription is not None and subscription.source_id == source_id


async def test_cleanup_preserves_saved_content_without_subscription():
    async with _database() as (factory, source_id, users):
        async with factory() as session:
            contents, _ = await _add_contents(session, source_id, 1)
            session.add(UserSavedContent(user_id=users[0], content_id=contents[0]))
            await session.commit()
            await purge_orphan_sources(session)
        async with factory() as session:
            assert await session.get(FeedSource, source_id) is not None
            assert (
                await session.scalar(
                    select(UserSavedContent.content_id).where(UserSavedContent.user_id == users[0])
                )
                == contents[0]
            )
