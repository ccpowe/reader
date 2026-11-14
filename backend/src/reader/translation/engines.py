"""Translation engines: the fixed catalog and the model clients behind it.

Three engines, each one model: DeepSeek and OpenRouter speak the OpenAI chat
completions API; the Codex subscription speaks the ChatGPT Responses stream with a
login Reader refreshes itself (codex_auth). A client takes a system prompt and a
user message and returns the model's JSON text; prompts live in `prompt`.

An engine's fingerprint names engine, model and prompt version. Translations are
cached per fingerprint, so changing any of them never reuses older output.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from reader.core.settings import Settings
from reader.translation.codex_auth import (
    CodexAuthenticationError,
    CodexRefreshError,
    subscription_headers,
    subscription_unavailable_reason,
)

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
_MAX_RESPONSE_BYTES = 2_000_000

# The JSON every engine must return; Codex enforces it natively.
BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "translated_text": {"type": "string"},
                },
                "required": ["item_id", "translated_text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class EngineError(Exception):
    """A failed model call, with a stable code and whether retrying can help.

    `isolate`: the batch as a whole was unusable; retrying items one by one may work.
    Messages never contain upstream bodies, which can echo credentials or source text.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = True,
        retry_after: float | None = None,
        isolate: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.isolate = isolate


@dataclass(frozen=True)
class Engine:
    id: str
    label: str
    provider: str
    model: str
    prompt_version: str
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None

    @property
    def fingerprint(self) -> str:
        identity = {"engine": self.id, "model": self.model, "prompt": self.prompt_version}
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def catalog(settings: Settings) -> tuple[Engine, ...]:
    prompt = settings.translation_prompt_version
    return (
        Engine(
            id="deepseek-v4-flash",
            label=f"DeepSeek · {settings.deepseek_model}",
            provider="deepseek",
            model=settings.deepseek_model,
            prompt_version=prompt,
            unavailable_reason=None if settings.deepseek_api_key else "missing_deepseek_api_key",
        ),
        Engine(
            id="openrouter-minimax-m3",
            label=f"OpenRouter · {settings.openrouter_model}",
            provider="openrouter",
            model=settings.openrouter_model,
            prompt_version=prompt,
            unavailable_reason=None
            if settings.openrouter_api_key
            else "missing_openrouter_api_key",
        ),
        Engine(
            id="codex-subscription",
            label=f"Codex · {settings.codex_subscription_model}",
            provider="codex-subscription",
            model=settings.codex_subscription_model,
            prompt_version=prompt,
            unavailable_reason=subscription_unavailable_reason(
                settings.codex_subscription_auth_file
            ),
        ),
    )


def find_engine(settings: Settings, engine_id: str | None) -> Engine | None:
    """The engine with this ID, or the server default when none is given."""
    wanted = (engine_id or settings.translation_default_engine_id).strip().lower()
    return next((engine for engine in catalog(settings) if engine.id == wanted), None)


# --- Clients ------------------------------------------------------------------------


class ChatClient(Protocol):
    async def complete(self, system: str, user: str) -> str: ...

    async def aclose(self) -> None: ...


