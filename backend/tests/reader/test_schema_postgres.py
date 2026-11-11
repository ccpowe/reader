import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from reader import models  # noqa: F401  (registers tables)
from reader.core.db import Base, build_engine, build_session_factory
from reader.core.settings import Settings, get_settings
from reader.main import create_app
from reader.system.api import SCHEMA_REVISION
from tests.reader.pg import postgres_dsn, reset_schema

pytestmark = pytest.mark.postgres


def _compare(connection) -> list:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return compare_metadata(context, Base.metadata)


@pytest.mark.asyncio
async def test_baseline_creates_exactly_the_model_schema() -> None:
    dsn = postgres_dsn()
    await reset_schema(dsn)
    engine = build_engine(Settings(database_url=dsn, database_ssl=False))
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            differences = await connection.run_sync(_compare)
            tables = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
                )
            )
    finally:
        await engine.dispose()

    assert revision == SCHEMA_REVISION
    assert differences == []
    assert tables == 14


@pytest.mark.asyncio
async def test_ready_passes_on_a_migrated_database(monkeypatch) -> None:
    dsn = postgres_dsn()
    await reset_schema(dsn)
    for name, value in {
        "APP_SERVER_ID": "home",
        "APP_SERVER_ACCESS_TOKEN": "token",
        "APP_AUTH_JWT_SECRET": "j" * 40,
        "APP_DATABASE_URL": dsn,
        "APP_DATABASE_SSL": "false",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    app = create_app()
    app.state.engine = build_engine(get_settings())
    app.state.session_factory = build_session_factory(app.state.engine)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ready = await client.get("/ready")
            worker = await client.get("/worker-ready")
    finally:
        await app.state.engine.dispose()

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "schema_revision": SCHEMA_REVISION}
    # No worker has run against this fresh database.
    assert worker.status_code == 503
    assert worker.json()["error"]["code"] == "worker_not_ready"
