from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.enums import TranslationProviderMode, TranslationStatus
from app.translation.domain import TranslationEngine, TranslationProjection
from app.translation.projection import (
    TitleTranslationContext,
    get_translation_context,
    invalidate_translation_context,
    translation_response_values,
)


def _context(*, enabled: bool = True) -> TitleTranslationContext:
    return TitleTranslationContext(
        target_locale="zh-CN",
        enabled=enabled,
        engine=(
            TranslationEngine(
                engine_id="deepseek-v4-flash",
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
            )
            if enabled
            else None
        ),
    )


def test_translation_response_uses_only_successful_artifact_text() -> None:
    projection = TranslationProjection(
        item_id="one",
        source_hash="hash",
        target_locale="zh-CN",
        status=TranslationStatus.SUCCEEDED,
        translated_text="  翻译标题  ",
    )

    assert translation_response_values(_context(), projection) == (
        "翻译标题",
        "zh-CN",
        "succeeded",
    )


def test_translation_response_keeps_original_visible_while_pending() -> None:
    projection = TranslationProjection(
        item_id="one",
        source_hash="hash",
        target_locale="zh-CN",
        status=TranslationStatus.PENDING,
        translated_text="partial",
    )

    assert translation_response_values(_context(), projection) == (
        None,
        "zh-CN",
        "pending",
    )
    assert translation_response_values(_context(enabled=False), projection) == (
        None,
        None,
        None,
    )


@pytest.mark.asyncio
async def test_translation_context_reuses_preference_and_can_be_invalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(
        target_locale="ja",
        is_enabled=True,
        provider_mode=TranslationProviderMode.APP_DEFAULT,
        updated_at=None,
    )
    engine = TranslationEngine(
        engine_id="deepseek-v4-flash",
        provider_name="deepseek",
        model_name="deepseek-v4-flash",
    )
    descriptor = SimpleNamespace(
        available=True,
        label="DeepSeek V4 Flash",
        to_engine=lambda: engine,
        unavailable_reason=None,
    )
    monkeypatch.setattr(
        "app.translation.projection.default_engine_descriptor", lambda _settings: descriptor
    )
    invalidate_translation_context(user_id)
    try:
        first = await get_translation_context(session, user_id)
        second = await get_translation_context(session, user_id)

        assert first is second
        assert first.target_locale == "ja"
        session.scalar.assert_awaited_once()

        invalidate_translation_context(user_id)
        await get_translation_context(session, user_id)
        assert session.scalar.await_count == 2
    finally:
        invalidate_translation_context(user_id)
