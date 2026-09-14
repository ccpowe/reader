"""Provider-neutral adapter for LangChain chat models."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from inspect import isawaitable
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.translation.domain import (
    TranslationItem,
    TranslationProviderError,
    TranslationResult,
)
from app.translation.rich_text import FORMAT_VERSION, TOKEN, uses_reader_format


class _StructuredTranslation(BaseModel):
    item_id: str = Field(min_length=1)
    translated_text: str = Field(min_length=1)

    @field_validator("translated_text")
    @classmethod
    def translated_text_cannot_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("translated_text cannot be blank")
        return normalized


class _StructuredBatch(BaseModel):
    items: list[_StructuredTranslation]


_PURPOSE_RULES = {
    "caption": "Use concise, natural spoken language suitable for a two-line subtitle.",
    "ranking_title": "Preserve repository names, product names, handles, and identifiers.",
    "ranking_description": "Translate the description naturally; preserve technical terms.",
    "web_segment": (
        "Reader rich-text-v1: preserve every ⟪READER_n⟫, ⟪READER_OPEN_n⟫ and "
        "⟪READER_CLOSE_n⟫ token exactly once and verbatim, with the same nesting. "
        "Translate the words between paired tokens, never translate or omit tokens. "
        "These markers are literal application data, not annotations to remove. "
        "Each item's expected_tokens lists the exact markers required in translated_text. "
        "For example, English text 'Costs are ⟪READER_OPEN_0⟫expected⟪READER_CLOSE_0⟫ to rise.' "
        "must produce Chinese translated_text '成本⟪READER_OPEN_0⟫预计⟪READER_CLOSE_0⟫会上升。' "
        "Do not add markers from this example if they are absent in the input. "
        "If validation_feedback.code is placeholder_mismatch, the previous attempt failed "
        "marker validation. Correct that defect and check every expected token before returning. "
        "Preserve links, code, form values, and interface identifiers."
    ),
}
_SUPPORTED_PROMPT_VERSIONS = {"v1", "v2-caption-context"}


class LangChainChatModelProvider:
    """Translate bounded batches through an injected LangChain chat model."""

    name = "langchain"

    def __init__(
        self,
        model: Any,
        *,
        max_chars_per_request: int = 12_000,
        max_concurrent_requests: int = 4,
        max_items_per_request: int = 24,
        model_name: str | None = None,
        prompt_version: str = "v1",
        provider_name: str = "langchain",
        structured_output_method: str | None = None,
        request_semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        if max_chars_per_request < 1 or max_concurrent_requests < 1 or max_items_per_request < 1:
            raise ValueError("LangChain batch limits must be positive.")
        if prompt_version not in _SUPPORTED_PROMPT_VERSIONS:
            raise ValueError(f"Unsupported translation prompt version: {prompt_version}.")
        self._model = model
        self._max_chars_per_request = max_chars_per_request
        self._max_items_per_request = max_items_per_request
        self._request_semaphore = request_semaphore or asyncio.Semaphore(max_concurrent_requests)
        self._structured_output_method = structured_output_method
        self.name = provider_name
        self.model_name = model_name
        self.prompt_version = prompt_version

    async def aclose(self) -> None:
        """Close model-owned async HTTP resources when the integration exposes them."""
        candidates = (self._model, getattr(self._model, "root_async_client", None))
        seen: set[int] = set()
        for candidate in candidates:
            if candidate is None or id(candidate) in seen:
                continue
            seen.add(id(candidate))
            close = getattr(candidate, "aclose", None) or getattr(candidate, "close", None)
            if close is None:
                continue
            result = close()
            if isawaitable(result):
                await result
            return

    async def translate_batch(
        self,
        items: Sequence[TranslationItem],
        *,
        source_locale: str | None,
        target_locale: str,
    ) -> list[TranslationResult]:
        if not items:
            return []
        if not target_locale.strip():
            raise TranslationProviderError(
                "Target locale is required.",
                code="invalid_locale",
                retryable=False,
            )

        chunks = list(
            _chunk_items(
                items,
                max_chars=self._max_chars_per_request,
                max_items=self._max_items_per_request,
                prompt_version=self.prompt_version,
            )
        )
        chunk_results = await asyncio.gather(
            *(
                self._translate_chunk_bounded(
                    chunk,
                    source_locale=source_locale,
                    target_locale=target_locale,
                )
                for chunk in chunks
            )
        )
        return [result for results in chunk_results for result in results]

    async def _translate_chunk_bounded(
        self,
        items: Sequence[TranslationItem],
        *,
        source_locale: str | None,
        target_locale: str,
    ) -> list[TranslationResult]:
        async with self._request_semaphore:
            return await self._translate_chunk(
                items,
                source_locale=source_locale,
                target_locale=target_locale,
            )

    async def _translate_chunk(
        self,
        items: Sequence[TranslationItem],
        *,
        source_locale: str | None,
        target_locale: str,
    ) -> list[TranslationResult]:
        payload = _prompt_payload(items, prompt_version=self.prompt_version)
        purpose_rules = sorted(
            {
                _PURPOSE_RULES[purpose]
                for item in items
                if (purpose := getattr(item, "purpose", None) or getattr(item, "context", None))
                in _PURPOSE_RULES
            }
        )
        if any(uses_reader_format(getattr(item, "purpose", None), item.text) for item in items):
            if _PURPOSE_RULES["web_segment"] not in purpose_rules:
                purpose_rules.append(_PURPOSE_RULES["web_segment"])
        messages = [
            (
                "system",
                _system_prompt(self.prompt_version, purpose_rules),
            ),
            (
                "human",
                json.dumps(
                    {
                        "source_locale": source_locale,
                        "target_locale": target_locale,
                        "items": payload,
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
        try:
            kwargs = (
                {"method": self._structured_output_method}
                if self._structured_output_method is not None
                else {}
            )
            runnable = self._model.with_structured_output(_StructuredBatch, **kwargs)
            response = await runnable.ainvoke(messages)
            parsed = _StructuredBatch.model_validate(response)
        except AttributeError as exc:
            raise TranslationProviderError(
                "The configured chat model does not support structured output.",
                code="unsupported_model",
                retryable=False,
            ) from exc
        except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logging.getLogger(__name__).warning(
                "Translation parse rejected: exception_type=%s issues=%s items=%d",
                type(exc).__name__,
                [error["type"] for error in exc.errors(include_input=False)][:8]
                if isinstance(exc, ValidationError)
                else [],
                len(items),
            )
            raise TranslationProviderError(
                "The model returned an invalid structured translation response.",
                code="invalid_response",
                retryable=True,
                isolate_items=True,
            ) from exc
        except Exception as exc:  # provider SDK exception types vary by integration
            raise _provider_error(exc) from exc

        expected_ids = {item.item_id for item in items}
        actual_ids = [item.item_id for item in parsed.items]
        if set(actual_ids) != expected_ids or len(actual_ids) != len(expected_ids):
            raise TranslationProviderError(
                "The model did not return exactly one result per input item.",
                code="incomplete_response",
                retryable=True,
                isolate_items=True,
            )
        translated_by_id = {item.item_id: item.translated_text.strip() for item in parsed.items}
        return [
            TranslationResult(
                item_id=item.item_id,
                translated_text=translated_by_id[item.item_id],
            )
            for item in items
        ]


def _chunk_items(
    items: Sequence[TranslationItem],
    *,
    max_chars: int,
    max_items: int,
    prompt_version: str,
) -> Iterator[list[TranslationItem]]:
    chunk: list[TranslationItem] = []
    characters = 0
    for item in items:
        item_characters = len(item.text)
        if chunk and (len(chunk) >= max_items or characters + item_characters > max_chars):
            yield chunk
            chunk = []
            characters = 0
        chunk.append(item)
        characters += item_characters
    if chunk:
        yield chunk


def _prompt_payload(
    items: Sequence[TranslationItem],
    *,
    prompt_version: str,
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in items:
        purpose = getattr(item, "purpose", None) or getattr(item, "context", None)
        entry: dict[str, Any] = {"item_id": item.item_id, "text": item.text, "purpose": purpose}
        if prompt_version == "v1":
            entry["context"] = (
                getattr(item, "context", None)
                if getattr(item, "purpose", None) is not None
                else None
            )
        if uses_reader_format(purpose, item.text):
            entry["format"] = FORMAT_VERSION
            entry["expected_tokens"] = [match.group() for match in TOKEN.finditer(item.text)]
            if getattr(item, "validation_feedback", None) == "placeholder_mismatch":
                entry["validation_feedback"] = {
                    "code": "placeholder_mismatch",
                    "required_tokens": entry["expected_tokens"],
                }
        payload.append(entry)
    return payload


def _system_prompt(prompt_version: str, purpose_rules: Sequence[str]) -> str:
    prefix = (
        "You are a precise translation service. Translate every input item into "
        "the requested target language without commentary. Preserve URLs, code, "
        "handles, identifiers, product names, and repository names. Preserve meaning "
        "rather than source-language word order. Never omit, merge, or invent item IDs. "
    )
    context_rule = (
        "Items in a request are in reading order and may be interpreted together for "
        "continuity, but return exactly one translation for every input item. "
    )
    return (
        prefix
        + context_rule
        + "Return only a valid json object with an items array; each item must contain item_id "
        "and translated_text. " + " ".join(purpose_rules)
    )


def _provider_error(exc: Exception) -> TranslationProviderError:
    class_name = type(exc).__name__.lower()
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)

    if status_code in {401, 403} or "authentication" in class_name or "permission" in class_name:
        return TranslationProviderError(
            "The translation engine rejected its credentials.",
            code="authentication_failed",
            retryable=False,
        )
    if status_code == 404 or "notfound" in class_name:
        return TranslationProviderError(
            "The configured translation model is unavailable.",
            code="unsupported_model",
            retryable=False,
        )
    if status_code == 429 or "ratelimit" in class_name:
        return TranslationProviderError(
            "The translation engine is rate limited.",
            code="rate_limited",
            retryable=True,
            retry_after_seconds=_retry_after_seconds(response),
        )
    if status_code in {408, 425} or "timeout" in class_name:
        return TranslationProviderError(
            "The translation engine timed out.",
            code="timeout",
            retryable=True,
        )
    if isinstance(status_code, int) and status_code >= 500:
        return TranslationProviderError(
            "The translation engine is temporarily unavailable.",
            code="provider_unavailable",
            retryable=True,
            retry_after_seconds=_retry_after_seconds(response),
        )
    if status_code == 400:
        return TranslationProviderError(
            "The translation engine rejected the request.",
            code="request_rejected",
            retryable=False,
            isolate_items=True,
        )
    if "connection" in class_name or "transport" in class_name:
        return TranslationProviderError(
            "The translation engine could not be reached.",
            code="transport_error",
            retryable=True,
        )
    return TranslationProviderError(
        "The LangChain translation call failed.",
        code="provider_error",
        retryable=True,
    )


def _retry_after_seconds(response: Any) -> float | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after")
    try:
        delay = float(value) if value is not None else None
        return max(delay, 0.0) if delay is not None and math.isfinite(delay) else None
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return max((date - datetime.now(UTC)).total_seconds(), 0)
        except (TypeError, ValueError, OverflowError):
            return None
