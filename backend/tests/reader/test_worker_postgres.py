"""The worker's heartbeat bookkeeping against a real database."""

import asyncio

import pytest

from reader import worker
from reader.core.db import build_engine, build_session_factory
from reader.core.settings import Settings
from reader.system.heartbeats import load_loop_reports
from tests.reader.pg import postgres_dsn, reset_schema

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_run_once_records_success_and_failure() -> None:
    dsn = postgres_dsn()
    await reset_schema(dsn)
    engine = build_engine(Settings(database_url=dsn, database_ssl=False))
    factory = build_session_factory(engine)

    async def fine():
        return 1

    async def broken():
        raise RuntimeError("boom")

    try:
        assert await worker.run_once(factory, "source_scan", 60, fine)
        assert not await worker.run_once(factory, "cleanup", 86400, broken)
        async with factory() as db:
            reports = {report.loop_name: report for report in await load_loop_reports(db)}
    finally:
        await engine.dispose()

    assert reports["source_scan"].state == "healthy"
    assert (reports["cleanup"].state, reports["cleanup"].last_error_code) == (
        "failing",
        "RuntimeError",
    )
    assert reports["title_translation"].state == "missing"


@pytest.mark.asyncio
async def test_cleanup_job_runs_on_an_empty_database() -> None:
    dsn = postgres_dsn()
    await reset_schema(dsn)
    settings = Settings(database_url=dsn, database_ssl=False)
    engine = build_engine(settings)
    try:
        result = await worker.cleanup(build_session_factory(engine), settings)
    finally:
        await engine.dispose()

    assert result == {
        "articles": 0,
        "scan_runs": 0,
        "translations": 0,
        "board_snapshots": 0,
        "sessions": 0,
    }


@pytest.mark.asyncio
async def test_a_long_job_keeps_its_heartbeat_fresh(monkeypatch) -> None:
    from sqlalchemy import select

    from reader.models import WorkerHeartbeat

    dsn = postgres_dsn()
    await reset_schema(dsn)
    engine = build_engine(Settings(database_url=dsn, database_ssl=False))
    factory = build_session_factory(engine)
    monkeypatch.setattr(worker, "ALIVE_INTERVAL_SECONDS", 0.05)

    async def updated_at():
        async with factory() as db:
            return await db.scalar(select(WorkerHeartbeat.updated_at))

    seen = []

    async def long_job():
        seen.append(await updated_at())
        await asyncio.sleep(0.3)
        seen.append(await updated_at())

    try:
        assert await worker.run_once(factory, "title_translation", 5, long_job)
    finally:
        await engine.dispose()

    assert seen[1] > seen[0]


def test_every_required_loop_is_scheduled() -> None:
    from reader.system.heartbeats import REQUIRED_LOOPS

    loops = worker.jobs(None, Settings(_env_file=None), None)
    assert set(loops) == set(REQUIRED_LOOPS)