def retry_after_seconds(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        delay = float(value)
        return max(delay, 0.0) if math.isfinite(delay) else None
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return max((moment - datetime.now(UTC)).total_seconds(), 0.0)


def http_error(status: int, headers: httpx.Headers | None = None) -> EngineError:
    """Map an upstream HTTP status to an EngineError."""
    retry_after = retry_after_seconds(headers) if headers is not None else None
    if status in {401, 403}:
        return EngineError(
            "authentication_failed", "The engine rejected its credentials.", retryable=False
        )
    if status == 404:
        return EngineError(
            "unsupported_model", "The configured model is unavailable.", retryable=False
        )
    if status == 429:
        return EngineError("rate_limited", "The engine is rate limited.", retry_after=retry_after)
    if status in {408, 425}:
        return EngineError("timeout", "The engine timed out.")
    if status >= 500:
        return EngineError(
            "provider_unavailable",
            "The engine is temporarily unavailable.",
            retry_after=retry_after,
        )
    return EngineError(
        "request_rejected", "The engine rejected the request.", retryable=False, isolate=True
    )


def _transport_error(exc: Exception) -> EngineError:
    if isinstance(exc, httpx.TimeoutException | TimeoutError):
        return EngineError("timeout", "The engine timed out.")
    return EngineError("transport_error", "The engine could not be reached.")


class OpenAICompatibleClient:
    """POST /chat/completions with a JSON object reply; no retries of its own."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        extra_body: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._model = model
        self._extra = extra_body or {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def complete(self, system: str, user: str) -> str:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            **self._extra,
        }
        try:
            response = await self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise _transport_error(exc) from None
        if response.status_code != 200:
            raise http_error(response.status_code, response.headers)
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise EngineError("invalid_response", "The engine reply is too large.", isolate=True)
        try:
            message = response.json()["choices"][0]["message"]
            content = message["content"]
            if message.get("refusal") or not isinstance(content, str) or not content.strip():
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise EngineError(
                "invalid_response", "The engine returned no usable reply.", isolate=True
            ) from None
        return content

    async def aclose(self) -> None:
        await self._client.aclose()


class _CodexStream:
    """Server-sent events of one Responses call; only a completed response counts."""

    def __init__(self) -> None:
        self.data: list[str] = []
        self.finished_items: dict[int, dict] = {}
        self.finished_bytes = 0

    def feed(self, line: str) -> str | None:
        line = line.rstrip("\r\n")
        if line.startswith("data:"):
            self.data.append(line[5:].removeprefix(" "))
            if sum(map(len, self.data)) > _MAX_RESPONSE_BYTES:
                raise _stream_error()
            return None
        if line or not self.data:
            return None
        data, self.data = "\n".join(self.data), []
        if data == "[DONE]":
            return None
        try:
            event = json.loads(data)
            kind = event["type"]
            if kind in {"error", "response.failed", "response.incomplete"}:
                raise _stream_error()
            if kind == "response.output_item.done":
                index, item = event["output_index"], event["item"]
                if type(index) is not int or index < 0 or not isinstance(item, dict):
                    raise ValueError
                self.finished_bytes += len(data)
                if len(self.finished_items) >= 128 or self.finished_bytes > _MAX_RESPONSE_BYTES:
                    raise _stream_error()
                self.finished_items[index] = item
                return None
            if kind != "response.completed":
                return None
            response = event["response"]
            if response.get("status") != "completed":
                raise _stream_error()
            output = response.get("output")
            # Some responses omit the output from the final event; finished items
            # are usable only once the whole response has completed.
            if not output:
                output = [self.finished_items[i] for i in sorted(self.finished_items)]
            if not isinstance(output, list):
                raise ValueError
            texts = []
            for item in output:
                if item["type"] != "message" or item.get("phase") == "commentary":
                    continue
                if item.get("status", "completed") != "completed":
                    raise _stream_error()
                for part in item["content"]:
                    if part["type"] == "refusal":
                        raise http_error(400)
                    if part["type"] == "output_text":
                        texts.append(part["text"])
            content = "".join(texts)
            if not content.strip():
                raise _stream_error()
            return content
        except (ValueError, KeyError, TypeError, AttributeError):
            raise _stream_error() from None


def _stream_error() -> EngineError:
    return EngineError("invalid_response", "The Codex reply did not complete.", isolate=True)


class CodexSubscriptionClient:
    """The ChatGPT Responses endpoint with Reader's own Codex login."""

    def __init__(
        self,
        *,
        auth_file: Path,
        model: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._auth_file = auth_file
        self._model = model
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
        )

    async def _headers(self, rejected: str | None = None) -> dict[str, str]:
        # File locking and the OAuth exchange run off the event loop. A cancelled
        # caller still lets a started rotation persist before the lock is released.
        try:
            return await asyncio.to_thread(
                subscription_headers, self._auth_file, rejected_access_token=rejected
            )
        except CodexAuthenticationError as exc:
            raise EngineError(
                "authentication_failed", "The Codex login needs renewing.", retryable=False
            ) from exc
        except CodexRefreshError as exc:
            raise EngineError("provider_unavailable", "Could not renew the Codex login.") from exc

    async def complete(self, system: str, user: str) -> str:
        body = {
            "model": self._model,
            "instructions": system,
            "input": [{"role": "user", "content": user}],
            "store": False,
            "stream": True,
            "reasoning": {"effort": "low"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "translation_batch",
                    "schema": BATCH_SCHEMA,
                    "strict": True,
                }
            },
        }
        try:
            async with asyncio.timeout(self._timeout):
                headers = await self._headers()
                for attempt in range(2):
                    async with self._client.stream(
                        "POST", CODEX_RESPONSES_URL, json=body, headers=headers
                    ) as response:
                        if response.status_code == 401 and attempt == 0:
                            rejected = headers["Authorization"].removeprefix("Bearer ")
                        elif response.status_code != 200:
                            raise http_error(response.status_code, response.headers)
                        else:
                            stream = _CodexStream()
                            async for line in response.aiter_lines():
                                if (content := stream.feed(line)) is not None:
                                    return content
                            if (content := stream.feed("")) is not None:
                                return content
                            raise _stream_error()
                    # Close the rejected response before exchanging credentials.
                    headers = await self._headers(rejected)
        except httpx.HTTPError as exc:
            raise _transport_error(exc) from None
        except TimeoutError:
            raise EngineError("timeout", "The engine timed out.") from None
        raise http_error(401)  # defensive: a second 401 is raised above

    async def aclose(self) -> None:
        await self._client.aclose()


