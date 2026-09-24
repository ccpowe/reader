import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.api import translation_preferences
from app.core.auth import AuthenticatedUser
from app.core.settings import Settings
from app.domain.enums import TranslationProviderMode
from app.llm.codex_auth import subscription_headers
from app.llm.codex_subscription import CodexSubscriptionModel
from app.llm.factory import ChatModelConfigurationError, chat_model_snapshot
from app.storage.models import TranslationPreference
from app.translation.domain import TranslationItem, TranslationProviderError
from app.translation.engines import engine_descriptor
from app.translation.factory import (
    build_translation_provider,
    build_translation_provider_routes,
    close_translation_providers,
)
from app.web_rule_agent.model import build_rule_chat_model

ENGINE = "codex-subscription"


def write_auth(path, *, expiry=None, account_id="test-account"):
    claims = {
        "exp": expiry or time.time() + 3600,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_data_residency": "us",
        },
    }
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token = f"test.{encoded}.signature"
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "reader_auth_version": 1,
                "tokens": {"access_token": token, "refresh_token": "private-refresh"},
            }
        )
    )
    path.chmod(0o600)
    return token


@pytest.fixture
def configured(tmp_path):
    path = tmp_path / "auth.json"
    write_auth(path)
    return Settings(
        _env_file=None, codex_subscription_auth_file=path, translation_default_engine_id=ENGINE
    )


def event(kind, **kwargs):
    return "data: " + json.dumps({"type": kind, **kwargs}) + "\r\n\r\n"


def completed(text=None):
    text = text or json.dumps({"items": [{"item_id": "title", "translated_text": "你好"}]})
    return event(
        "response.completed",
        response={
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "not translation"}],
                },
                {"type": "message", "content": [{"type": "output_text", "text": text}]},
            ],
        },
    )


async def translate(provider):
    return await provider.translate_batch(
        [TranslationItem("title", "Hello", purpose="title")],
        source_locale="en",
        target_locale="zh-CN",
    )


def test_explicit_opt_in_and_safe_invalid_credentials(configured):
    assert (
        engine_descriptor(Settings(_env_file=None), ENGINE).unavailable_reason
        == "missing_codex_subscription_auth_file"
    )
    descriptor = engine_descriptor(configured, ENGINE)
    assert descriptor.available
    for value in (
        "not json",
        "null",
        "[]",
        '{"auth_mode":"apikey"}',
        '{"auth_mode":"chatgpt","tokens":{"access_token":"private"}}',
    ):
        configured.codex_subscription_auth_file.write_text(value)
        unavailable = engine_descriptor(configured, ENGINE)
        assert unavailable.unavailable_reason == "invalid_codex_subscription_auth"
        assert unavailable.to_engine().fingerprint == descriptor.to_engine().fingerprint
    configured.codex_subscription_auth_file.unlink()
    assert not engine_descriptor(configured, ENGINE).available


def test_expired_login_and_account_fallback(configured):
    headers = subscription_headers(configured.codex_subscription_auth_file)
    assert headers["ChatGPT-Account-ID"] == "test-account"
    assert headers["x-openai-internal-codex-residency"] == "us"
    write_auth(configured.codex_subscription_auth_file, expiry=time.time() - 1)
    assert engine_descriptor(configured, ENGINE).available
    auth = json.loads(configured.codex_subscription_auth_file.read_text())
    auth["tokens"].pop("refresh_token")
    configured.codex_subscription_auth_file.write_text(json.dumps(auth))
    assert (
        engine_descriptor(configured, ENGINE).unavailable_reason
        == "expired_codex_subscription_auth"
    )


async def test_wire_schema_complete_output_and_credential_rotation(configured, monkeypatch):
    tokens = []

    async def send(_client, request, **kwargs):
        assert str(request.url) == "https://chatgpt.com/backend-api/codex/responses"
        tokens.append(request.headers["authorization"])
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-luna"
        assert body["reasoning"] == {"effort": "low"}
        assert body["store"] is False and body["stream"] is True
        assert "Hello" in body["input"][0]["content"]
        assert "translation" in body["instructions"]
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        assert not {"temperature", "max_tokens", "response_format"} & body.keys()
        return httpx.Response(
            200,
            request=request,
            text=": keepalive\r\n\r\n"
            + event("response.output_text.delta", delta="partial")
            + completed(),
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    provider = build_translation_provider(configured)
    assert (await translate(provider))[0].translated_text == "你好"
    token = write_auth(configured.codex_subscription_auth_file, account_id="rotated-account")
    assert (await translate(provider))[0].translated_text == "你好"
    assert tokens[0] != tokens[1] == "Bearer " + token
    await provider.aclose()


def finished_message(text, *, index=0, phase="final_answer", status="completed"):
    return event(
        "response.output_item.done",
        output_index=index,
        item={
            "type": "message",
            "phase": phase,
            "status": status,
            "content": [{"type": "output_text", "text": text}],
        },
    )


@pytest.mark.parametrize("terminal_output", [{}, {"output": []}])
async def test_finished_items_supply_output_only_after_response_completed(
    configured, monkeypatch, terminal_output
):
    text = json.dumps({"items": [{"item_id": "title", "translated_text": "你好"}]})
    stream = (
        finished_message("not translation", index=0, phase="commentary")
        + finished_message(text[10:], index=2)
        + finished_message(text[:10], index=1)
        + event("response.completed", response={"status": "completed", **terminal_output})
    )

    async def send(_client, request, **kwargs):
        return httpx.Response(200, request=request, text=stream)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    provider = build_translation_provider(configured)
    try:
        assert (await translate(provider))[0].translated_text == "你好"
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "data: [DONE]\n\n",
        event("response.failed", response={"status": "failed"}),
        event("response.incomplete", response={"status": "incomplete"}),
    ],
)
async def test_finished_item_is_not_a_successful_response(configured, monkeypatch, suffix):
    text = json.dumps({"items": [{"item_id": "title", "translated_text": "你好"}]})

    async def send(_client, request, **kwargs):
        return httpx.Response(200, request=request, text=finished_message(text) + suffix)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    provider = build_translation_provider(configured)
    try:
        with pytest.raises(TranslationProviderError):
            await translate(provider)
    finally:
        await provider.aclose()


