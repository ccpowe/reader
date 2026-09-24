from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api import translation_preferences
from app.core.auth import AuthenticatedUser
from app.core.settings import Settings
from app.domain.enums import TranslationProviderMode
from app.storage.models import TranslationPreference


def _settings(*, deepseek: bool = True) -> Settings:
    values = {"_env_file": None}
    if deepseek:
        values["APP_DEEPSEEK_API_KEY"] = "deepseek-secret"
    return Settings(**values)


@pytest.mark.parametrize(
    "engine_id,label",
    [
        ("deepseek-v4-flash", "DeepSeek · deepseek-flash"),
        ("openrouter-minimax-m3", "OpenRouter · minimax/minimax-m3"),
    ],
)
@pytest.mark.asyncio
async def test_preference_selects_available_managed_engine_and_cancels_private_old_work(
    monkeypatch: pytest.MonkeyPatch,
    engine_id: str,
    label: str,
) -> None:
    user_id = uuid4()
    preference = TranslationPreference(
        user_id=user_id,
        target_locale="zh-CN",
        provider_mode=TranslationProviderMode.APP_DEFAULT,
        is_enabled=True,
    )
    session = AsyncMock()
    session.get.return_value = preference
    cancelled: list[tuple[object, str | None, str | None]] = []

    async def fake_cancel(_session, *, owner_id, keep_engine_id, keep_target_locale):
        cancelled.append((owner_id, keep_engine_id, keep_target_locale))
        return 2

    monkeypatch.setattr(
        translation_preferences,
        "get_settings",
        lambda: Settings(
            _env_file=None, APP_DEEPSEEK_API_KEY="secret", APP_OPENROUTER_API_KEY="secret"
        ),
    )
    monkeypatch.setattr(translation_preferences, "cancel_stale_user_translation_work", fake_cancel)
    monkeypatch.setattr(translation_preferences, "cache_translation_context", lambda *_args: None)

    response = await translation_preferences.update_translation_preference(
        translation_preferences.UpdateTranslationPreferenceRequest(engine_id=engine_id),
        current_user=AuthenticatedUser(id=user_id, email=None, claims={}),
        session=session,
    )

    assert preference.provider_mode == TranslationProviderMode.APP_MANAGED
    assert preference.engine_id == engine_id
    assert response.effective_engine_id == engine_id
    assert response.effective_engine_label == label
    assert cancelled == [(user_id, engine_id, "zh-CN")]
    lock = str(session.execute.await_args_list[0].args[0].compile(dialect=postgresql.dialect()))
    assert "pg_advisory_xact_lock" in lock
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_preference_rejects_unavailable_engine_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        translation_preferences,
        "get_settings",
        lambda: _settings(deepseek=False),
    )

    with pytest.raises(HTTPException) as error:
        await translation_preferences.update_translation_preference(
            translation_preferences.UpdateTranslationPreferenceRequest(
                engine_id="deepseek-v4-flash"
            ),
            current_user=AuthenticatedUser(id=uuid4(), email=None, claims={}),
            session=AsyncMock(),
        )

    assert error.value.status_code == 422
    assert "unavailable" in str(error.value.detail)


@pytest.mark.asyncio
async def test_preference_rejects_retired_google_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(translation_preferences, "get_settings", _settings)

    with pytest.raises(HTTPException) as error:
        await translation_preferences.update_translation_preference(
            translation_preferences.UpdateTranslationPreferenceRequest(engine_id="google-nmt"),
            current_user=AuthenticatedUser(id=uuid4(), email=None, claims={}),
            session=AsyncMock(),
        )

    assert error.value.status_code == 422
    assert "Unknown managed translation engine" in str(error.value.detail)


@pytest.mark.asyncio
async def test_disabling_translation_cancels_all_pending_private_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    preference = TranslationPreference(
        user_id=user_id,
        target_locale="zh-CN",
        provider_mode=TranslationProviderMode.APP_MANAGED,
        engine_id="deepseek-v4-flash",
        is_enabled=True,
    )
    session = AsyncMock()
    session.get.return_value = preference
    cancelled = []

    async def fake_cancel(_session, **kwargs):
        cancelled.append(kwargs)
        return 1

    monkeypatch.setattr(translation_preferences, "get_settings", lambda: _settings())
    monkeypatch.setattr(translation_preferences, "cancel_stale_user_translation_work", fake_cancel)
    monkeypatch.setattr(translation_preferences, "cache_translation_context", lambda *_args: None)

    await translation_preferences.update_translation_preference(
        translation_preferences.UpdateTranslationPreferenceRequest(is_enabled=False),
        current_user=AuthenticatedUser(id=user_id, email=None, claims={}),
        session=session,
    )

    assert cancelled == [
        {
            "owner_id": user_id,
            "keep_engine_id": None,
            "keep_target_locale": None,
        }
    ]


def test_preference_response_lists_runtime_engine_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        translation_preferences,
        "get_settings",
        _settings,
    )

    response = translation_preferences._to_response(None)

    assert response.effective_engine_id == "deepseek-v4-flash"
    assert response.effective_engine_available is True
    engines = {engine.engine_id: engine for engine in response.managed_engines}
    assert set(engines) == {"deepseek-v4-flash", "openrouter-minimax-m3", "codex-subscription"}
    assert (
        engines["codex-subscription"].unavailable_reason == "missing_codex_subscription_auth_file"
    )
    assert engines["openrouter-minimax-m3"].available is False
    assert engines["deepseek-v4-flash"].available is True
