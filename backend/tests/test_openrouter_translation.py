import json

import httpx
import pytest

from app.core.settings import Settings
from app.translation.domain import TranslationItem, TranslationProviderError
from app.translation.engines import engine_descriptor
from app.translation.factory import (
    TranslationProviderConfigurationError,
    build_translation_provider,
    build_translation_provider_routes,
    close_translation_providers,
)

ENGINE = "openrouter-minimax-m3"


def settings(**kwargs):
    return Settings(_env_file=None, translation_default_engine_id=ENGINE, **kwargs)


@pytest.mark.parametrize("alias", ["APP_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"])
def test_key_alias_and_stable_identity(alias):
    configured = settings(**{alias: "test-secret"})
    assert configured.openrouter_api_key.get_secret_value() == "test-secret"
    available = engine_descriptor(configured, ENGINE)
    unavailable = engine_descriptor(settings(openrouter_api_key=None), ENGINE)
    assert available.available
    assert not unavailable.available
    assert unavailable.unavailable_reason == "missing_openrouter_api_key"
    assert available.to_engine().fingerprint == unavailable.to_engine().fingerprint
    assert "test-secret" not in repr(configured)


def test_missing_key_rejects_required_default():
    with pytest.raises(TranslationProviderConfigurationError, match="missing_openrouter_api_key"):
        build_translation_provider_routes(settings(openrouter_api_key=None), require_default=True)


def test_missing_package_is_unavailable(monkeypatch):
    monkeypatch.setattr("app.translation.engines.find_spec", lambda _name: None)
    descriptor = engine_descriptor(settings(openrouter_api_key="secret"), ENGINE)
    assert not descriptor.available
    assert descriptor.unavailable_reason == "langchain_openai_not_installed"


@pytest.mark.asyncio
async def test_routes_share_openrouter_capacity_but_not_deepseek_capacity():
    routes = build_translation_provider_routes(
        settings(openrouter_api_key="secret", deepseek_api_key="secret"), require_default=True
    )
    try:
        router = [p for p in routes.values() if p.name == "openrouter"]
        deepseek = [p for p in routes.values() if p.name == "deepseek"]
        assert len(router) == 2
        assert router[0]._request_semaphore is router[1]._request_semaphore
        assert router[0]._request_semaphore is not deepseek[0]._request_semaphore
        assert router[0].model_name == "minimax/minimax-m3"
    finally:
        await close_translation_providers(routes)


@pytest.mark.parametrize("status", [200, 401, 429])
@pytest.mark.asyncio
async def test_openrouter_wire_contract_and_error_mapping(monkeypatch, status):
    calls = []

    async def send(_client, request, **_kwargs):
        calls.append(request)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "minimax/minimax-m3"
        assert body["reasoning"] == {"exclude": True}
        assert body["response_format"] == {"type": "json_object"}
        assert "thinking" not in body
        if status != 200:
            return httpx.Response(status, request=request, json={"error": {"message": "failure"}})
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {"items": [{"item_id": "title", "translated_text": "你好，世界"}]}
                            ),
                        },
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    provider = build_translation_provider(settings(openrouter_api_key="test-secret"))
    try:
        if status == 200:
            results = await provider.translate_batch(
                [TranslationItem("title", "Hello, world", purpose="title")],
                source_locale="en",
                target_locale="zh-CN",
            )
            assert results[0].translated_text == "你好，世界"
        else:
            with pytest.raises(TranslationProviderError) as error:
                await provider.translate_batch(
                    [TranslationItem("title", "Hello")], source_locale="en", target_locale="zh-CN"
                )
            assert error.value.retryable is (status == 429)
        assert len(calls) == 1
    finally:
        await provider.aclose()