def test_terminal_output_is_authoritative_and_buffer_is_bounded():
    from app.llm.codex_subscription import CodexSubscriptionStreamError, _ResponseStream

    state = _ResponseStream()
    for line in (finished_message("earlier") + completed("final")).splitlines():
        result = state.feed(line)
        if result:
            assert result.generations[0].message.content == "final"
            break
    else:
        pytest.fail("No completed result")

    state = _ResponseStream()
    with pytest.raises(CodexSubscriptionStreamError, match="output exceeds"):
        for index in range(129):
            for line in finished_message("x", index=index).splitlines():
                state.feed(line)


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "authentication_failed", False),
        (403, "authentication_failed", False),
        (404, "unsupported_model", False),
        (429, "rate_limited", True),
        (500, "provider_unavailable", True),
        (400, "request_rejected", False),
    ],
)
async def test_safe_http_errors(configured, monkeypatch, status, code, retryable):
    def refresh(_client, request, **kwargs):
        return httpx.Response(401, request=request, json={"error": "invalid_grant"})

    monkeypatch.setattr(httpx.Client, "send", refresh)

    async def send(_client, request, **kwargs):
        return httpx.Response(
            status, request=request, text="private-refresh", headers={"retry-after": "7"}
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    with pytest.raises(TranslationProviderError) as error:
        await translate(build_translation_provider(configured))
    assert error.value.code == code
    assert error.value.retryable is retryable
    assert "private-refresh" not in str(error.value.__cause__)
    if status in {429, 500}:
        assert error.value.retry_after_seconds == 7


@pytest.mark.parametrize(
    "stream",
    [
        event("response.output_text.delta", delta='{"items":[]}') + "data: [DONE]\n\n",
        event("response.failed", response={"error": {"message": "private-refresh"}}),
        event("response.incomplete", response={"status": "incomplete"}),
        "data: invalid-json\n\n",
        completed('{"items":[{"item_id":"wrong","translated_text":"你好"}]}'),
    ],
)
async def test_incomplete_or_invalid_stream_never_returns_translation(
    configured, monkeypatch, stream
):
    async def send(_client, request, **kwargs):
        return httpx.Response(200, request=request, text=stream)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    with pytest.raises(TranslationProviderError) as error:
        await translate(build_translation_provider(configured))
    assert error.value.retryable
    assert "private-refresh" not in str(error.value.__cause__)


async def test_deleted_credentials_are_not_retried(configured):
    provider = build_translation_provider(configured)
    configured.codex_subscription_auth_file.unlink()
    with pytest.raises(TranslationProviderError) as error:
        await translate(provider)
    assert error.value.code == "authentication_failed"
    assert not error.value.retryable


async def test_routes_share_capacity_model_changes_cache_and_rule_agent_rejects(configured):
    routes = build_translation_provider_routes(configured, require_default=True)
    try:
        providers = list(routes.values())
        assert len(providers) == 2
        assert providers[0]._request_semaphore is providers[1]._request_semaphore
        before = engine_descriptor(configured, ENGINE)
        changed = configured.model_copy(update={"codex_subscription_model": "another-model"})
        assert (
            before.to_engine().fingerprint
            != engine_descriptor(changed, ENGINE).to_engine().fingerprint
        )
        assert "private-refresh" not in json.dumps(chat_model_snapshot(configured, before))
        with pytest.raises(ChatModelConfigurationError, match="translation only"):
            build_rule_chat_model(configured, before)
    finally:
        await close_translation_providers(routes)


async def test_deadline_and_cancellation(configured, monkeypatch):
    async def send(_client, request, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    model = CodexSubscriptionModel(auth_file=configured.codex_subscription_auth_file, timeout=0.01)
    with pytest.raises(TimeoutError):
        await model.ainvoke("Hello")
    task = asyncio.create_task(model.ainvoke("Hello"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_preference_selects_codex(configured, monkeypatch):
    monkeypatch.setattr(translation_preferences, "get_settings", lambda: configured)
    monkeypatch.setattr(translation_preferences, "cancel_stale_user_translation_work", AsyncMock())
    monkeypatch.setattr(translation_preferences, "cache_translation_context", lambda *_: None)
    user_id = uuid4()
    session = AsyncMock()
    session.get.return_value = TranslationPreference(
        user_id=user_id,
        target_locale="zh-CN",
        provider_mode=TranslationProviderMode.APP_DEFAULT,
        is_enabled=True,
    )
    response = await translation_preferences.update_translation_preference(
        translation_preferences.UpdateTranslationPreferenceRequest(engine_id=ENGINE),
        current_user=AuthenticatedUser(id=user_id, email=None, claims={}),
        session=session,
    )
    assert response.effective_engine_id == ENGINE
    assert response.effective_engine_available
    assert response.effective_engine_label == "Codex · gpt-6-luna"
