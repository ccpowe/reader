from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from app.core.settings import Settings
from app.translation.engines import engine_descriptor
from app.translation.factory import build_translation_provider
from app.workers.cleanup import obsolete_model_work_statement


@pytest.mark.parametrize("provider", ["deepseek", "openrouter"])
@pytest.mark.parametrize("prefix", ["", "APP_"])
def test_model_environment_aliases(monkeypatch, provider, prefix):
    key = provider.upper() + "_MODEL"
    monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("APP_" + key, raising=False)
    monkeypatch.setenv(prefix + key, "  vendor/future-model  ")
    settings = Settings(_env_file=None)
    assert getattr(settings, provider + "_model") == "vendor/future-model"
    monkeypatch.setenv("APP_" + key, "preferred-model")
    assert getattr(Settings(_env_file=None), provider + "_model") == "preferred-model"


@pytest.mark.parametrize(
    "field", ["deepseek_model", "openrouter_model", "codex_subscription_model"]
)
@pytest.mark.parametrize("value", ["", "   ", "a" * 161])
def test_invalid_model_is_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    "provider,route",
    [
        ("deepseek", "deepseek-v4-flash"),
        ("openrouter", "openrouter-minimax-m3"),
    ],
)
@pytest.mark.asyncio
async def test_model_switch_keeps_route_and_changes_cache_identity_and_sdk(provider, route):
    original = Settings(_env_file=None, **{provider + "_api_key": "test-key"})
    changed = original.model_copy(update={provider + "_model": "vendor/future-model"})
    before = engine_descriptor(original, route)
    after = engine_descriptor(changed, route)
    assert before.engine_id == after.engine_id
    assert before.to_engine().fingerprint != after.to_engine().fingerprint
    assert "vendor/future-model" in after.label
    adapter = build_translation_provider(changed, after)
    try:
        assert adapter.model_name == "vendor/future-model"
        assert adapter._model.model_name == "vendor/future-model"
    finally:
        await adapter.aclose()
    # Switching back recovers the exact original cache identity.
    assert (
        engine_descriptor(original, route).to_engine().fingerprint == before.to_engine().fingerprint
    )


def test_cleanup_cancels_only_obsolete_unleased_work_and_is_bounded():
    database = create_engine("sqlite://")
    now = datetime.now(UTC)
    rows = [
        ("pending-old", "pending", "old-model", None, "openrouter-minimax-m3"),
        (
            "expired-old",
            "running",
            "old-model",
            now - timedelta(minutes=1),
            "openrouter-minimax-m3",
        ),
        ("live-old", "running", "old-model", now + timedelta(minutes=5), "openrouter-minimax-m3"),
        ("success-old", "succeeded", "old-model", None, "openrouter-minimax-m3"),
        ("failed-old", "failed", "old-model", None, "openrouter-minimax-m3"),
        ("current", "pending", "minimax/minimax-m3", None, "openrouter-minimax-m3"),
        ("other", "pending", "old-model", None, "unmanaged"),
    ]
    try:
        with database.begin() as connection:
            connection.execute(
                text("""CREATE TABLE translation_work (
                id VARCHAR PRIMARY KEY, test_label TEXT, engine_id TEXT, model_name TEXT,
                status TEXT, lease_token TEXT, lease_expires_at DATETIME, created_at DATETIME,
                finished_at DATETIME, last_attempt_finished_at DATETIME, error_code TEXT,
                error_message TEXT, error_retryable BOOLEAN, updated_at DATETIME)""")
            )
            for label, status, model, expiry, route in rows:
                connection.execute(
                    text("""INSERT INTO translation_work
                    (id,test_label,engine_id,model_name,status,lease_expires_at,created_at)
                    VALUES (:id,:label,:route,:model,:status,:expiry,:created)"""),
                    dict(
                        id=uuid4().hex,
                        label=label,
                        route=route,
                        model=model,
                        status=status,
                        expiry=expiry.isoformat(sep=" ") if expiry else None,
                        created=now.isoformat(sep=" "),
                    ),
                )
            statement = obsolete_model_work_statement(Settings(_env_file=None), limit=1)
            assert len(connection.execute(statement).all()) == 1
            assert len(connection.execute(statement).all()) == 1
            assert len(connection.execute(statement).all()) == 0
            states = dict(
                connection.execute(text("SELECT test_label,status FROM translation_work")).all()
            )
            assert states == {
                label: "cancelled" if label in {"pending-old", "expired-old"} else status
                for label, status, *_ in rows
            }
    finally:
        database.dispose()
