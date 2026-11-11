"""Destructive migration contract checks for an explicitly disposable PostgreSQL DB."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.translation_quota import TranslationQuotaAdminService
from app.api.saved import _query_saved_page, save_content
from app.api.translation_preferences import (
    UpdateTranslationPreferenceRequest,
    update_translation_preference,
)
from app.core.auth import AuthenticatedUser
from app.core.settings import Settings
from app.ingestion.source_identity import canonical_rss_identity
from app.services import ranking_snapshots
from app.services.subscriptions import subscribe_to_source
from app.storage.database import build_session_factory
from app.storage.schema import (
    REQUIRED_SCHEMA_CONTRACT,
    REQUIRED_SCHEMA_REVISION,
    runtime_readiness_failures,
)
from app.translation.quota import (
    TRANSLATION_QUOTA_GLOBAL_CHARS_EXCEEDED,
    TRANSLATION_QUOTA_STORAGE_UNAVAILABLE,
    TRANSLATION_QUOTA_USER_CHARS_EXCEEDED,
    TranslationDisabledForUser,
    TranslationQuotaExceeded,
    TranslationQuotaStorageUnavailable,
    TranslationQuotaStore,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
RUN_ENV = "READER_RUN_POSTGRES_TESTS"
DSN_ENV = "READER_TEST_POSTGRES_DSN"
GUARD_ENV = "READER_TEST_POSTGRES_GUARD"
_GUARD_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _test_dsn() -> str:
    if os.environ.get(RUN_ENV) != "1":
        pytest.skip(f"set {RUN_ENV}=1 to run destructive PostgreSQL migration checks")
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        pytest.fail(f"{DSN_ENV} is required when {RUN_ENV}=1")
    try:
        parsed = urlsplit(dsn)
        port = parsed.port
    except ValueError as exc:
        pytest.fail(f"{DSN_ENV} is malformed: {exc}", pytrace=False)
    if parsed.scheme not in {"postgresql", "postgres"}:
        pytest.fail(f"{DSN_ENV} must be a plain asyncpg-compatible PostgreSQL DSN")
    if parsed.hostname != "127.0.0.1":
        pytest.fail(f"{DSN_ENV} must target 127.0.0.1", pytrace=False)
    if port is None:
        pytest.fail(f"{DSN_ENV} must include the temporary PostgreSQL port", pytrace=False)
    if parsed.path != "/reader_test":
        pytest.fail(f"{DSN_ENV} must target the reader_test database", pytrace=False)
    if parsed.query or parsed.fragment:
        pytest.fail(f"{DSN_ENV} must not contain query parameters or a fragment", pytrace=False)
    return dsn


def _test_guard_token() -> str:
    guard_token = os.environ.get(GUARD_ENV, "")
    if not _GUARD_TOKEN_RE.fullmatch(guard_token):
        pytest.fail(
            f"{GUARD_ENV} must contain the random ownership token created by the test script",
            pytrace=False,
        )
    return guard_token


async def _assert_disposable_database(connection: asyncpg.Connection, guard_token: str) -> None:
    try:
        stored_token = await connection.fetchval(
            "SELECT token FROM reader_test_guard.ownership WHERE singleton = true"
        )
    except asyncpg.PostgresError:
        pytest.fail(
            "refusing destructive migration test: database ownership guard is missing",
            pytrace=False,
        )
    if not isinstance(stored_token, str) or not secrets.compare_digest(stored_token, guard_token):
        pytest.fail(
            "refusing destructive migration test: database ownership guard does not match",
            pytrace=False,
        )


def test_destructive_postgres_gate_rejects_remote_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUN_ENV, "1")
    monkeypatch.setenv(
        DSN_ENV,
        "postgresql://postgres:secret@db.production.example/production",
    )

    with pytest.raises(pytest.fail.Exception, match="127.0.0.1"):
        _test_dsn()


def test_destructive_postgres_gate_rejects_wrong_local_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RUN_ENV, "1")
    monkeypatch.setenv(
        DSN_ENV,
        "postgresql://postgres:secret@127.0.0.1:5432/production",
    )

    with pytest.raises(pytest.fail.Exception, match="reader_test"):
        _test_dsn()


def test_destructive_postgres_gate_rejects_dsn_connection_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RUN_ENV, "1")
    monkeypatch.setenv(
        DSN_ENV,
        "postgresql://postgres:secret@127.0.0.1:5432/reader_test?host=production.example",
    )

    with pytest.raises(pytest.fail.Exception, match="query parameters"):
        _test_dsn()


def test_destructive_postgres_gate_requires_script_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(GUARD_ENV, raising=False)

    with pytest.raises(pytest.fail.Exception, match=GUARD_ENV):
        _test_guard_token()


@pytest.mark.asyncio
async def test_destructive_postgres_gate_rejects_mismatched_database_guard() -> None:
    connection = AsyncMock()
    connection.fetchval.return_value = "0" * 32

    with pytest.raises(pytest.fail.Exception, match="does not match"):
        await _assert_disposable_database(connection, "1" * 32)


def _alembic_environment(dsn: str) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("APP_")}
    environment.update(
        {
            "APP_DATABASE_SSL": "false",
            "APP_DATABASE_URL": dsn,
            "READER_ALEMBIC_DISABLE_DOTENV": "1",
        }
    )
    return environment


def _run_alembic(dsn: str, *arguments: str, capture_sql: bool = False) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=_alembic_environment(dsn),
        check=True,
        capture_output=capture_sql,
        text=True,
        timeout=120,
    )
    return completed.stdout if capture_sql else ""


def _expected_engine_fingerprint() -> str:
    payload = {
        "engine_id": "供应商:模型😀",
        "glossary_version": "术语",
        "model": "模型😀",
        "prompt_version": "提示-vé",
        "provider": "供应商",
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_baseline_migration_installs_the_runtime_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh database reaches the runtime contract and its transactional rules hold."""
    dsn = _test_dsn()
    guard_token = _test_guard_token()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    settings = Settings(
        _env_file=None,
        server_id="reader-migration-test",
        database_url=dsn,
        server_access_token="test-connection",
        auth_jwt_secret="test-jwt-signing-secret-at-least-32-characters",
    )
    try:
        await _assert_disposable_database(connection, guard_token)
        await connection.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        assert await runtime_readiness_failures(settings, engine) == [
            "database_or_schema_unavailable"
        ]

        _run_alembic(dsn, "upgrade", "head")

        assert (
            await connection.fetchval("SELECT version_num FROM alembic_version")
            == REQUIRED_SCHEMA_REVISION
        )
        assert await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM app_schema_contracts WHERE contract = $1)",
            REQUIRED_SCHEMA_CONTRACT,
        )
        assert await runtime_readiness_failures(settings, engine) == []
        await _assert_ranking_worker_contract(dsn, connection, monkeypatch)
        await _assert_concurrent_idempotency_contract(dsn, connection)
        await _assert_translation_quota_contract(dsn, connection)
    finally:
        await engine.dispose()
        await connection.close()


