"""Engine catalog, model clients and the prompt, with no network."""

from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
import pytest

from reader.core.settings import Settings
from reader.translation.engines import (
    CODEX_RESPONSES_URL,
    CodexSubscriptionClient,
    EngineError,
    EnginePool,
    OpenAICompatibleClient,
    _CodexStream,
    catalog,
    find_engine,
)
from reader.translation.prompt import PromptItem, batches, build_messages, parse_reply

REPLY = json.dumps({"items": [{"item_id": "1", "translated_text": "你好"}]})


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


# --- Catalog ------------------------------------------------------------------------


def test_engines_are_available_only_when_configured(tmp_path) -> None:
    bare = {engine.id: engine for engine in catalog(_settings())}
    keyed = {engine.id: engine for engine in catalog(_settings(deepseek_api_key="k"))}

    assert not any(engine.available for engine in bare.values())
    assert bare["deepseek-v4-flash"].unavailable_reason == "missing_deepseek_api_key"
    assert keyed["deepseek-v4-flash"].available
    assert find_engine(_settings(), None).id == "deepseek-v4-flash"
    assert find_engine(_settings(), "nope") is None


def test_the_fingerprint_changes_with_model_or_prompt() -> None:
    base = find_engine(_settings(), "deepseek-v4-flash").fingerprint
    other_model = find_engine(_settings(deepseek_model="m2"), "deepseek-v4-flash").fingerprint
    other_prompt = find_engine(
        _settings(translation_prompt_version="v4"), "deepseek-v4-flash"
    ).fingerprint

    assert len({base, other_model, other_prompt}) == 3


@pytest.mark.asyncio
async def test_the_pool_reserves_budget_only_once_a_slot_is_free() -> None:
    events: list[str] = []

    class Slow:
        async def complete(self, system, user):
            events.append(f"call {user}")
            await asyncio.sleep(0.05)
            events.append(f"done {user}")
            return REPLY

        async def aclose(self):
            pass

    pool = EnginePool(_settings(translation_max_concurrency=1), build=lambda *_: Slow())
    engine = find_engine(_settings(), None)

    def reserve(name):
        async def before():
            events.append(f"reserve {name}")

        return before

    await asyncio.gather(
        pool.complete(engine, "s", "1", before=reserve("1")),
        pool.complete(engine, "s", "2", before=reserve("2")),
    )

    assert events == ["reserve 1", "call 1", "done 1", "reserve 2", "call 2", "done 2"]


def test_database_errors_never_show_bound_values() -> None:
    from reader.core.db import build_engine

    engine = build_engine(_settings(database_url="postgresql+asyncpg://u@127.0.0.1/db"))
    assert engine.sync_engine.hide_parameters is True


# --- OpenAI-compatible client ---------------------------------------------------------


def _openai(handler) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key="secret-key",
        model="m",
        timeout=5,
        extra_body={"thinking": {"type": "disabled"}},
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_openai_client_sends_a_json_request() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": REPLY}}]})

    client = _openai(handler)
    assert await client.complete("system", "user") == REPLY
    await client.aclose()

    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-key"
    body = seen["body"]
    assert body["response_format"] == {"type": "json_object"} and body["temperature"] == 0
    assert body["thinking"] == {"type": "disabled"}
    assert [message["role"] for message in body["messages"]] == ["system", "user"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "authentication_failed", False),
        (404, "unsupported_model", False),
        (429, "rate_limited", True),
        (503, "provider_unavailable", True),
        (400, "request_rejected", False),
    ],
)
async def test_openai_http_errors_never_echo_the_body(status, code, retryable) -> None:
    client = _openai(
        lambda request: httpx.Response(status, text="secret-key", headers={"retry-after": "7"})
    )
    with pytest.raises(EngineError) as error:
        await client.complete("s", "u")

    assert (error.value.code, error.value.retryable) == (code, retryable)
    assert "secret-key" not in str(error.value)
    if status in {429, 503}:
        assert error.value.retry_after == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [{"choices": []}, {"choices": [{"message": {"content": ""}}]}, {"nope": 1}],
)
async def test_openai_unusable_replies(reply) -> None:
    client = _openai(lambda request: httpx.Response(200, json=reply))
    with pytest.raises(EngineError) as error:
        await client.complete("s", "u")
    assert error.value.code == "invalid_response" and error.value.isolate


