from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.admin.cli import build_parser, main
from app.admin.translation_quota import (
    TranslationQuotaAdminError,
    _validate_mutation_metadata,
)
from app.core.settings import Settings
from app.translation.quota import (
    TRANSLATION_QUOTA_GLOBAL_CHARS_EXCEEDED,
    TRANSLATION_QUOTA_USER_CHARS_EXCEEDED,
    TranslationQuotaExceeded,
    TranslationQuotaStore,
    _effective_limits,
    _fixed_window_start,
)


def test_quota_defaults_and_override_precedence_are_finite() -> None:
    settings = Settings(
        _env_file=None,
        translation_quota_requests_per_minute=30,
        translation_quota_user_miss_chars_per_minute=120_000,
        translation_quota_global_miss_chars_per_minute=500_000,
    )
    assert _effective_limits(settings, None).requests == 30
    assert _effective_limits(settings, None).actual_miss_chars == 120_000

    override = SimpleNamespace(requests=7, actual_miss_chars=9_000, is_disabled=False)
    effective = _effective_limits(settings, override)
    assert effective.requests == 7
    assert effective.actual_miss_chars == 9_000
    assert not effective.is_disabled
    assert _effective_limits(
        settings,
        SimpleNamespace(requests=999_999, actual_miss_chars=999_999, is_disabled=True),
    ).is_disabled


class _QuotaSession:
    def __init__(self, values) -> None:
        self.values = list(values)
        self.execute = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def scalar(self, _statement):
        return self.values.pop(0)


def _factory(session):
    @asynccontextmanager
    async def create():
        yield session

    return create


@pytest.mark.asyncio
async def test_mixed_user_global_reservation_is_atomic_on_user_limit_failure() -> None:
    user_id = uuid4()
    user_bucket = SimpleNamespace(
        window_started_at=_fixed_window_start(datetime.now(UTC)),
        requests_used=0,
        actual_miss_chars_used=120_000,
    )
    global_bucket = SimpleNamespace(
        window_started_at=user_bucket.window_started_at,
        requests_used=0,
        actual_miss_chars_used=0,
    )
    session = _QuotaSession([None, user_bucket, global_bucket])
    settings = Settings(_env_file=None, translation_quota_user_miss_chars_per_minute=120_000)
    store = TranslationQuotaStore(_factory(session), settings)

    with pytest.raises(TranslationQuotaExceeded) as error:
        await store.reserve(
            user_id,
            requests=1,
            actual_miss_chars=1,
            provider_miss_chars=1,
        )

    assert error.value.code == TRANSLATION_QUOTA_USER_CHARS_EXCEEDED
    assert user_bucket.actual_miss_chars_used == 120_000
    assert global_bucket.actual_miss_chars_used == 0
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_user_global_reservation_is_atomic_on_global_limit_failure() -> None:
    user_id = uuid4()
    now = _fixed_window_start(datetime.now(UTC))
    user_bucket = SimpleNamespace(
        window_started_at=now,
        requests_used=0,
        actual_miss_chars_used=0,
    )
    global_bucket = SimpleNamespace(
        window_started_at=now,
        requests_used=0,
        actual_miss_chars_used=500_000,
    )
    session = _QuotaSession([None, user_bucket, global_bucket])
    settings = Settings(_env_file=None)
    store = TranslationQuotaStore(_factory(session), settings)

    with pytest.raises(TranslationQuotaExceeded) as error:
        await store.reserve(
            user_id,
            actual_miss_chars=1,
            provider_miss_chars=1,
        )

    assert error.value.code == TRANSLATION_QUOTA_GLOBAL_CHARS_EXCEEDED
    assert user_bucket.actual_miss_chars_used == 0
    assert global_bucket.actual_miss_chars_used == 500_000
    session.rollback.assert_awaited_once()


def test_translation_quota_cli_requires_actor_and_reason_for_mutations() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["translation-quota", "disable", str(uuid4())])

    args = parser.parse_args(
        [
            "translation-quota",
            "set",
            "--user-id",
            str(uuid4()),
            "--requests",
            "10",
            "--actor",
            "operator",
            "--reason",
            "support",
        ]
    )
    assert args.requests == 10
    assert args.actor == "operator"
    assert args.reason == "support"
    assert args.reference is None


