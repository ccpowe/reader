"""Scanner progress and candidate admission remain one durable transition."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_candidate_recovery_postgres import _database

from app.core.settings import get_settings
from app.domain.enums import ContentKind, SyncPhase, SyncRunStatus
from app.ingestion.models import DiscoveredContent, SourceScanPage
from app.storage.models import (
    FeedSource,
    IngestionCandidate,
    SourceEntry,
    SourceSyncRun,
    SourceSyncState,
)
from app.workers import candidates, sync

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def _item(native_id):
    return DiscoveredContent(
        native_id=native_id,
        kind=ContentKind.ARTICLE,
        title=f"Article {native_id}",
        external_url=f"https://example.com/{native_id}",
        published_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


def _page(*native_ids, checkpoint=None, **kwargs):
    items = tuple(_item(value) for value in native_ids)
    return SourceScanPage(
        items=items,
        provider_mode="rss",
        raw_items=len(items),
        checkpoint=checkpoint
        if checkpoint is not None
        else {
            "head_ids": list(native_ids),
            "item_hashes": {item.native_id: item.material_hash() for item in items},
        },
        **kwargs,
    )


class _Pages:
    def __init__(self, factory, *pages):
        self.engine = factory.kw["bind"]
        self.pages = iter(pages)
        self.requests = []

    async def scan_page(self, request):
        # Upstream waits must not retain a database connection or source lock.
        assert self.engine.sync_engine.pool.checkedout() == 0
        self.requests.append(request)
        return next(self.pages)


async def _scan(factory, source_id, adapter, monkeypatch):
    token = uuid4()
    async with factory() as session:
        state = await session.get(SourceSyncState, source_id)
        state.lease_token = token
        state.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        await session.commit()
    monkeypatch.setattr(sync, "_build_adapter", lambda *_args, **_kwargs: adapter)
    await sync.scan_claimed_source(factory, sync.SourceClaim(source_id, token))


async def test_candidate_cap_replays_backlog_and_delayed_baseline_stays_non_update(monkeypatch):
    settings = get_settings()
    async with _database() as (factory, source_id):
        async with factory() as session:
            session.add(SourceSyncState(source_id=source_id))
            await session.commit()
        baseline = _page("baseline", completed=True)
        await _scan(factory, source_id, _Pages(factory, baseline), monkeypatch)
        monkeypatch.setattr(settings, "ingestion_max_items_per_sync", 1)
        head = _page("new-a", "new-b", completed=True)
        await _scan(factory, source_id, _Pages(factory, head), monkeypatch)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.initial_sync_completed
            assert state.phase == SyncPhase.CATCHING_UP
            assert state.committed_checkpoint["head_ids"] == ["baseline"]
            assert state.pending_checkpoint == head.checkpoint
            assert state.continuation["tasks"][0]["cursor"] == {"_restart_from_head": True}
            admitted = dict(
                (
                    await session.execute(
                        select(
                            IngestionCandidate.native_id, IngestionCandidate.counts_as_update
                        ).where(IngestionCandidate.source_id == source_id)
                    )
                ).all()
            )
            assert admitted == {"baseline": False, "new-a": True}
        monkeypatch.setattr(settings, "ingestion_max_items_per_sync", 40)
        resumed = _Pages(factory, head, head)
        await _scan(factory, source_id, resumed, monkeypatch)
        assert [request.checkpoint["head_ids"] for request in resumed.requests] == [
            ["new-a", "new-b"],
            ["baseline"],
        ]
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.phase == SyncPhase.IDLE
            assert state.continuation is None and state.pending_checkpoint == {}
            assert state.committed_checkpoint["head_ids"] == ["new-a", "new-b"]
            assert state.lease_token is None and state.next_scan_at is not None
        assert await candidates.process_candidates(factory) == 3
        async with factory() as session:
            sequences = dict(
                (
                    await session.execute(
                        select(SourceEntry.native_id, SourceEntry.update_sequence).where(
                            SourceEntry.source_id == source_id
                        )
                    )
                ).all()
            )
            assert sequences["baseline"] is None
            updates = [sequences[key] for key in ("new-a", "new-b")]
            assert all(value is not None and value > 0 for value in updates)
            assert updates[0] != updates[1]
            assert (await session.get(FeedSource, source_id)).latest_update_sequence == max(updates)


async def test_degraded_fallback_retains_backlog_and_authoritative_commit_keeps_atom(monkeypatch):
    committed = {"data_api": {"head_ids": ["old"]}, "atom": {"head_ids": ["old-atom"]}}
    pending = {"data_api": {"head_ids": ["head"]}, "atom": {"head_ids": ["old-atom"]}}
    continuation = {
        "latest_checkpoint": pending,
        "tasks": [{"lane": "backlog", "cursor": {"page": "history"}, "checkpoint": committed}],
    }
    async with _database() as (factory, source_id):
        async with factory() as session:
            session.add(
                SourceSyncState(
                    source_id=source_id,
                    initial_sync_completed=True,
                    committed_checkpoint=committed,
                    pending_checkpoint=pending,
                    continuation=continuation,
                    phase=SyncPhase.CATCHING_UP,
                )
            )
            await session.commit()
        fallback = _page(
            completed=True,
            authoritative=False,
            checkpoint={"atom": {"head_ids": ["new-atom"]}},
            warning_code="youtube_quota_exhausted",
        )
        await _scan(factory, source_id, _Pages(factory, fallback), monkeypatch)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.phase == SyncPhase.DEGRADED
            assert state.continuation == continuation
            assert state.pending_checkpoint == pending
            assert state.committed_checkpoint["data_api"] == committed["data_api"]
            assert state.committed_checkpoint["atom"] == {"head_ids": ["new-atom"]}
        latest = {"data_api": {"head_ids": ["fresh"]}, "atom": {"head_ids": ["old-atom"]}}
        resumed = _Pages(
            factory,
            _page(completed=True, checkpoint=latest),
            _page(completed=True, checkpoint=committed),
        )
        await _scan(factory, source_id, resumed, monkeypatch)
        assert [request.continuation for request in resumed.requests] == [None, {"page": "history"}]
        assert all(not request.conditional for request in resumed.requests)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.committed_checkpoint["data_api"] == latest["data_api"]
            assert state.committed_checkpoint["atom"] == {"head_ids": ["new-atom"]}
            assert state.phase == SyncPhase.IDLE and state.continuation is None
            assert state.last_error_code is None and state.last_complete_at is not None


@pytest.mark.parametrize("has_backlog", [False, True])
async def test_truncated_atom_fallback_replays_unadmitted_items(monkeypatch, has_backlog):
    monkeypatch.setattr(get_settings(), "ingestion_max_items_per_sync", 1)
    committed = {"data_api": {"head_ids": ["old"]}, "atom": {"head_ids": ["old-atom"]}}
    pending = {"data_api": {"head_ids": ["head"]}} if has_backlog else {}
    continuation = (
        {
            "latest_checkpoint": pending,
            "tasks": [{"lane": "backlog", "cursor": {"page": "history"}, "checkpoint": committed}],
        }
        if has_backlog
        else None
    )
    atom = _page("a", "b", completed=True).checkpoint
    fallback = _page(
        "a",
        "b",
        checkpoint={"atom": atom},
        completed=True,
        authoritative=False,
        warning_code="youtube_quota_exhausted",
    )
    async with _database() as (factory, source_id):
        async with factory() as session:
            session.add(
                SourceSyncState(
                    source_id=source_id,
                    initial_sync_completed=True,
                    committed_checkpoint=committed,
                    pending_checkpoint=pending,
                    continuation=continuation,
                )
            )
            await session.commit()
        await _scan(factory, source_id, _Pages(factory, fallback), monkeypatch)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.committed_checkpoint == committed
            assert state.continuation == continuation
            assert state.pending_checkpoint == pending
            assert state.last_complete_at is None
            assert state.lease_token is None
            assert set(
                await session.scalars(
                    select(IngestionCandidate.native_id).where(
                        IngestionCandidate.source_id == source_id
                    )
                )
            ) == {"a"}
        await _scan(factory, source_id, _Pages(factory, fallback), monkeypatch)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.committed_checkpoint == {**committed, "atom": atom}
            assert state.continuation == continuation
            assert state.pending_checkpoint == pending
            assert state.phase == SyncPhase.DEGRADED
            assert state.last_complete_at is None
            assert set(
                await session.scalars(
                    select(IngestionCandidate.native_id).where(
                        IngestionCandidate.source_id == source_id
                    )
                )
            ) == {"a", "b"}


async def test_page_commit_failure_rolls_back_candidates_and_progress_before_retry(monkeypatch):
    async with _database() as (factory, source_id):
        async with factory() as session:
            session.add(SourceSyncState(source_id=source_id))
            await session.commit()
        fail_once = True

        def interrupt_commit(session):
            nonlocal fail_once
            if fail_once and any(
                isinstance(row, SourceSyncState)
                and row.source_id == source_id
                and row.initial_sync_completed
                for row in session.dirty
            ):
                fail_once = False
                raise ValueError("Interrupted before page commit")

        event.listen(Session, "before_commit", interrupt_commit)
        try:
            await _scan(
                factory, source_id, _Pages(factory, _page("baseline", completed=True)), monkeypatch
            )
        finally:
            event.remove(Session, "before_commit", interrupt_commit)
        assert not fail_once
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert not state.initial_sync_completed
            assert state.committed_checkpoint == {} and state.continuation is None
            assert state.phase == SyncPhase.DEGRADED and state.lease_token is None
            assert state.last_error_code == "ValueError"
            assert (
                list(
                    await session.scalars(
                        select(IngestionCandidate).where(IngestionCandidate.source_id == source_id)
                    )
                )
                == []
            )
            run = await session.scalar(
                select(SourceSyncRun).where(SourceSyncRun.source_id == source_id)
            )
            assert run.status == SyncRunStatus.FAILED and run.metrics["pages"] == 0
        await _scan(
            factory, source_id, _Pages(factory, _page("baseline", completed=True)), monkeypatch
        )
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.initial_sync_completed and state.phase == SyncPhase.IDLE
            assert state.committed_checkpoint["head_ids"] == ["baseline"]
            item = await session.scalar(
                select(IngestionCandidate).where(IngestionCandidate.source_id == source_id)
            )
            assert item.native_id == "baseline" and item.counts_as_update is False


@pytest.mark.parametrize("limit", ["pages", "raw_items"])
async def test_scan_budget_saves_continuation_without_advancing_committed_head(monkeypatch, limit):
    settings = get_settings()
    if limit == "pages":
        monkeypatch.setattr(settings, "ingestion_max_pages_per_sync", 1)
    else:
        monkeypatch.setattr(settings, "ingestion_raw_items_per_sync", 10)
    async with _database() as (factory, source_id):
        async with factory() as session:
            session.add(
                SourceSyncState(
                    source_id=source_id,
                    initial_sync_completed=True,
                    committed_checkpoint={"head_ids": ["known"]},
                )
            )
            await session.commit()
        page = _page(
            *(f"new-{index}" for index in range(10)),
            completed=False,
            next_continuation={"page": "older"},
        )
        await _scan(factory, source_id, _Pages(factory, page), monkeypatch)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.committed_checkpoint == {"head_ids": ["known"]}
            assert state.pending_checkpoint == page.checkpoint
            assert state.continuation["tasks"] == [
                {
                    "lane": "backlog",
                    "cursor": {"page": "older"},
                    "checkpoint": {"head_ids": ["known"]},
                }
            ]
            assert state.phase == SyncPhase.CATCHING_UP and state.lease_token is None
            run = await session.scalar(
                select(SourceSyncRun).where(SourceSyncRun.source_id == source_id)
            )
            assert run.status == SyncRunStatus.PARTIAL
            assert run.metrics["limits"] == [limit]
            assert run.metrics["coverage_complete"] is False
            assert set(
                await session.scalars(
                    select(IngestionCandidate.native_id).where(
                        IngestionCandidate.source_id == source_id
                    )
                )
            ) == {f"new-{index}" for index in range(10)}


async def test_invalid_provider_cursor_restarts_backlog_without_losing_checkpoints(monkeypatch):
    from app.ingestion.models import SourceScanError

    class ExpiredCursor:
        async def scan_page(self, request):
            raise SourceScanError("cursor_invalid", "Provider cursor expired")

    async with _database() as (factory, source_id):
        async with factory() as session:
            session.add(
                SourceSyncState(
                    source_id=source_id,
                    initial_sync_completed=True,
                    committed_checkpoint={"head_ids": ["known"]},
                    pending_checkpoint={"head_ids": ["new"]},
                    continuation={
                        "latest_checkpoint": {"head_ids": ["new"]},
                        "tasks": [
                            {
                                "lane": "backlog",
                                "cursor": {"page": "expired"},
                                "checkpoint": {"head_ids": ["known"]},
                            }
                        ],
                    },
                )
            )
            await session.commit()
        await _scan(factory, source_id, ExpiredCursor(), monkeypatch)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.committed_checkpoint == {"head_ids": ["known"]}
            assert state.pending_checkpoint == {"head_ids": ["new"]}
            assert state.continuation == {
                "latest_checkpoint": {"head_ids": ["new"]},
                "tasks": [
                    {
                        "lane": "backlog",
                        "cursor": {"_restart_from_head": True},
                        "checkpoint": {"head_ids": ["known"]},
                    }
                ],
            }
            assert state.phase == SyncPhase.CATCHING_UP and state.lease_token is None
            assert state.consecutive_failures == 0 and state.next_scan_at is not None
            run = await session.scalar(
                select(SourceSyncRun).where(SourceSyncRun.source_id == source_id)
            )
            assert run.status == SyncRunStatus.PARTIAL and run.error_code == "cursor_invalid"


@pytest.mark.parametrize("complete", [False, True])
async def test_empty_first_page_checkpoint_uses_later_head_evidence(monkeypatch, complete):
    monkeypatch.setattr(get_settings(), "ingestion_max_pages_per_sync", 2)
    async with _database() as (factory, source_id):
        async with factory() as session:
            session.add(
                SourceSyncState(
                    source_id=source_id,
                    initial_sync_completed=True,
                    committed_checkpoint={"head_ids": ["known"]},
                )
            )
            await session.commit()
        later = _page(
            "new", completed=complete, next_continuation=None if complete else {"page": "older"}
        )
        pages = _Pages(
            factory,
            _page(completed=False, checkpoint={}, next_continuation={"page": "next"}),
            later,
        )
        await _scan(factory, source_id, pages, monkeypatch)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            if complete:
                assert state.committed_checkpoint["head_ids"] == ["new"]
                assert state.continuation is None
            else:
                assert state.committed_checkpoint == {"head_ids": ["known"]}
                assert state.pending_checkpoint == later.checkpoint
                assert state.continuation["latest_checkpoint"] == later.checkpoint
