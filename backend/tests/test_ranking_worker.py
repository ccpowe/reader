from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.settings import Settings
from app.services.ranking_snapshots import (
    RankingCycleError,
    _clear_refresh_lease_on_cancel,
    sync_due_ranking_snapshots,
)
from app.workers.health import summarize_worker_readiness


def _snapshot(index: int):
    kind = "hacker_news" if index == 0 else "github"
    return SimpleNamespace(
        cache_key=f"ranking:v2:{kind}",
        kind=kind,
        parameters={},
        next_refresh_at=datetime.now(UTC) - timedelta(minutes=1),
        last_accessed_at=datetime.now(UTC),
    )


def _session_with_snapshots(snapshots):
    session = AsyncMock()
    session.scalars.return_value = list(snapshots)
    return session


def _factory_for(captured):
    def factory():
        session = AsyncMock()
        captured.append(session)

        @asynccontextmanager
        async def context():
            yield session

        return context()

    return factory


@pytest.mark.asyncio
async def test_ranking_worker_uses_bounded_independent_sessions_and_reports_metrics(monkeypatch):
    snapshots = [_snapshot(index) for index in range(5)]
    scheduler_session = _session_with_snapshots(snapshots)
    independent_sessions = []
    active = 0
    peak = 0

    async def refresh(_session, _request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(
        "app.services.ranking_snapshots.get_settings",
        lambda: SimpleNamespace(
            ranking_snapshot_inactive_ttl_days=7,
            ranking_worker_concurrency=4,
            ranking_worker_cycle_timeout_seconds=120,
        ),
    )
    monkeypatch.setattr(
        "app.services.ranking_snapshots.create_or_refresh_snapshot",
        refresh,
    )
    metrics = {}

    handled = await sync_due_ranking_snapshots(
        scheduler_session,
        session_factory=_factory_for(independent_sessions),
        metrics_out=metrics,
    )

    assert handled == 5
    assert peak == 4
    assert len(independent_sessions) == 5
    assert metrics["status"] == "success"
    assert metrics["requested"] == 5
    assert metrics["succeeded"] == 5
    assert metrics["failed"] == 0


@pytest.mark.asyncio
async def test_all_failed_cycle_raises_with_structured_metrics(monkeypatch):
    scheduler_session = _session_with_snapshots([_snapshot(0), _snapshot(1)])

    async def refresh(*_args):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        "app.services.ranking_snapshots.get_settings",
        lambda: SimpleNamespace(
            ranking_snapshot_inactive_ttl_days=7,
            ranking_worker_concurrency=4,
            ranking_worker_cycle_timeout_seconds=120,
        ),
    )
    monkeypatch.setattr(
        "app.services.ranking_snapshots.create_or_refresh_snapshot",
        refresh,
    )
    metrics = {}

    with pytest.raises(RankingCycleError) as error:
        await sync_due_ranking_snapshots(
            scheduler_session,
            session_factory=_factory_for([]),
            metrics_out=metrics,
        )

    assert error.value.metrics["status"] == "failed"
    assert error.value.metrics["failed"] == 2
    assert metrics["results"]


@pytest.mark.asyncio
async def test_partial_cycle_returns_count_and_keeps_success_failure_metrics(monkeypatch):
    scheduler_session = _session_with_snapshots([_snapshot(0), _snapshot(1)])
    calls = 0

    async def refresh(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("one provider failed")

    monkeypatch.setattr(
        "app.services.ranking_snapshots.get_settings",
        lambda: SimpleNamespace(
            ranking_snapshot_inactive_ttl_days=7,
            ranking_worker_concurrency=4,
            ranking_worker_cycle_timeout_seconds=120,
        ),
    )
    monkeypatch.setattr(
        "app.services.ranking_snapshots.create_or_refresh_snapshot",
        refresh,
    )
    metrics = {}

    assert (
        await sync_due_ranking_snapshots(
            scheduler_session,
            session_factory=_factory_for([]),
            metrics_out=metrics,
        )
        == 2
    )
    assert metrics["status"] == "partial"
    assert metrics["succeeded"] == 1
    assert metrics["failed"] == 1


@pytest.mark.asyncio
async def test_cycle_timeout_is_a_failure_when_every_attempt_times_out(monkeypatch):
    scheduler_session = _session_with_snapshots([_snapshot(0)])
    never = asyncio.Event()

    async def refresh(*_args):
        await never.wait()

    monkeypatch.setattr(
        "app.services.ranking_snapshots.get_settings",
        lambda: SimpleNamespace(
            ranking_snapshot_inactive_ttl_days=7,
            ranking_worker_concurrency=4,
            ranking_worker_cycle_timeout_seconds=1,
        ),
    )
    monkeypatch.setattr(
        "app.services.ranking_snapshots.create_or_refresh_snapshot",
        refresh,
    )
    metrics = {}

    with pytest.raises(RankingCycleError) as error:
        await sync_due_ranking_snapshots(
            scheduler_session,
            session_factory=_factory_for([]),
            cycle_timeout_seconds=0.01,
            metrics_out=metrics,
        )

    assert error.value.metrics["status"] == "timeout"
    assert error.value.metrics["timed_out"] == 1
    assert metrics["status"] == "timeout"


@pytest.mark.asyncio
async def test_cancelled_refresh_clears_only_its_authoritative_lease():
    token = object()
    snapshot = SimpleNamespace(
        refresh_token=token,
        refresh_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        next_refresh_at=datetime.now(UTC) + timedelta(minutes=1),
        last_error=None,
        last_metrics=None,
    )
    session = AsyncMock()
    session.scalar.return_value = snapshot

    request = SimpleNamespace(cache_key="ranking:v2:hacker_news")
    await _clear_refresh_lease_on_cancel(session, request, token)

    assert snapshot.refresh_token is None
    assert snapshot.refresh_expires_at is None
    assert snapshot.last_error == "ranking_refresh_cancelled"
    assert snapshot.last_metrics["status"] == "cancelled"
    session.commit.assert_awaited_once()


def test_worker_ready_report_exposes_last_metrics():
    now = datetime.now(UTC)
    rows = [
        SimpleNamespace(
            worker_instance_id="worker",
            loop_name=loop_name,
            expected_interval_seconds=60,
            last_started_at=now,
            last_succeeded_at=now,
            last_failed_at=None,
            last_error_code=None,
            last_metrics={"status": "partial", "succeeded": 1, "failed": 1}
            if loop_name == "ranking_snapshot"
            else None,
            updated_at=now,
        )
        for loop_name in (
            "source_scanner",
            "candidate_processor",
            "ranking_snapshot",
            "orphan_cleanup",
            "translation_realtime",
            "translation_foreground",
            "translation_background",
        )
    ]

    report = summarize_worker_readiness(rows, now=now)
    ranking = next(item for item in report["loops"] if item["loop_name"] == "ranking_snapshot")
    assert ranking["last_metrics"]["status"] == "partial"


def test_ranking_worker_defaults_are_bounded():
    settings = Settings(_env_file=None)
    assert settings.ranking_worker_concurrency == 4
    assert settings.ranking_worker_cycle_timeout_seconds == 120