def test_translation_quota_cli_examples_use_production_parser_and_validator() -> None:
    user_id = str(uuid4())
    commands = [
        ["translation-quota", "show", user_id],
        [
            "translation-quota",
            "set",
            user_id,
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
            user_id,
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
            user_id,
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
            user_id,
            "--actor",
            "operator@example.com",
            "--reason",
            "remove_temporary_override",
            "--reference",
            "CHG-QUOTA-012",
        ],
    ]
    expected_reasons = {
        "set": "temporary_support_allowance",
        "disable": "abuse_mitigation",
        "enable": "mitigation_complete",
        "delete": "remove_temporary_override",
    }
    parser = build_parser()
    for argv in commands:
        args = parser.parse_args(argv)
        if args.action == "show":
            assert args.action == "show"
            continue
        assert args.reason == expected_reasons[args.action]
        validated = _validate_mutation_metadata(args.actor, args.reason, args.reference)
        assert validated == (args.actor, args.reason, args.reference)


def test_quota_cli_rejects_spaced_reason_with_exit_two(monkeypatch, capsys) -> None:
    monkeypatch.setenv(
        "APP_DATABASE_URL",
        "postgresql://postgres:reader-test@127.0.0.1:5432/reader_test",
    )
    exit_code = main(
        [
            "translation-quota",
            "set",
            str(uuid4()),
            "--requests",
            "1",
            "--actor",
            "operator",
            "--reason",
            "support reason",
        ]
    )
    assert exit_code == 2
    assert "controlled code" in capsys.readouterr().err


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor", "postgresql://db.example/reader"),
        ("actor", "Bearer eyJhbGciOiJIUzI1NiJ9"),
        ("actor", "jwt-token"),
        ("actor", "sk-live-secret"),
        ("actor", "sb_secret_test"),
        ("actor", "operator\nadmin"),
        ("reason", "Bearer token"),
        ("reason", "jwt-secret"),
        ("reason", "support reason"),
        ("reference", "https://incident.example/123"),
        ("reference", "sb_publishable_test"),
        ("reference", "incident\t42"),
    ],
)
def test_quota_mutation_metadata_rejects_unsafe_values_without_echo(
    field: str,
    value: str,
) -> None:
    values = {"actor": "operator", "reason": "support", "reference": None}
    values[field] = value
    with pytest.raises(TranslationQuotaAdminError) as raised:
        _validate_mutation_metadata(**values)
    assert value not in str(raised.value)


def test_quota_cli_uuid_parse_error_does_not_echo_credential_material(capsys) -> None:
    parser = build_parser()
    secret = "postgresql://postgres:secret@db.example/production"
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "translation-quota",
                "show",
                secret,
            ]
        )
    assert secret not in capsys.readouterr().err


@pytest.mark.asyncio
async def test_allowed_check_rejects_disabled_without_loading_buckets():
    from app.translation.quota import TranslationDisabledForUser

    session = _QuotaSession([SimpleNamespace(is_disabled=True)])
    store = TranslationQuotaStore(_factory(session), Settings(_env_file=None))
    with pytest.raises(TranslationDisabledForUser):
        await store.check_allowed(uuid4())
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowed_check_does_not_charge_or_load_buckets():
    session = _QuotaSession([None])
    store = TranslationQuotaStore(_factory(session), Settings(_env_file=None))
    await store.check_allowed(uuid4())
    session.execute.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_does_not_reset_counters_before_all_locks_are_acquired():
    from app.translation.quota import _lock_bucket

    bucket = SimpleNamespace(
        window_started_at=datetime(2026, 9, 6, 12, 1, tzinfo=UTC),
        requests_used=7,
        actual_miss_chars_used=1000,
    )
    session = _QuotaSession([bucket])
    result = await _lock_bucket(
        session,
        bucket_key="global",
        user_id=None,
        window_start=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
    )
    assert result.window_started_at.minute == 1
    assert result.requests_used == 7
    assert result.actual_miss_chars_used == 1000
