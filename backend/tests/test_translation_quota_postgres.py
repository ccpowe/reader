"""Disposable-Postgres coverage for the production translation-quota CLI."""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest
from test_postgres_migrations import (
    _assert_disposable_database,
    _run_translation_quota_cli,
    _test_dsn,
    _test_guard_token,
)

from app.admin.cli import build_parser


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_translation_quota_commands_run_through_production_cli() -> None:
    """Run every mutating example as a real CLI subprocess on PostgreSQL."""
    dsn = _test_dsn()
    guard_token = _test_guard_token()
    connection = await asyncpg.connect(dsn)
    user_id = uuid4()
    try:
        await _assert_disposable_database(connection, guard_token)
        await connection.execute(
            "INSERT INTO profiles (id) VALUES ($1)",
            user_id,
        )

        commands = [
            ["translation-quota", "show", str(user_id)],
            [
                "translation-quota",
                "set",
                str(user_id),
                "--requests",
                "60",
                "--actual-miss-chars",
                "240000",
                "--actor",
                "operator@example.com",
                "--reason",
                "temporary_support_allowance",
                "--reference",
                "INC-SUPPORT-123",
            ],
            [
                "translation-quota",
                "disable",
                str(user_id),
                "--actor",
                "operator@example.com",
                "--reason",
                "abuse_mitigation",
                "--reference",
                "INC-ABUSE-456",
            ],
            [
                "translation-quota",
                "enable",
                str(user_id),
                "--actor",
                "operator@example.com",
                "--reason",
                "mitigation_complete",
                "--reference",
                "INC-MITIGATION-789",
            ],
            [
                "translation-quota",
                "delete",
                str(user_id),
                "--actor",
                "operator@example.com",
                "--reason",
                "remove_temporary_override",
                "--reference",
                "CHG-QUOTA-012",
            ],
        ]
        parser = build_parser()
        parsed_commands = [parser.parse_args(argv) for argv in commands]
        assert [args.action for args in parsed_commands] == [
            "show",
            "set",
            "disable",
            "enable",
            "delete",
        ]
        results = {
            args.action: _run_translation_quota_cli(dsn, *argv[1:])
            for args, argv in zip(parsed_commands, commands, strict=True)
        }
        assert results["show"] is None
        assert results["set"]["requests"] == parsed_commands[1].requests
        assert results["delete"] == {"deleted": True, "user_id": str(user_id)}
        assert _run_translation_quota_cli(dsn, *commands[0][1:]) is None

        audit_rows = await connection.fetch(
            """
            SELECT action, reason, reference
            FROM translation_quota_audits
            WHERE user_id = $1
            ORDER BY created_at, id
            """,
            user_id,
        )
        assert [(row["action"], row["reason"], row["reference"]) for row in audit_rows] == [
            (args.action, args.reason, args.reference)
            for args in parsed_commands
            if args.action != "show"
        ]
    finally:
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_quota_waiting_request_uses_same_forward_window_for_both_buckets(monkeypatch):
    """A request blocked on its user row must retain a newer global charge."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.settings import Settings
    from app.translation import quota as quota_module

    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://", 1),
        connect_args={"server_settings": {"application_name": "quota-window-regression"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    old_user, new_user = uuid4(), uuid4()
    old_window = datetime.now(UTC).replace(second=0, microsecond=0)
    next_window = old_window + timedelta(minutes=1)

    class Clock:
        value = old_window

        @classmethod
        def now(cls, tz):
            return cls.value

        fromtimestamp = staticmethod(datetime.fromtimestamp)

    task = None
    lock_transaction = None
    previous_global = None
    guarded = False
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        guarded = True
        previous_global = await connection.fetchrow(
            "SELECT * FROM translation_quota_buckets WHERE bucket_key = 'global'"
        )
        for user in (old_user, new_user):
            await connection.execute(
                "INSERT INTO profiles (id) VALUES ($1)",
                user,
            )
        await connection.execute(
            """INSERT INTO translation_quota_buckets
                (bucket_key, user_id, window_started_at, requests_used, actual_miss_chars_used)
                VALUES ($1, $2, $3, 1, 77)""",
            f"user:{old_user}",
            old_user,
            old_window,
        )
        await connection.execute(
            """INSERT INTO translation_quota_buckets
                (bucket_key, window_started_at, requests_used, actual_miss_chars_used)
                VALUES ('global', $1, 0, 900)
                ON CONFLICT (bucket_key) DO UPDATE SET
                    window_started_at = $1, requests_used = 0, actual_miss_chars_used = 900""",
            old_window,
        )
        lock_transaction = connection.transaction()
        await lock_transaction.start()
        await connection.fetchrow(
            "SELECT * FROM translation_quota_buckets WHERE bucket_key = $1 FOR UPDATE",
            f"user:{old_user}",
        )
        monkeypatch.setattr(quota_module, "datetime", Clock)
        store = quota_module.TranslationQuotaStore(
            factory,
            Settings(
                _env_file=None,
                translation_quota_global_miss_chars_per_minute=1000,
            ),
        )
        task = asyncio.create_task(store.reserve_miss(old_user, actual_miss_chars=100))

        # Observe an actual PostgreSQL lock wait, not merely task scheduling.
        async def wait_for_lock():
            while True:
                await connection.execute("SELECT pg_stat_clear_snapshot()")
                if await connection.fetchval(
                    """SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                   WHERE application_name = 'quota-window-regression'
                   AND cardinality(pg_blocking_pids(pid)) > 0)"""
                ):
                    return
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_lock(), 3)
        Clock.value = next_window
        await store.reserve_miss(new_user, actual_miss_chars=100)
        await lock_transaction.commit()
        lock_transaction = None
        await asyncio.wait_for(task, 3)
        rows = await connection.fetch(
            """SELECT bucket_key, window_started_at, actual_miss_chars_used
               FROM translation_quota_buckets WHERE bucket_key = ANY($1::text[])""",
            ["global", f"user:{old_user}", f"user:{new_user}"],
        )
        assert len(rows) == 3
        assert all(row["window_started_at"] == next_window for row in rows)
        assert {row["bucket_key"]: row["actual_miss_chars_used"] for row in rows} == {
            "global": 200,
            f"user:{old_user}": 100,
            f"user:{new_user}": 100,
        }
        with pytest.raises(quota_module.TranslationQuotaExceeded) as raised:
            await store.reserve_miss(old_user, actual_miss_chars=801)
        assert raised.value.code == quota_module.TRANSLATION_QUOTA_GLOBAL_CHARS_EXCEEDED
        assert raised.value.retry_after_seconds == 60
    finally:
        if lock_transaction is not None:
            await lock_transaction.rollback()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if guarded:
            await connection.execute(
                "DELETE FROM translation_quota_buckets WHERE bucket_key = ANY($1::text[])",
                ["global", f"user:{old_user}", f"user:{new_user}"],
            )
            if previous_global is not None:
                await connection.execute(
                    """INSERT INTO translation_quota_buckets
                       (bucket_key, user_id, window_started_at, requests_used,
                        actual_miss_chars_used, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    *(
                        previous_global[key]
                        for key in (
                            "bucket_key",
                            "user_id",
                            "window_started_at",
                            "requests_used",
                            "actual_miss_chars_used",
                            "updated_at",
                        )
                    ),
                )
        await engine.dispose()
        await connection.close()