@pytest.mark.asyncio
async def test_openai_transport_errors() -> None:
    def fail(request):
        raise httpx.ConnectError("down")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(EngineError) as down:
        await _openai(fail).complete("s", "u")
    with pytest.raises(EngineError) as timeout:
        await _openai(slow).complete("s", "u")
    assert down.value.code == "transport_error" and timeout.value.code == "timeout"


# --- Codex subscription client ------------------------------------------------------


def _write_auth(path, *, account="test-account") -> str:
    claims = {
        "exp": time.time() + 3600,
        "https://api.openai.com/auth": {"chatgpt_account_id": account},
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
def auth_file(tmp_path, monkeypatch):
    def refresh(_client, request, **kwargs):  # an OAuth refresh that always fails
        return httpx.Response(401, request=request, json={"error": "invalid_grant"})

    monkeypatch.setattr(httpx.Client, "send", refresh)
    path = tmp_path / "auth.json"
    _write_auth(path)
    return path


def _codex(auth_file, handler, timeout=5.0) -> CodexSubscriptionClient:
    return CodexSubscriptionClient(
        auth_file=auth_file,
        model="gpt-6-luna",
        timeout=timeout,
        transport=httpx.MockTransport(handler),
    )


def _event(kind, **fields) -> str:
    return "data: " + json.dumps({"type": kind, **fields}) + "\r\n\r\n"


def _completed(text=REPLY) -> str:
    return _event(
        "response.completed",
        response={
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "not the answer"}],
                },
                {"type": "message", "content": [{"type": "output_text", "text": text}]},
            ],
        },
    )


def _finished(text, *, index=0, phase="final_answer") -> str:
    return _event(
        "response.output_item.done",
        output_index=index,
        item={
            "type": "message",
            "phase": phase,
            "status": "completed",
            "content": [{"type": "output_text", "text": text}],
        },
    )


@pytest.mark.asyncio
async def test_codex_wire_format_and_login_rotation(auth_file) -> None:
    tokens = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == CODEX_RESPONSES_URL
        tokens.append(request.headers["authorization"])
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-luna" and body["reasoning"] == {"effort": "low"}
        assert body["store"] is False and body["stream"] is True
        assert body["input"][0]["content"] == "Hello" and body["instructions"] == "system"
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        stream = ": keepalive\r\n\r\n" + _event("response.output_text.delta", delta="x")
        return httpx.Response(200, text=stream + _completed())

    client = _codex(auth_file, handler)
    assert await client.complete("system", "Hello") == REPLY
    rotated = _write_auth(auth_file, account="rotated-account")
    assert await client.complete("system", "Hello") == REPLY
    await client.aclose()

    assert tokens[0] != tokens[1] == "Bearer " + rotated


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [{}, {"output": []}])
async def test_codex_finished_items_count_only_once_the_response_completes(
    auth_file, terminal
) -> None:
    stream = (
        _finished("not the answer", index=0, phase="commentary")
        + _finished(REPLY[10:], index=2)
        + _finished(REPLY[:10], index=1)
        + _event("response.completed", response={"status": "completed", **terminal})
    )
    client = _codex(auth_file, lambda request: httpx.Response(200, text=stream))
    assert await client.complete("s", "u") == REPLY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream",
    [
        _finished(REPLY),
        _finished(REPLY) + "data: [DONE]\n\n",
        _finished(REPLY) + _event("response.failed", response={"status": "failed"}),
        _event("response.incomplete", response={"status": "incomplete"}),
        _event("response.failed", response={"error": {"message": "private-refresh"}}),
        "data: invalid-json\n\n",
    ],
)
async def test_codex_unfinished_or_broken_streams_are_errors(auth_file, stream) -> None:
    client = _codex(auth_file, lambda request: httpx.Response(200, text=stream))
    with pytest.raises(EngineError) as error:
        await client.complete("s", "u")
    assert error.value.code == "invalid_response" and error.value.retryable
    assert "private-refresh" not in str(error.value)