def build_client(engine: Engine, settings: Settings) -> ChatClient:
    if engine.provider == "deepseek" and settings.deepseek_api_key:
        return OpenAICompatibleClient(
            base_url=str(settings.deepseek_api_base),
            api_key=settings.deepseek_api_key.get_secret_value(),
            model=engine.model,
            timeout=settings.deepseek_request_timeout_seconds,
            extra_body={"thinking": {"type": "disabled"}},
        )
    if engine.provider == "openrouter" and settings.openrouter_api_key:
        return OpenAICompatibleClient(
            base_url=str(settings.openrouter_api_base),
            api_key=settings.openrouter_api_key.get_secret_value(),
            model=engine.model,
            timeout=settings.openrouter_request_timeout_seconds,
            extra_body={"reasoning": {"exclude": True}},
        )
    if engine.provider == "codex-subscription" and settings.codex_subscription_auth_file:
        return CodexSubscriptionClient(
            auth_file=settings.codex_subscription_auth_file,
            model=engine.model,
            timeout=settings.codex_subscription_request_timeout_seconds,
        )
    raise EngineError(
        "engine_unavailable", f"Engine {engine.id} is not configured.", retryable=False
    )


class EnginePool:
    """One client per engine for this process, with a cap on concurrent calls each."""

    def __init__(
        self,
        settings: Settings,
        build: Callable[[Engine, Settings], ChatClient] = build_client,
    ):
        self._settings = settings
        self._build = build
        self._clients: dict[str, ChatClient] = {}
        self._limits: dict[str, asyncio.Semaphore] = {}

    async def complete(
        self,
        engine: Engine,
        system: str,
        user: str,
        *,
        before: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        """Call the engine; `before` runs once a slot is free, right before the call
        (the budget reservation, so it is charged to the window the call is made in)."""
        client = self._clients.get(engine.id)
        if client is None:
            client = self._clients[engine.id] = self._build(engine, self._settings)
        limit = self._limits.setdefault(
            engine.id, asyncio.Semaphore(self._settings.translation_max_concurrency)
        )
        async with limit:
            if before is not None:
                await before()
            return await client.complete(system, user)

    async def aclose(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            await client.aclose()
