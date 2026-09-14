"""Candidate crash recovery and transaction atomicity on guarded PostgreSQL."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_postgres_migrations import _assert_disposable_database, _test_dsn, _test_guard_token

from app.domain.enums import CandidateStatus
from app.storage.database import build_session_factory
from app.storage.models import FeedSource, IngestionCandidate
from app.workers import candidates


@asynccontextmanager
async def _database():
    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    try:
        await _assert_disposable_database(connection, _test_guard_token())
    finally:
        await connection.close()
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    source_id = uuid4()
    try:
        async with factory() as session:
            session.add(
                FeedSource(
                    id=source_id,
                    kind="rss",
                    canonical_key=f"candidate-recovery:{source_id}",
                    canonical_url="https://example.com/feed",
                    display_name="Original",
                )
            )
            await session.commit()
        yield factory, source_id
    finally:
        async with factory() as session:
            await session.execute(delete(FeedSource).where(FeedSource.id == source_id))
            await session.commit()
        await engine.dispose()


def _candidate(source_id, *, attempts=0, status=CandidateStatus.PENDING, expired=True):
    now = datetime.now(UTC)
    return IngestionCandidate(
        id=uuid4(),
        source_id=source_id,
        native_id=str(uuid4()),
        payload={},
        payload_hash="a" * 64,
        observed_at=now,
        suggested_feed_sort_at=now,
        status=status,
        attempt_count=attempts,
        available_at=now - timedelta(minutes=5),
        lease_token=uuid4() if status == CandidateStatus.PROCESSING else None,
        lease_expires_at=(now + timedelta(minutes=-1 if expired else 5))
        if status == CandidateStatus.PROCESSING
        else None,
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_final_crash_becomes_terminal_without_touching_active_or_retryable_leases():
    async with _database() as (factory, source_id):
        final = _candidate(source_id, attempts=5, status=CandidateStatus.PROCESSING)
        retry = _candidate(source_id, attempts=4, status=CandidateStatus.PROCESSING)
        active = _candidate(source_id, attempts=5, status=CandidateStatus.PROCESSING, expired=False)
        active_token = active.lease_token
        async with factory() as session:
            session.add_all([final, retry, active])
            await session.commit()
        async with factory() as session:
            claims = await candidates.claim_candidates(session, limit=100)
        assert [claim.candidate_id for claim in claims] == [retry.id]
        async with factory() as session:
            stored = await session.get(IngestionCandidate, final.id)
            assert stored.status == CandidateStatus.FAILED
            assert stored.last_error_code == "lease_expired"
            assert stored.lease_token is None
            assert stored.lease_expires_at is None
            stored_active = await session.get(IngestionCandidate, active.id)
            assert stored_active.status == CandidateStatus.PROCESSING
            assert stored_active.lease_token == active_token
            stored_retry = await session.get(IngestionCandidate, retry.id)
            assert stored_retry.attempt_count == 5
            assert stored_retry.lease_token == claims[0].lease_token


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["sql", "python", "flush"])
async def test_processing_failure_rolls_back_partial_writes_and_continues(monkeypatch, failure):
    async with _database() as (factory, source_id):
        broken = _candidate(source_id)
        good = _candidate(source_id)
        # Claim the broken row first to prove a failure does not abort the batch.
        broken.observed_at = good.observed_at + timedelta(seconds=1)
        async with factory() as session:
            session.add_all([broken, good])
            await session.commit()

        async def process(session, candidate):
            if candidate.id == broken.id:
                source = await session.get(FeedSource, source_id)
                source.display_name = "Partial projection must roll back"
                await session.flush()
                if failure == "sql":
                    await session.execute(text("SELECT 1 / 0"))
                if failure == "flush":
                    source.canonical_key = None
                    await session.flush()
                raise RuntimeError("projection interrupted")
            candidates._complete_candidate(candidate, content_id=None)

        monkeypatch.setattr(candidates, "_process_candidate", process)
        assert await candidates.process_candidates(factory, limit=100) == 1
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            assert source.display_name == "Original"
            stored = await session.get(IngestionCandidate, broken.id)
            assert stored.status == CandidateStatus.PENDING
            assert stored.attempt_count == 1
            assert stored.available_at > datetime.now(UTC)
            assert stored.last_error_code is not None
            assert stored.lease_token is None
            assert stored.lease_expires_at is None
            completed = await session.scalar(
                select(IngestionCandidate).where(IngestionCandidate.id == good.id)
            )
            assert completed.status == CandidateStatus.COMPLETED


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_failed_processor_does_not_overwrite_lease_reclaimed_after_rollback(monkeypatch):
    async with _database() as (factory, source_id):
        broken = _candidate(source_id)
        async with factory() as session:
            session.add(broken)
            await session.commit()
        replacement_claims = []

        async def process(session, candidate):
            original_token = candidate.lease_token
            original_rollback = session.rollback

            async def rollback_then_reclaim():
                await original_rollback()
                # Deterministically interleave a second worker after rollback
                # releases the lock and before the failed worker reacquires it.
                async with factory() as successor:
                    stored = await successor.scalar(
                        select(IngestionCandidate)
                        .where(IngestionCandidate.id == broken.id)
                        .with_for_update()
                    )
                    assert stored.lease_token == original_token
                    stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    await successor.commit()
                    claims = await candidates.claim_candidates(successor, limit=1)
                    assert len(claims) == 1
                    assert claims[0].candidate_id == broken.id
                    assert claims[0].lease_token != original_token
                    replacement_claims.extend(claims)

            monkeypatch.setattr(session, "rollback", rollback_then_reclaim)
            await session.execute(text("SELECT 1 / 0"))

        monkeypatch.setattr(candidates, "_process_candidate", process)
        assert await candidates.process_candidates(factory, limit=1) == 0
        assert len(replacement_claims) == 1
        async with factory() as session:
            stored = await session.get(IngestionCandidate, broken.id)
            assert stored.status == CandidateStatus.PROCESSING
            assert stored.attempt_count == 2
            assert stored.lease_token == replacement_claims[0].lease_token
            assert stored.lease_expires_at > datetime.now(UTC)
            assert stored.last_error_code is None
            assert stored.last_error_message is None