async def _assert_concurrent_idempotency_contract(
    dsn: str,
    connection: asyncpg.Connection,
) -> None:
    """Exercise repeated user writes through independent real transactions."""
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    session_factory = build_session_factory(engine)
    user_id = uuid4()
    identity = canonical_rss_identity(f"https://example.com/{uuid4()}.xml")
    await connection.execute(
        "INSERT INTO profiles (id) VALUES ($1)",
        user_id,
    )
    # Simulate a legacy Auth user whose profile trigger did not run. The two
    # requests below must safely repair the same missing row.
    await connection.execute("DELETE FROM profiles WHERE id = $1", user_id)

    async def subscribe(folder_name: str):
        async with session_factory() as session:
            return await subscribe_to_source(
                session,
                user_id=user_id,
                identity=identity,
                display_name="Concurrent source",
                folder_name=folder_name,
            )

    try:
        subscriptions = await asyncio.gather(subscribe("first"), subscribe("second"))
        source_id = subscriptions[0][0].id
        assert {result[0].id for result in subscriptions} == {source_id}
        assert {result[1].id for result in subscriptions} == {subscriptions[0][1].id}
        assert sum(result[3] for result in subscriptions) == 1
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM source_subscriptions WHERE user_id = $1 AND source_id = $2",
                user_id,
                source_id,
            )
            == 1
        )

        content_id = uuid4()
        entry_id = uuid4()
        canonical_url = f"https://example.com/articles/{content_id}"
        await connection.execute(
            """
            INSERT INTO contents (
                id, authority_source_id, kind, canonical_url, url_hash, title,
                body_html, body_text, extraction_status
            ) VALUES ($1, $2, 'article', $3, $4, 'Concurrent Needle',
                      '<p>Needle body</p>', 'Needle body', 'success')
            """,
            content_id,
            source_id,
            canonical_url,
            hashlib.sha256(canonical_url.encode()).hexdigest(),
        )
        await connection.execute(
            """
            INSERT INTO source_entries (
                id, source_id, content_id, native_id, external_url, title,
                raw_metadata, feed_sort_at
            ) VALUES ($1, $2, $3, $4, $5, 'Concurrent Needle', '{}', now())
            """,
            entry_id,
            source_id,
            content_id,
            f"native-{content_id}",
            canonical_url,
        )
        current_user = AuthenticatedUser(id=user_id, email=None, claims={})

        async def save_once():
            async with session_factory() as session:
                return await save_content(content_id, current_user=current_user, session=session)

        await asyncio.gather(save_once(), save_once())
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM user_saved_contents WHERE user_id = $1 AND content_id = $2",
                user_id,
                content_id,
            )
            == 1
        )

        async def update_preference(payload: UpdateTranslationPreferenceRequest):
            async with session_factory() as session:
                return await update_translation_preference(
                    payload,
                    current_user=current_user,
                    session=session,
                )

        await asyncio.gather(
            update_preference(UpdateTranslationPreferenceRequest(target_locale="ja")),
            update_preference(UpdateTranslationPreferenceRequest(is_enabled=False)),
        )
        preference = await connection.fetchrow(
            "SELECT target_locale, is_enabled FROM user_translation_preferences WHERE user_id = $1",
            user_id,
        )
        assert preference is not None
        assert preference["target_locale"] == "ja"
        assert preference["is_enabled"] is False

        async with session_factory() as session:
            search_items, next_cursor = await _query_saved_page(
                session,
                current_user=current_user,
                limit=20,
                cursor=None,
                query="Needle",
            )
        assert [item.content_id for item in search_items] == [str(content_id)]
        assert next_cursor is None
    finally:
        await engine.dispose()


