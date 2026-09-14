from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.api import translations
from app.core.auth import AuthenticatedUser
from app.domain.enums import (
    TranslationPurpose,
    TranslationScope,
    TranslationStatus,
)
from app.translation.domain import (
    TranslationEngine,
    TranslationProjection,
    TranslationProviderError,
)
from app.translation.interactive import InteractiveResolution
from app.translation.projection import TranslationContext
from app.translation.quota import TranslationQuotaExceeded, TranslationQuotaStorageUnavailable


def _segment(
    segment_id: str,
    text: str,
    purpose: str,
) -> dict[str, str | None]:
    return {
        "segment_id": segment_id,
        "text": text,
        "purpose": purpose,
    }


def test_segment_request_rejects_duplicate_ids_and_unsupported_purposes() -> None:
    with pytest.raises(ValidationError, match="segment_id values must be unique"):
        translations.ResolveTranslationSegmentsRequest(
            segments=[
                _segment("same", "First", "paragraph"),
                _segment("same", "Second", "caption"),
            ]
        )

    with pytest.raises(ValidationError, match="unsupported client translation purpose"):
        translations.ResolveTranslationSegmentsRequest(
            segments=[_segment("title", "A content title", "title")]
        )


def test_segment_request_bounds_total_text() -> None:
    with pytest.raises(ValidationError, match="exceeds 30000 characters"):
        translations.ResolveTranslationSegmentsRequest(
            segments=[
                _segment(f"segment-{index}", "x" * 8_000, "web_segment") for index in range(4)
            ]
        )


@pytest.mark.parametrize("route_changed", [False, True])
@pytest.mark.asyncio
async def test_segment_endpoint_scopes_web_text_and_captions_to_current_user(
    monkeypatch: pytest.MonkeyPatch,
    route_changed: bool,
) -> None:
    user_id = uuid4()
    current_user = AuthenticatedUser(id=user_id, email=None, claims={})
    context = TranslationContext(
        target_locale="zh-CN",
        enabled=True,
        engine=TranslationEngine(
            engine_id="deepseek-v4-flash",
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
        ),
        engine_label="DeepSeek V4 Flash",
    )
    captured = []
    reads = 0

    async def fake_context(*_args, **_kwargs):
        nonlocal reads
        reads += 1
        return (
            replace(context, cache_generation="changed") if route_changed and reads > 1 else context
        )

    class FakeRealtimeCoordinator:
        async def resolve(self, items, *, context, provider, user_id, wait_seconds):
            assert session.commit.await_count == 1
            captured.extend(items)
            assert context.target_locale == "zh-CN"
            assert provider is None
            assert user_id == current_user.id
            assert wait_seconds > 0
            return {
                "web:one": TranslationProjection(
                    item_id="web:one",
                    source_hash="web-hash",
                    target_locale="zh-CN",
                    status=TranslationStatus.SUCCEEDED,
                    translated_text="网页译文",
                ),
                "caption:one": TranslationProjection(
                    item_id="caption:one",
                    source_hash="caption-hash",
                    target_locale="zh-CN",
                    status=TranslationStatus.PENDING,
                    error_code="rate_limited",
                    retry_after_ms=750,
                ),
            }

    monkeypatch.setattr(translations, "get_translation_context", fake_context)
    payload = translations.ResolveTranslationSegmentsRequest(
        segments=[
            _segment("web:one", "Private page paragraph", "web_segment"),
            _segment("caption:one", "Public caption cue", "caption"),
        ]
    )

    session = AsyncMock()
    response = await translations.resolve_translation_segments(
        payload,
        request=SimpleNamespace(
            headers={},
            app=SimpleNamespace(
                state=SimpleNamespace(
                    realtime_translation_coordinator=FakeRealtimeCoordinator(),
                    translation_providers={},
                )
            ),
        ),
        background_tasks=BackgroundTasks(),
        current_user=current_user,
        session=session,
    )

    assert session.commit.await_count == 2
    assert captured[0].purpose == TranslationPurpose.WEB_SEGMENT
    assert captured[0].scope == TranslationScope.USER
    assert captured[0].owner_id == user_id
    assert captured[1].purpose == TranslationPurpose.CAPTION
    assert captured[1].scope == TranslationScope.USER
    assert captured[1].owner_id == user_id
    if route_changed:
        assert all(item.translated_text is None for item in response)
        assert all(item.cache_expires_at is None for item in response)
        assert all(item.error_code == "translation_route_changed" for item in response)
        return
    assert response[0].translated_text == "网页译文"
    assert response[0].translation_status == "succeeded"
    assert response[1].translated_text is None
    assert response[1].error_code == "rate_limited"
    assert response[1].retry_after_ms == 750
    assert response[1].engine_id == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_segments_reserve_request_and_only_actual_miss_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    current_user = AuthenticatedUser(id=user_id, email=None, claims={})
    context = TranslationContext(
        target_locale="zh-CN",
        enabled=True,
        engine=TranslationEngine(
            engine_id="deepseek-v4-flash",
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
        ),
        engine_label="DeepSeek V4 Flash",
    )
    quota = SimpleNamespace(reserve=AsyncMock())
    seen_demands: list[object] = []

    async def fake_context(*_args, **_kwargs):
        return context

    async def fake_resolve(_session, items, *, reserve_budget, **_options):
        seen_demands.extend(items)
        await reserve_budget([])
        return InteractiveResolution({})

    monkeypatch.setattr(translations, "get_translation_context", fake_context)
    monkeypatch.setattr(translations, "resolve_interactive_texts", fake_resolve)
    payload = translations.ResolveTranslationSegmentsRequest(
        segments=[
            _segment("cache-hit", "already cached", "paragraph"),
        ]
    )
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                translation_quota=quota,
                translation_providers={},
                session_factory=None,
            )
        ),
    )

    await translations.resolve_translation_segments(
        payload,
        request=request,
        background_tasks=BackgroundTasks(),
        current_user=current_user,
        session=AsyncMock(),
    )

    quota.reserve.assert_awaited_once_with(
        user_id,
        requests=1,
        actual_miss_chars=0,
        provider_miss_chars=0,
    )
    assert seen_demands[0].text == "already cached"


