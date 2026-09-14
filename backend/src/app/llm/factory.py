"""Provider configuration without translation or agent workflow semantics."""

from __future__ import annotations

import hashlib
import json
from inspect import isawaitable
from typing import Any

from app.core.settings import Settings
from app.translation.engines import EngineDescriptor


class ChatModelConfigurationError(RuntimeError):
    """The selected managed provider cannot construct a chat model."""


def build_chat_model(
    settings: Settings,
    descriptor: EngineDescriptor,
    *,
    timeout_seconds: float | None = None,
    max_output_tokens: int | None = None,
) -> Any:
    """Create a model; callers own prompts, output protocols, budgets and cleanup."""
    options: dict[str, Any] = {}
    if max_output_tokens is not None:
        options["max_tokens"] = max_output_tokens
    if descriptor.provider_name == "openrouter":
        if settings.openrouter_api_key is None:
            raise ChatModelConfigurationError(
                "APP_OPENROUTER_API_KEY is required for the OpenRouter engine."
            )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ChatModelConfigurationError(
                "langchain-openai is required for the OpenRouter engine."
            ) from exc
        return ChatOpenAI(
            api_key=settings.openrouter_api_key.get_secret_value(),
            base_url=str(settings.openrouter_api_base).rstrip("/"),
            extra_body={"reasoning": {"exclude": True}},
            max_retries=0,
            model=descriptor.model_name,
            temperature=0,
            timeout=(
                settings.openrouter_request_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
            use_responses_api=False,
            **options,
        )
    if descriptor.provider_name != "deepseek":
        raise ChatModelConfigurationError(
            f"Unsupported LangChain provider: {descriptor.provider_name}."
        )
    if settings.deepseek_api_key is None:
        raise ChatModelConfigurationError(
            "APP_DEEPSEEK_API_KEY is required for the DeepSeek engine."
        )
    try:
        from langchain_deepseek import ChatDeepSeek
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ChatModelConfigurationError(
            "langchain-deepseek is required for the DeepSeek engine."
        ) from exc
    return ChatDeepSeek(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=str(settings.deepseek_api_base).rstrip("/"),
        extra_body={"thinking": {"type": "disabled"}},
        max_retries=0,
        model=descriptor.model_name,
        temperature=0,
        timeout=(
            settings.deepseek_request_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        ),
        **options,
    )


def chat_model_snapshot(settings: Settings, descriptor: EngineDescriptor) -> dict[str, str]:
    """Freeze model identity, excluding personal preferences and credential material."""
    if descriptor.provider_name == "openrouter":
        base_url = str(settings.openrouter_api_base).rstrip("/")
        options = {"reasoning": {"exclude": True}}
    elif descriptor.provider_name == "deepseek":
        base_url = str(settings.deepseek_api_base).rstrip("/")
        options = {"thinking": {"type": "disabled"}}
    else:
        raise ChatModelConfigurationError("Unsupported managed model provider.")
    identity = {
        "engine_id": descriptor.engine_id,
        "provider_name": descriptor.provider_name,
        "model_name": descriptor.model_name,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            {**identity, "base_url": base_url, "options": options},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {**identity, "config_fingerprint": fingerprint}


async def close_chat_model(model: Any) -> None:
    """Close model-owned SDK clients without closing the same owner twice."""
    close = getattr(model, "aclose", None) or getattr(model, "close", None)
    if close is not None:
        result = close()
        if isawaitable(result):
            await result
        return
    seen: set[int] = set()
    for candidate in (
        getattr(model, "root_async_client", None),
        getattr(model, "root_client", None),
    ):
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        close = getattr(candidate, "aclose", None) or getattr(candidate, "close", None)
        if close is not None:
            result = close()
            if isawaitable(result):
                await result
