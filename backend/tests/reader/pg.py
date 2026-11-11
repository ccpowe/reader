"""Guards for destructive PostgreSQL tests.

They only run through scripts/test-postgres-migrations.sh, which starts a
throwaway container, and refuse any database that lacks its random ownership
token.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
RUN_ENV = "READER_RUN_POSTGRES_TESTS"
DSN_ENV = "READER_TEST_POSTGRES_DSN"
GUARD_ENV = "READER_TEST_POSTGRES_GUARD"
_GUARD_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def postgres_dsn() -> str:
    if os.environ.get(RUN_ENV) != "1":
        pytest.skip(f"set {RUN_ENV}=1 (via scripts/test-postgres-migrations.sh)")
    dsn = os.environ.get(DSN_ENV, "")
    parsed = urlsplit(dsn)
    if (
        parsed.scheme not in {"postgresql", "postgres"}
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.path != "/reader_test"
        or parsed.query
        or parsed.fragment
    ):
        pytest.fail(f"{DSN_ENV} must target 127.0.0.1:<port>/reader_test", pytrace=False)
    return dsn


async def assert_disposable(connection: asyncpg.Connection) -> None:
    expected = os.environ.get(GUARD_ENV, "")
    if not _GUARD_TOKEN_RE.fullmatch(expected):
        pytest.fail(f"{GUARD_ENV} must hold the test script's ownership token", pytrace=False)
    try:
        stored = await connection.fetchval(
            "SELECT token FROM reader_test_guard.ownership WHERE singleton = true"
        )
    except asyncpg.PostgresError:
        pytest.fail("refusing: the database ownership guard is missing", pytrace=False)
    if not isinstance(stored, str) or not secrets.compare_digest(stored, expected):
        pytest.fail("refusing: the database ownership guard does not match", pytrace=False)


async def reset_schema(dsn: str) -> None:
    """Drop everything Reader owns and migrate an empty database to head."""
    connection = await asyncpg.connect(dsn)
    try:
        await assert_disposable(connection)
        await connection.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
    finally:
        await connection.close()
    alembic("upgrade", "head", dsn=dsn)


def alembic(*arguments: str, dsn: str) -> None:
    environment = {
        **os.environ,
        "APP_DATABASE_URL": dsn,
        "APP_DATABASE_SSL": "false",
        "READER_ALEMBIC_DISABLE_DOTENV": "1",
        "PYTHON_DOTENV_DISABLED": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        pytest.fail(f"alembic {' '.join(arguments)} failed:\n{result.stderr[-3000:]}")