async def _assert_translation_quota_contract(
    dsn: str,
    connection: asyncpg.Connection,
) -> None:
    """Exercise quota stores, override transactions, and the real CLI on PG."""
    async_url = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    users = [uuid4() for _ in range(8)]
    await connection.executemany(
        "INSERT INTO profiles (id) VALUES ($1)",
        [(user_id,) for user_id in users],
    )

    concurrent_settings = Settings(
        _env_file=None,
        database_url=dsn,
        database_ssl=False,
        translation_quota_requests_per_minute=1,
        translation_quota_user_miss_chars_per_minute=100,
        translation_quota_global_miss_chars_per_minute=100,
    )
    broad_settings = Settings(
        _env_file=None,
        database_url=dsn,
        database_ssl=False,
        translation_quota_requests_per_minute=30,
        translation_quota_user_miss_chars_per_minute=120_000,
        translation_quota_global_miss_chars_per_minute=3,
    )
    override_settings = Settings(
        _env_file=None,
        database_url=dsn,
        database_ssl=False,
        translation_quota_requests_per_minute=30,
        translation_quota_user_miss_chars_per_minute=120_000,
        translation_quota_global_miss_chars_per_minute=100,
    )
    engines: list = []
    try:
        # Two independently pooled stores serialize a shared one-request
        # window. One succeeds and the other receives the stable request code.
        concurrent_engine_a = create_async_engine(async_url, pool_size=1, max_overflow=0)
        concurrent_engine_b = create_async_engine(async_url, pool_size=1, max_overflow=0)
        engines.extend((concurrent_engine_a, concurrent_engine_b))
        store_a = TranslationQuotaStore(
            build_session_factory(concurrent_engine_a), concurrent_settings
        )
        store_b = TranslationQuotaStore(
            build_session_factory(concurrent_engine_b), concurrent_settings
        )
        concurrent_results = await asyncio.gather(
            store_a.reserve(users[0], requests=1, actual_miss_chars=1, provider_miss_chars=1),
            store_b.reserve(users[0], requests=1, actual_miss_chars=1, provider_miss_chars=1),
            return_exceptions=True,
        )
        assert (
            sum(isinstance(result, TranslationQuotaExceeded) for result in concurrent_results) == 1
        )
        assert sum(not isinstance(result, Exception) for result in concurrent_results) == 1
        failed = next(result for result in concurrent_results if isinstance(result, Exception))
        assert isinstance(failed, TranslationQuotaExceeded)
        assert failed.code == "translation_quota_requests_exceeded"
        counters = await connection.fetchrow(
            """
            SELECT requests_used, actual_miss_chars_used
            FROM translation_quota_buckets
            WHERE bucket_key = $1
            """,
            f"user:{users[0]}",
        )
        assert counters is not None
        assert counters["requests_used"] == 1
        assert counters["actual_miss_chars_used"] == 1

        # A provider-global cap rejects a mixed reservation atomically, while
        # a pure cache hit still reserves its request dimension successfully.
        broad_engine_a = create_async_engine(async_url, pool_size=1, max_overflow=0)
        broad_engine_b = create_async_engine(async_url, pool_size=1, max_overflow=0)
        engines.extend((broad_engine_a, broad_engine_b))
        broad_store_a = TranslationQuotaStore(build_session_factory(broad_engine_a), broad_settings)
        broad_store_b = TranslationQuotaStore(build_session_factory(broad_engine_b), broad_settings)
        await broad_store_a.reserve(users[1], actual_miss_chars=2, provider_miss_chars=2)
        with pytest.raises(TranslationQuotaExceeded) as global_error:
            await broad_store_b.reserve(
                users[2], requests=1, actual_miss_chars=1, provider_miss_chars=1
            )
        assert global_error.value.code == TRANSLATION_QUOTA_GLOBAL_CHARS_EXCEEDED
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM translation_quota_buckets WHERE bucket_key = $1",
                f"user:{users[2]}",
            )
            == 0
        )
        await broad_store_b.reserve(users[2], requests=1)
        global_counter = await connection.fetchrow(
            "SELECT actual_miss_chars_used FROM translation_quota_buckets "
            "WHERE bucket_key = 'global'"
        )
        assert global_counter["actual_miss_chars_used"] == 3

        # Admin mutations use two independent expire-on-commit sessions. The
        # first upsert is serialized and both returned payloads are primitives.
        admin_factory_a = async_sessionmaker(broad_engine_a, expire_on_commit=True)
        admin_factory_b = async_sessionmaker(broad_engine_b, expire_on_commit=True)
        admin_a = TranslationQuotaAdminService(admin_factory_a)
        admin_b = TranslationQuotaAdminService(admin_factory_b)
        admin_results = await asyncio.gather(
            admin_a.set(
                users[3],
                requests=5,
                actual_miss_chars=3,
                actor="operator-a",
                reason="first-upsert",
                reference="INC-100",
            ),
            admin_b.set(
                users[3],
                requests=None,
                actual_miss_chars=10,
                actor="operator-b",
                reason="second-upsert",
                reference="INC-101",
            ),
        )
        assert all(isinstance(result["user_id"], str) for result in admin_results)
        override_row = await connection.fetchrow(
            """
            SELECT requests, actual_miss_chars, is_disabled
            FROM translation_quota_overrides WHERE user_id = $1
            """,
            users[3],
        )
        assert override_row["requests"] == 5
        assert override_row["actual_miss_chars"] in {3, 10}
        assert override_row["is_disabled"] is False
        await admin_a.set(
            users[3],
            requests=None,
            actual_miss_chars=3,
            actor="operator-a",
            reason="normalise-limit",
        )

        override_store = TranslationQuotaStore(
            build_session_factory(broad_engine_a), override_settings
        )
        await override_store.reserve(users[3], actual_miss_chars=2, provider_miss_chars=2)
        await admin_a.set(
            users[3],
            requests=None,
            actual_miss_chars=1,
            actor="operator-a",
            reason="lower-limit",
        )
        with pytest.raises(TranslationQuotaExceeded) as lowered_error:
            await override_store.reserve(users[3], actual_miss_chars=2, provider_miss_chars=2)
        assert lowered_error.value.code == TRANSLATION_QUOTA_USER_CHARS_EXCEEDED
        await admin_a.set(
            users[3],
            requests=None,
            actual_miss_chars=10,
            actor="operator-a",
            reason="raise-limit",
        )
        await override_store.reserve(users[3], actual_miss_chars=2, provider_miss_chars=2)

        # Disable and reserve compete on the same locked override row. Whatever
        # wins, a subsequent disabled request cannot consume a counter.
        before_disable = await connection.fetchrow(
            """
            SELECT requests_used, actual_miss_chars_used
            FROM translation_quota_buckets WHERE bucket_key = $1
            """,
            f"user:{users[3]}",
        )
        race_results = await asyncio.gather(
            admin_b.disable(
                users[3], actor="operator-b", reason="disable-race", reference="INC-102"
            ),
            override_store.reserve(
                users[3], requests=1, actual_miss_chars=1, provider_miss_chars=1
            ),
            return_exceptions=True,
        )
        assert any(
            isinstance(result, TranslationDisabledForUser) for result in race_results
        ) or any(isinstance(result, dict) for result in race_results)
        assert (
            await connection.fetchval(
                "SELECT is_disabled FROM translation_quota_overrides WHERE user_id = $1",
                users[3],
            )
            is True
        )
        with pytest.raises(TranslationDisabledForUser) as disabled_error:
            await override_store.reserve(
                users[3], requests=1, actual_miss_chars=1, provider_miss_chars=1
            )
        assert disabled_error.value.code == "translation_disabled_for_user"
        after_disabled = await connection.fetchrow(
            """
            SELECT requests_used, actual_miss_chars_used
            FROM translation_quota_buckets WHERE bucket_key = $1
            """,
            f"user:{users[3]}",
        )
        assert after_disabled == before_disable or (
            after_disabled["requests_used"] >= before_disable["requests_used"]
            and after_disabled["actual_miss_chars_used"] >= before_disable["actual_miss_chars_used"]
        )

        await admin_a.enable(users[3], actor="operator-a", reason="enable-after-test")
        delete_result = await admin_a.delete(
            users[3], actor="operator-a", reason="delete-after-test", reference="INC-103"
        )
        assert delete_result == {"user_id": str(users[3]), "deleted": True}
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM translation_quota_overrides WHERE user_id = $1",
                users[3],
            )
            == 0
        )

        audit_rows = await connection.fetch(
            """
            SELECT action, actor, reason, reference, before_state, after_state
            FROM translation_quota_audits
            WHERE user_id = $1 ORDER BY created_at, id
            """,
            users[3],
        )
        assert {row["action"] for row in audit_rows} >= {
            "set",
            "disable",
            "enable",
            "delete",
        }
        for row in audit_rows:
            for state_name in ("before_state", "after_state"):
                state = row[state_name]
                if state is not None:
                    if isinstance(state, str):
                        state = json.loads(state)
                    assert set(state) == {"requests", "actual_miss_chars", "is_disabled"}
        with pytest.raises(asyncpg.PostgresError):
            await connection.execute(
                "UPDATE translation_quota_audits SET reason = 'tampered' WHERE user_id = $1",
                users[3],
            )
        with pytest.raises(asyncpg.PostgresError):
            await connection.execute(
                """
                INSERT INTO translation_quota_audits (id, user_id, actor, reason, action)
                VALUES ($1, $2, 'Bearer secret', 'support', 'set')
                """,
                uuid4(),
                users[3],
            )

        # A disconnected quota store returns the stable 503 class and does
        # not expose connection details.
        outage_engine = create_async_engine(
            "postgresql+asyncpg://postgres:reader-test@127.0.0.1:1/reader_test",
            connect_args={"timeout": 1},
            pool_size=1,
            max_overflow=0,
        )
        try:
            outage_store = TranslationQuotaStore(
                build_session_factory(outage_engine), broad_settings
            )
            with pytest.raises(TranslationQuotaStorageUnavailable) as outage_error:
                await outage_store.reserve(users[4], requests=1)
            assert outage_error.value.code == TRANSLATION_QUOTA_STORAGE_UNAVAILABLE
            assert "127.0.0.1" not in str(outage_error.value)
        finally:
            await outage_engine.dispose()

        # Exercise every production CLI operation as a subprocess. The DSN is
        # supplied only through the environment, never through argv.
        cli_user = users[5]
        assert _run_translation_quota_cli(dsn, "show", str(cli_user)) is None
        set_payload = _run_translation_quota_cli(
            dsn,
            "set",
            str(cli_user),
            "--requests",
            "4",
            "--actual-miss-chars",
            "40",
            "--actor",
            "cli-test",
            "--reason",
            "cli-set",
            "--reference",
            "INC-CLI",
        )
        assert set_payload["requests"] == 4
        assert str(cli_user) in {row["user_id"] for row in _run_translation_quota_cli(dsn, "list")}
        _run_translation_quota_cli(
            dsn,
            "disable",
            str(cli_user),
            "--actor",
            "cli-test",
            "--reason",
            "cli-disable",
        )
        _run_translation_quota_cli(
            dsn,
            "enable",
            str(cli_user),
            "--actor",
            "cli-test",
            "--reason",
            "cli-enable",
        )
        cli_delete = _run_translation_quota_cli(
            dsn,
            "delete",
            str(cli_user),
            "--actor",
            "cli-test",
            "--reason",
            "cli-delete",
        )
        assert cli_delete == {"deleted": True, "user_id": str(cli_user)}
        assert _run_translation_quota_cli(dsn, "show", str(cli_user)) is None
    finally:
        for engine in engines:
            await engine.dispose()


