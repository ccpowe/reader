"""Background-loop heartbeats: the worker writes them, /worker-ready reads them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from reader.models import WorkerHeartbeat

# The worker runs exactly these loops.
REQUIRED_LOOPS = ("source_scan", "title_translation", "cleanup")

LoopState = Literal["healthy", "starting", "failing", "stale", "missing"]


@dataclass(frozen=True)
class LoopReport:
    loop_name: str
    state: LoopState
    last_succeeded_at: datetime | None = None
    last_error_code: str | None = None


async def record_loop_start(session: AsyncSession, loop_name: str, interval_seconds: int) -> None:
    now = datetime.now(UTC)
    await _upsert(
        session,
        loop_name,
        {"expected_interval_seconds": max(interval_seconds, 1), "last_started_at": now},
    )


async def record_loop_result(
    session: AsyncSession, loop_name: str, interval_seconds: int, *, error_code: str | None
) -> None:
    now = datetime.now(UTC)
    values: dict[str, object] = {"expected_interval_seconds": max(interval_seconds, 1)}
    if error_code is None:
        values.update(last_succeeded_at=now, last_error_code=None)
    else:
        values.update(last_failed_at=now, last_error_code=error_code[:120])
    await _upsert(session, loop_name, values)


async def _upsert(session: AsyncSession, loop_name: str, values: dict[str, object]) -> None:
    now = datetime.now(UTC)
    statement = pg_insert(WorkerHeartbeat).values(loop_name=loop_name, updated_at=now, **values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.loop_name], set_={**values, "updated_at": now}
        )
    )
    await session.commit()


async def load_loop_reports(session: AsyncSession) -> list[LoopReport]:
    rows = {row.loop_name: row for row in await session.scalars(select(WorkerHeartbeat))}
    return [summarize(name, rows.get(name)) for name in REQUIRED_LOOPS]


def summarize(
    loop_name: str, row: WorkerHeartbeat | None, *, now: datetime | None = None
) -> LoopReport:
    if row is None:
        return LoopReport(loop_name, "missing")
    current = now or datetime.now(UTC)
    stale_after = max(row.expected_interval_seconds * 3, 60)
    failed_last = row.last_failed_at is not None and (
        row.last_succeeded_at is None or row.last_failed_at > row.last_succeeded_at
    )
    if (current - row.updated_at).total_seconds() > stale_after:
        state: LoopState = "stale"
    elif failed_last:
        state = "failing"
    elif row.last_succeeded_at is None:
        state = "starting"
    else:
        state = "healthy"
    return LoopReport(loop_name, state, row.last_succeeded_at, row.last_error_code)