def test_codex_stream_buffer_is_bounded() -> None:
    stream = _CodexStream()
    with pytest.raises(EngineError):
        for index in range(129):
            for line in _finished("x", index=index).splitlines():
                stream.feed(line)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "authentication_failed", False),
        (403, "authentication_failed", False),
        (429, "rate_limited", True),
        (500, "provider_unavailable", True),
    ],
)
async def test_codex_http_errors(auth_file, status, code, retryable) -> None:
    client = _codex(
        auth_file,
        lambda request: httpx.Response(
            status, text="private-refresh", headers={"retry-after": "7"}
        ),
    )
    with pytest.raises(EngineError) as error:
        await client.complete("s", "u")
    assert (error.value.code, error.value.retryable) == (code, retryable)
    assert "private-refresh" not in str(error.value)


@pytest.mark.asyncio
async def test_codex_deleted_login_is_not_retried(auth_file) -> None:
    client = _codex(auth_file, lambda request: pytest.fail("should not call"))
    auth_file.unlink()
    with pytest.raises(EngineError) as error:
        await client.complete("s", "u")
    assert error.value.code == "authentication_failed" and not error.value.retryable


@pytest.mark.asyncio
async def test_codex_deadline(auth_file) -> None:
    async def slow(request):
        await asyncio.sleep(60)

    client = _codex(auth_file, slow, timeout=0.05)
    with pytest.raises(EngineError) as error:
        await client.complete("s", "u")
    assert error.value.code == "timeout"


# --- Prompt -------------------------------------------------------------------------


def test_marker_items_carry_their_expected_tokens() -> None:
    text = "Costs ⟪READER_OPEN_0⟫rise⟪READER_CLOSE_0⟫."
    system, user = build_messages(
        [PromptItem("a", text, marker_feedback=True)], "web_segment", "zh-CN"
    )
    payload = json.loads(user)

    assert "rich-text-v1" in system
    assert payload["items"][0]["expected_tokens"] == ["⟪READER_OPEN_0⟫", "⟪READER_CLOSE_0⟫"]
    assert payload["items"][0]["validation_feedback"]["code"] == "placeholder_mismatch"
    plain_system, plain_user = build_messages([PromptItem("b", "Hello")], "title", "zh-CN")
    assert "rich-text" not in plain_system and "expected_tokens" not in plain_user


@pytest.mark.parametrize(
    "reply,code",
    [
        ("not json", "invalid_response"),
        ('{"items": [{"item_id": "1", "translated_text": "  "}]}', "invalid_response"),
        ('{"items": [{"item_id": "2", "translated_text": "x"}]}', "incomplete_response"),
        (
            '{"items": [{"item_id": "1", "translated_text": "x"},'
            ' {"item_id": "1", "translated_text": "y"}]}',
            "invalid_response",
        ),
    ],
)
def test_a_reply_must_translate_every_item_once(reply, code) -> None:
    with pytest.raises(EngineError) as error:
        parse_reply(reply, [PromptItem("1", "Hello")])
    assert error.value.code == code and error.value.isolate


def test_batches_respect_both_limits() -> None:
    items = [PromptItem(str(n), "x" * size) for n, size in enumerate([5, 5, 5, 20, 1])]
    groups = [
        [item.item_id for item in batch] for batch in batches(items, max_items=2, max_chars=12)
    ]
    assert groups == [["0", "1"], ["2"], ["3"], ["4"]]
