"""Durable heartbeat writes and readiness projection for worker loops."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.storage.models import WorkerLoopHeartbeat

REQUIRED_WORKER_LOOPS = (
    "source_scanner",
    "candidate_processor",
    "ranking_snapshot",
    "orphan_cleanup",
    # Web/caption realtime work is owned by the API coordinator, not a worker loop.
    "translation_foreground",
    "translation_background",
)
OPTIONAL_WORKER_LOOPS = ("web_rule_author",)


async def mark_worker_loop_started(
    session_factory,
    *,
    worker_instance_id: UUID,
    loop_name: str,
    expected_interval_seconds: int,
) -> None:
    now = datetime.now(UTC)
    statement = pg_insert(WorkerLoopHeartbeat).values(
        worker_instance_id=worker_instance_id,
        loop_name=loop_name,
        expected_interval_seconds=max(expected_interval_seconds, 1),
        last_started_at=now,
        updated_at=now,
    )
    async with session_factory() as session:
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    WorkerLoopHeartbeat.worker_instance_id,
                    WorkerLoopHeartbeat.loop_name,
                ],
                set_={
                    "expected_interval_seconds": statement.excluded.expected_interval_seconds,
                    "last_started_at": statement.excluded.last_started_at,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
        await session.commit()


async def prune_worker_heartbeats(session_factory, *, retention_days: int = 7) -> None:
    """Bound rows left by replaced worker process instances."""
    cutoff = datetime.now(UTC) - timedelta(days=max(retention_days, 1))
    async with session_factory() as session:
        await session.execute(
            delete(WorkerLoopHeartbeat).where(WorkerLoopHeartbeat.updated_at < cutoff)
        )
        await session.commit()


async def mark_worker_loop_finished(
    session_factory,
    *,
    worker_instance_id: UUID,
    loop_name: str,
    expected_interval_seconds: int,
    error: Exception | None,
    metrics: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "worker_instance_id": worker_instance_id,
        "loop_name": loop_name,
        "expected_interval_seconds": max(expected_interval_seconds, 1),
        "last_started_at": now,
        "updated_at": now,
    }
    updates: dict[str, Any] = {
        "expected_interval_seconds": max(expected_interval_seconds, 1),
        "updated_at": now,
    }
    if error is None:
        values.update(last_succeeded_at=now, last_error_code=None)
        updates.update(last_succeeded_at=now, last_error_code=None)
    else:
        error_code = type(error).__name__[:120]
        values.update(last_failed_at=now, last_error_code=error_code)
        updates.update(last_failed_at=now, last_error_code=error_code)
    if metrics is not None:
        values["last_metrics"] = metrics
        updates["last_metrics"] = metrics
    async with session_factory() as session:
        await session.execute(
            pg_insert(WorkerLoopHeartbeat)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[
                    WorkerLoopHeartbeat.worker_instance_id,
                    WorkerLoopHeartbeat.loop_name,
                ],
                set_=updates,
            )
        )
        await session.commit()


async def load_worker_readiness(session) -> dict[str, Any]:
    rows = list(
        await session.scalars(
            select(WorkerLoopHeartbeat).order_by(WorkerLoopHeartbeat.updated_at.desc())
        )
    )
    return summarize_worker_readiness(rows)


def summarize_worker_readiness(
    rows: list[WorkerLoopHeartbeat],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or datetime.now(UTC)
    latest: dict[str, WorkerLoopHeartbeat] = {}
    for row in rows:
        current = latest.get(row.loop_name)
        if current is None or row.updated_at > current.updated_at:
            latest[row.loop_name] = row

    loops: list[dict[str, Any]] = []
    failures: list[str] = []
    for loop_name in (
        *REQUIRED_WORKER_LOOPS,
        *(name for name in OPTIONAL_WORKER_LOOPS if name in latest),
    ):
        row = latest.get(loop_name)
        if row is None:
            failures.append(f"missing:{loop_name}")
            loops.append({"loop_name": loop_name, "state": "missing"})
            continue
        stale_after = max(row.expected_interval_seconds * 3, 60)
        age_seconds = max((current_time - row.updated_at).total_seconds(), 0)
        latest_failed = row.last_failed_at is not None and (
            row.last_succeeded_at is None or row.last_failed_at > row.last_succeeded_at
        )
        if age_seconds > stale_after:
            state = "stale"
        elif latest_failed:
            state = "failing"
        elif row.last_succeeded_at is None:
            state = "starting"
        else:
            state = "healthy"
        metrics = getattr(row, "last_metrics", None) or {}
        if loop_name in OPTIONAL_WORKER_LOOPS and state == "healthy":
            mode = metrics.get("mode")
            if mode in {"disabled", "paused"}:
                state = mode
        if state != "healthy" and loop_name in REQUIRED_WORKER_LOOPS:
            failures.append(f"{state}:{loop_name}")
        loops.append(
            {
                "loop_name": loop_name,
                "state": state,
                "worker_instance_id": str(row.worker_instance_id),
                "last_started_at": _isoformat(row.last_started_at),
                "last_succeeded_at": _isoformat(row.last_succeeded_at),
                "last_failed_at": _isoformat(row.last_failed_at),
                "last_error_code": row.last_error_code,
                "last_metrics": getattr(row, "last_metrics", None),
                "updated_at": _isoformat(row.updated_at),
                "stale_after_seconds": stale_after,
            }
        )
    return {
        "status": "ready" if not failures else "not_ready",
        "failures": failures,
        "loops": loops,
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
