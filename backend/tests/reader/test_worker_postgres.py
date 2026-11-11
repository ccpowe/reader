"""The worker's heartbeat bookkeeping against a real database."""

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

    assert result == {"articles": 0, "scan_runs": 0, "sessions": 0}
