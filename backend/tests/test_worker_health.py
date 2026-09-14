from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.workers.health import (
    REQUIRED_WORKER_LOOPS,
    mark_worker_loop_finished,
    mark_worker_loop_started,
    summarize_worker_readiness,
)


def _row(loop_name: str, *, now: datetime, failed: bool = False, age: int = 0):
    updated_at = now - timedelta(seconds=age)
    return SimpleNamespace(
        worker_instance_id=uuid4(),
        loop_name=loop_name,
        expected_interval_seconds=10,
        last_started_at=updated_at,
        last_succeeded_at=None if failed else updated_at,
        last_failed_at=updated_at if failed else None,
        last_error_code="RuntimeError" if failed else None,
        updated_at=updated_at,
    )


def test_worker_readiness_distinguishes_missing_failing_and_stale_loops() -> None:
    now = datetime.now(UTC)
    rows = [_row(name, now=now) for name in REQUIRED_WORKER_LOOPS]
    rows[0] = _row(REQUIRED_WORKER_LOOPS[0], now=now, failed=True)
    rows[1] = _row(REQUIRED_WORKER_LOOPS[1], now=now, age=61)
    rows.pop()

    report = summarize_worker_readiness(rows, now=now)  # type: ignore[arg-type]

    assert report["status"] == "not_ready"
    assert f"failing:{REQUIRED_WORKER_LOOPS[0]}" in report["failures"]
    assert f"stale:{REQUIRED_WORKER_LOOPS[1]}" in report["failures"]
    assert f"missing:{REQUIRED_WORKER_LOOPS[-1]}" in report["failures"]


@pytest.mark.parametrize("legacy_row", [False, True])
def test_worker_readiness_ignores_retired_realtime_worker(legacy_row: bool) -> None:
    now = datetime.now(UTC)
    names = (
        "source_scanner",
        "candidate_processor",
        "ranking_snapshot",
        "orphan_cleanup",
        "translation_foreground",
        "translation_background",
    )
    rows = [_row(name, now=now) for name in names]
    if legacy_row:
        rows.append(_row("translation_realtime", now=now, age=3600, failed=True))

    report = summarize_worker_readiness(rows, now=now)  # type: ignore[arg-type]

    assert report["status"] == "ready"
    assert report["failures"] == []
    assert {loop["loop_name"] for loop in report["loops"]} == set(names)


@pytest.mark.parametrize(
    "mode,failed,age,expected",
    [
        ("disabled", False, 0, "disabled"),
        ("paused", False, 0, "paused"),
        ("active", False, 0, "healthy"),
        ("active", True, 0, "failing"),
        ("active", False, 61, "stale"),
    ],
)
def test_optional_rule_author_health_does_not_break_existing_sync_readiness(
    mode, failed, age, expected
):
    now = datetime.now(UTC)
    rows = [_row(name, now=now) for name in REQUIRED_WORKER_LOOPS]
    author = _row("web_rule_author", now=now, failed=failed, age=age)
    author.last_metrics = {"mode": mode, "pending_count": 3}
    report = summarize_worker_readiness([*rows, author], now=now)
    assert report["status"] == "ready"
    assert report["failures"] == []
    author_health = next(loop for loop in report["loops"] if loop["loop_name"] == "web_rule_author")
    assert author_health["state"] == expected
    assert author_health["last_metrics"]["pending_count"] == 3


@pytest.mark.asyncio
async def test_worker_heartbeat_writes_are_atomic_upserts() -> None:
    session = AsyncMock()

    @asynccontextmanager
    async def session_factory():
        yield session

    worker_id = uuid4()
    await mark_worker_loop_started(
        session_factory,
        worker_instance_id=worker_id,
        loop_name="source_scanner",
        expected_interval_seconds=60,
    )
    await mark_worker_loop_finished(
        session_factory,
        worker_instance_id=worker_id,
        loop_name="source_scanner",
        expected_interval_seconds=60,
        error=None,
    )

    statements = [
        str(call.args[0].compile(dialect=postgresql.dialect()))
        for call in session.execute.await_args_list
    ]
    assert all(
        "ON CONFLICT (worker_instance_id, loop_name) DO UPDATE" in statement
        for statement in statements
    )
    assert session.commit.await_count == 2