@pytest.mark.asyncio
async def test_segments_map_quota_errors_to_stable_responses_and_titles_do_not_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    current_user = AuthenticatedUser(id=user_id, email=None, claims={})
    context = TranslationContext(
        target_locale="zh-CN",
        enabled=True,
        engine=TranslationEngine(
            engine_id="deepseek-v4-flash",
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
        ),
        engine_label="DeepSeek V4 Flash",
    )
    quota = SimpleNamespace(reserve=AsyncMock())

    async def fake_context(*_args, **_kwargs):
        return context

    async def fake_resolve(_session, _items, *, reserve_budget, **_options):
        quota.reserve.side_effect = TranslationQuotaExceeded(
            "translation_quota_requests_exceeded", retry_after_seconds=17
        )
        await reserve_budget([])
        return InteractiveResolution({})

    monkeypatch.setattr(translations, "get_translation_context", fake_context)
    monkeypatch.setattr(translations, "resolve_interactive_texts", fake_resolve)
    payload = translations.ResolveTranslationSegmentsRequest(
        segments=[_segment("segment", "text", "paragraph")]
    )
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                translation_quota=quota,
                translation_providers={},
                session_factory=None,
            )
        ),
    )

    with pytest.raises(HTTPException) as raised:
        await translations.resolve_translation_segments(
            payload,
            request=request,
            background_tasks=BackgroundTasks(),
            current_user=current_user,
            session=AsyncMock(),
        )
    assert raised.value.status_code == 429
    assert raised.value.headers == {"Retry-After": "17"}
    assert raised.value.detail == {
        "code": "translation_quota_requests_exceeded",
        "error_code": "translation_quota_requests_exceeded",
    }

    schema = translations.router.routes
    titles_route = next(route for route in schema if route.path == "/translations/titles")
    segments_route = next(route for route in schema if route.path == "/translations/segments")
    assert not getattr(titles_route, "responses", {})
    assert set(getattr(segments_route, "responses", {})) == {403, 429, 503}


def test_segments_storage_failure_maps_to_503_without_database_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The endpoint catches this dedicated exception before constructing the
    # response; keep this assertion close to the production response mapper.
    exception = TranslationQuotaStorageUnavailable()
    mapped = translations._quota_http_exception(exception, status_code=503)
    assert isinstance(mapped, HTTPException)
    assert mapped.status_code == 503
    assert mapped.detail == {
        "code": "translation_quota_storage_unavailable",
        "error_code": "translation_quota_storage_unavailable",
    }


@pytest.mark.asyncio
async def test_segments_map_database_checkout_timeout_to_stable_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timed_out(*_args, **_kwargs):
        raise SQLAlchemyTimeoutError("QueuePool internal details")

    monkeypatch.setattr(translations, "get_translation_context", timed_out)
    payload = translations.ResolveTranslationSegmentsRequest(
        segments=[_segment("segment", "source", "web_segment")]
    )

    with pytest.raises(HTTPException) as raised:
        await translations.resolve_translation_segments(
            payload,
            request=SimpleNamespace(headers={}, app=SimpleNamespace(state=SimpleNamespace())),
            background_tasks=BackgroundTasks(),
            current_user=AuthenticatedUser(id=uuid4(), email=None, claims={}),
            session=AsyncMock(),
        )

    assert raised.value.status_code == 503
    assert raised.value.detail == {
        "code": "translation_storage_busy",
        "error_code": "translation_storage_busy",
    }
    assert "QueuePool" not in str(raised.value.detail)