def _run_translation_quota_cli(dsn: str, *arguments: str):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("APP_")}
    environment.update(
        {
            "APP_DATABASE_URL": dsn,
            "APP_DATABASE_SSL": "false",
            "READER_ALEMBIC_DISABLE_DOTENV": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "app.admin.cli", "translation-quota", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert dsn not in completed.stdout
    assert dsn not in completed.stderr
    return json.loads(completed.stdout)


async def _assert_ranking_worker_contract(
    dsn: str,
    connection: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The API creates core board rows on first view; seed the one this checks.
    await connection.execute(
        """
        INSERT INTO ranking_snapshots (
            cache_key, kind, parameters, payload, fetched_at,
            next_refresh_at, last_accessed_at, is_ready, last_error
        ) VALUES (
            'ranking:v2:hacker_news', 'hacker_news', '{}',
            '{"title":"Hacker News","subtitle":"","items":[]}',
            now(), now(), now(), true, NULL
        ) ON CONFLICT (cache_key) DO NOTHING
        """
    )
    await connection.execute(
        """
        UPDATE ranking_snapshots
        SET next_refresh_at = now() + interval '1 day',
            last_accessed_at = now();

        UPDATE ranking_snapshots
        SET next_refresh_at = now() - interval '1 minute'
        WHERE cache_key = 'ranking:v2:hacker_news';

        INSERT INTO ranking_snapshots (
            cache_key, kind, parameters, payload, fetched_at,
            next_refresh_at, last_accessed_at, is_ready, last_error
        ) VALUES
        (
            'ranking:v2:reddit:machinelearning:top:day', 'reddit',
            '{"subreddit":"machinelearning","sort":"top","time_filter":"day"}',
            '{"title":"Cold","subtitle":"","items":[]}',
            now(), now() - interval '1 minute', now() - interval '8 days', true, NULL
        );
        """
    )

    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    refresh = AsyncMock()
    monkeypatch.setattr(ranking_snapshots, "create_or_refresh_snapshot", refresh)
    monkeypatch.setattr(
        ranking_snapshots,
        "get_settings",
        lambda: SimpleNamespace(ranking_snapshot_inactive_ttl_days=7),
    )
    try:
        async with build_session_factory(engine)() as session:
            await ranking_snapshots.sync_due_ranking_snapshots(session)
    finally:
        await engine.dispose()

    refreshed_requests = [call.args[1] for call in refresh.await_args_list]
    assert any(request.cache_key == "ranking:v2:hacker_news" for request in refreshed_requests)
    assert all(request.kind != "reddit" for request in refreshed_requests)
    assert {
        record["cache_key"]
        for record in await connection.fetch(
            "SELECT cache_key FROM ranking_snapshots WHERE kind = 'github'"
        )
    } >= {
        "ranking:v2:github",
        "ranking:v2:github:daily",
        "ranking:v2:github:monthly",
    }
    assert (
        await connection.fetchval(
            """
            SELECT count(*)
            FROM ranking_snapshots
            WHERE cache_key = 'ranking:v2:reddit:machinelearning:top:day'
            """
        )
        == 0
    )
    scheduler_sql = next(
        statement
        for statement in statements
        if statement.lstrip().startswith("SELECT ranking_snapshots.cache_key")
        and "ranking_snapshots.cache_key LIKE" in statement
    )
    assert "ranking_snapshots.payload" not in scheduler_sql.partition("FROM")[0]
