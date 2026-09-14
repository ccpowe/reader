"""Real PostgreSQL claims and an explicitly gated stale candidate-read boundary."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_web_rule_jobs_postgres import _job_database

from app.domain.enums import SourceStatus
from app.storage.models import FeedSource, SourceSubscription, SourceSyncState
from app.workers.sync import claim_due_sources


async def _due(factory, source_id):
    async with factory() as session:
        state = await session.get(SourceSyncState, source_id)
        state.next_scan_at = datetime.now(UTC) - timedelta(minutes=1)
        state.lease_token = state.lease_expires_at = None
        await session.commit()


@pytest.mark.postgres
async def test_two_real_scanners_never_claim_the_same_source():
    async with _job_database() as (factory, source_id):
        await _due(factory, source_id)

        async def claim():
            async with factory() as session:
                return await claim_due_sources(session, limit=100)

        batches = await asyncio.gather(claim(), claim())
        claims = [item for batch in batches for item in batch]
        assert len({claim.source_id for claim in claims}) == len(claims)
        own_claims = [claim for claim in claims if claim.source_id == source_id]
        assert len(own_claims) == 1
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.lease_token == own_claims[0].lease_token


@pytest.mark.postgres
@pytest.mark.parametrize("change", ["claimed", "paused", "rescheduled", "disabled"])
async def test_locked_refresh_rejects_stale_candidate_snapshot(change):
    """Gate only the candidate read; concurrent writes and locking stay real.

    This deterministically represents a join that observed the state before its
    source lock was acquired, including a stale object in the ORM identity map.
    """
    async with _job_database() as (factory, source_id):
        await _due(factory, source_id)
        read_ready, write_done = asyncio.Event(), asyncio.Event()
        winner_token = None

        async with factory() as session:

            class CandidateSnapshotSession:
                def __getattr__(self, name):
                    return getattr(session, name)

                async def scalars(self, _statement):
                    if _statement.column_descriptions[0].get("entity") is FeedSource:
                        # Discovery backfill is independent of the claim snapshot
                        # whose interleaving this test deliberately controls.
                        return await session.scalars(_statement)
                    snapshot = list(
                        await session.scalars(
                            select(SourceSyncState).where(SourceSyncState.source_id == source_id)
                        )
                    )
                    assert snapshot[0].lease_token is None
                    read_ready.set()
                    await write_done.wait()
                    return snapshot

            async def competing_change():
                nonlocal winner_token
                await read_ready.wait()
                async with factory() as other:
                    if change == "claimed":
                        claims = [
                            claim
                            for claim in await claim_due_sources(other, limit=100)
                            if claim.source_id == source_id
                        ]
                        assert len(claims) == 1
                        winner_token = claims[0].lease_token
                    else:
                        source = await other.get(FeedSource, source_id, with_for_update=True)
                        state = await other.get(SourceSyncState, source_id, with_for_update=True)
                        if change == "paused":
                            source.status = SourceStatus.PAUSED
                        elif change == "rescheduled":
                            state.next_scan_at = datetime.now(UTC) + timedelta(hours=1)
                        else:
                            subscription = await other.scalar(
                                select(SourceSubscription).where(
                                    SourceSubscription.source_id == source_id,
                                )
                            )
                            subscription.is_enabled = False
                        await other.commit()
                write_done.set()

            loser, _ = await asyncio.wait_for(
                asyncio.gather(
                    claim_due_sources(CandidateSnapshotSession(), limit=1),
                    competing_change(),
                ),
                timeout=10,
            )
            assert loser == []

        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.lease_token == winner_token