@pytest.mark.asyncio
async def test_segments_map_realtime_cache_checkout_timeout_to_stable_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = TranslationContext(
        target_locale="zh-CN",
        enabled=True,
        engine=TranslationEngine(
            engine_id="deepseek-v4-flash",
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
        ),
    )

    class TimedOutCoordinator:
        async def resolve(self, *_args, **_kwargs):
            raise SQLAlchemyTimeoutError("QueuePool internal details")

    monkeypatch.setattr(
        translations,
        "get_translation_context",
        AsyncMock(return_value=context),
    )
    payload = translations.ResolveTranslationSegmentsRequest(
        segments=[_segment("segment", "source", "web_segment")]
    )
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                realtime_translation_coordinator=TimedOutCoordinator(),
                translation_providers={},
            )
        ),
    )

    with pytest.raises(HTTPException) as raised:
        await translations.resolve_translation_segments(
            payload,
            request=request,
            background_tasks=BackgroundTasks(),
            current_user=AuthenticatedUser(id=uuid4(), email=None, claims={}),
            session=AsyncMock(),
        )

    assert raised.value.status_code == 503
    assert raised.value.detail["error_code"] == "translation_storage_busy"


@pytest.mark.asyncio
async def test_segments_do_not_misclassify_unrelated_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timed_out(*_args, **_kwargs):
        raise TimeoutError("unrelated timeout")

    monkeypatch.setattr(translations, "get_translation_context", timed_out)
    payload = translations.ResolveTranslationSegmentsRequest(
        segments=[_segment("segment", "source", "web_segment")]
    )

    with pytest.raises(TimeoutError, match="unrelated timeout"):
        await translations.resolve_translation_segments(
            payload,
            request=SimpleNamespace(headers={}, app=SimpleNamespace(state=SimpleNamespace())),
            background_tasks=BackgroundTasks(),
            current_user=AuthenticatedUser(id=uuid4(), email=None, claims={}),
            session=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_production_segments_route_retries_failed_ranking_title_once_per_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ranking retry uses the production segments path without a durable loop."""
    user_id = uuid4()
    current_user = AuthenticatedUser(id=user_id, email=None, claims={})
    context = TranslationContext(
        target_locale="zh-CN",
        enabled=True,
        engine=TranslationEngine(
            engine_id="deepseek-v4-flash",
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
        ),
        engine_label="DeepSeek V4 Flash",
    )
    project_calls: list[bool] = []

    async def failed_projection(_session, items, *, context, ensure_missing):
        project_calls.append(ensure_missing)
        return {
            candidate.item_id: TranslationProjection(
                item_id=candidate.item_id,
                source_hash="ranking-title-hash",
                target_locale=context.target_locale,
                status=TranslationStatus.FAILED,
                error_code="transport_error",
                error_retryable=True,
            )
            for candidate in items
        }

    class ProviderSpy:
        name = "deepseek"
        model_name = "deepseek-v4-flash"

        def __init__(self) -> None:
            self.calls = 0

        async def translate_batch(self, items, *, source_locale, target_locale):
            self.calls += 1
            raise TranslationProviderError(
                "provider unavailable",
                code="transport_error",
                retryable=True,
            )

    provider = ProviderSpy()
    session = AsyncMock()
    monkeypatch.setattr("app.translation.interactive.project_texts", failed_projection)
    monkeypatch.setattr(translations, "get_translation_context", AsyncMock(return_value=context))
    payload = translations.ResolveTranslationSegmentsRequest(
        segments=[_segment("ranking-1:title", "Original ranking title", "ranking_title")]
    )
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_factory=None,
                translation_providers={context.engine.engine_id: provider},
            )
        ),
    )

    first = await translations.resolve_translation_segments(
        payload,
        request=request,
        background_tasks=BackgroundTasks(),
        current_user=current_user,
        session=session,
    )
    second = await translations.resolve_translation_segments(
        payload,
        request=request,
        background_tasks=BackgroundTasks(),
        current_user=current_user,
        session=session,
    )

    assert provider.calls == 2, "each explicit click gets one direct provider attempt"
    assert project_calls == [False, False]
    assert first[0].translation_status == "failed"
    assert second[0].translation_status == "failed"
    # Each call ends the context read before interactive projection; the
    # interactive resolver then commits its own short cache transaction.
    assert session.commit.await_count == 6
