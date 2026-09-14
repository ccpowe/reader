"""Provider-independent translation types.

The rest of the backend depends on this small contract instead of a concrete
LLM SDK. Provider adapters use AI models behind LangChain, including future
user-managed OpenAI-compatible endpoints.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.domain.enums import TranslationPurpose, TranslationScope, TranslationStatus

AUTO_SOURCE_LOCALE = "auto"


@dataclass(frozen=True, slots=True)
class TranslationItem:
    """One stable piece of source text in a batch request."""

    item_id: str
    text: str
    purpose: str | None = None
    context_before: str | None = None
    context_after: str | None = None
    # Compatibility for older provider callers that used ``context`` as the
    # translation purpose. New caption callers use the explicit neighbor fields.
    context: str | None = None
    # Coordinator-owned correction hint; never populated from client/model text.
    validation_feedback: Literal["placeholder_mismatch"] | None = None


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """Provider output associated with an input ``item_id``."""

    item_id: str
    translated_text: str


class TranslationProviderError(RuntimeError):
    """A provider failed in a way the worker can record and retry."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = True,
        retry_after_seconds: float | None = None,
        isolate_items: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.isolate_items = isolate_items


class TranslationProvider(Protocol):
    """Batch-oriented provider contract used by the translation service."""

    name: str
    model_name: str | None

    async def translate_batch(
        self,
        items: Sequence[TranslationItem],
        *,
        source_locale: str | None,
        target_locale: str,
    ) -> list[TranslationResult]:
        """Translate every item and return results keyed by ``item_id``."""


@dataclass(frozen=True, slots=True)
class TranslationEngine:
    """Provider configuration represented by one stable cache fingerprint."""

    engine_id: str
    provider_name: str
    model_name: str | None
    prompt_version: str = "v1"
    glossary_version: str | None = None

    @property
    def fingerprint(self) -> str:
        return engine_fingerprint(
            engine_id=self.engine_id,
            provider=self.provider_name,
            model=self.model_name,
            prompt_version=self.prompt_version,
            glossary_version=self.glossary_version,
        )


@dataclass(frozen=True, slots=True)
class TranslationIdentity:
    """Hashable persistence identity shared by work and successful artifacts."""

    owner_id: UUID | None
    purpose: TranslationPurpose
    scope: TranslationScope
    source_hash: str
    source_locale: str
    target_locale: str
    engine_fingerprint: str


@dataclass(frozen=True, slots=True)
class TranslationDemand:
    """One caller-associated request for an exact source text."""

    item_id: str
    text: str
    purpose: TranslationPurpose
    target_locale: str
    context_before: str | None = None
    context_after: str | None = None
    scope: TranslationScope = TranslationScope.SHARED
    owner_id: UUID | None = None
    source_locale: str = AUTO_SOURCE_LOCALE
    priority: int = 0
    cache_generation: str | None = None

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ValueError("Translation demand item_id cannot be empty.")
        if not self.text.strip():
            raise ValueError("Translation demand text cannot be empty.")
        if not self.target_locale.strip():
            raise ValueError("Translation demand target_locale cannot be empty.")
        if self.scope == TranslationScope.SHARED and self.owner_id is not None:
            raise ValueError("Shared translation demand cannot have an owner_id.")
        if self.scope == TranslationScope.USER and self.owner_id is None:
            raise ValueError("User-scoped translation demand requires an owner_id.")

    def identity(self, engine: TranslationEngine) -> TranslationIdentity:
        source_hash = source_text_hash(self.text)
        from app.translation.rich_text import FORMAT_VERSION, uses_reader_format

        if uses_reader_format(self.purpose.value, self.text):
            source_hash = source_text_hash(FORMAT_VERSION + "\n" + self.text)
        return TranslationIdentity(
            owner_id=self.owner_id,
            purpose=self.purpose,
            scope=self.scope,
            source_hash=source_hash,
            source_locale=self.source_locale or AUTO_SOURCE_LOCALE,
            target_locale=self.target_locale,
            engine_fingerprint=(
                source_text_hash(engine.fingerprint + ":" + self.cache_generation)
                if self.cache_generation is not None
                and self.purpose in {TranslationPurpose.WEB_SEGMENT, TranslationPurpose.CAPTION}
                else engine.fingerprint
            ),
        )


@dataclass(frozen=True, slots=True)
class TranslationProjection:
    """Immediate original-text resolution plus optional work/artifact state."""

    item_id: str
    source_hash: str
    target_locale: str
    status: TranslationStatus | None
    translated_text: str | None = None
    cache_expires_at: datetime | None = None
    error_code: str | None = None
    error_retryable: bool | None = None
    retry_after_ms: int | None = None
    priority: int = 0


def source_text_hash(text: str) -> str:
    """Hash normalized source text so whitespace-only changes reuse safely."""
    normalized = normalize_source_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def contextual_source_text_hash(
    text: str,
    *,
    context_before: str | None,
    context_after: str | None,
) -> str:
    """Hash a caption together with content-derived neighboring context.

    Navigation IDs and timestamps are deliberately excluded so the same public
    caption window remains reusable, while an ambiguous fragment in a different
    sentence cannot reuse the wrong immutable Artifact.
    """
    payload = {
        "context_after": normalize_source_text(context_after or ""),
        "context_before": normalize_source_text(context_before or ""),
        "source_text": normalize_source_text(text),
        "version": "caption-context-v1",
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_source_text(text: str) -> str:
    """Normalize inconsequential whitespace while preserving source wording."""
    return " ".join(text.split())


def engine_fingerprint(
    *,
    engine_id: str,
    provider: str,
    model: str | None,
    prompt_version: str = "v1",
    glossary_version: str | None = None,
) -> str:
    """Build a stable cache identity for a provider configuration.

    JSON with sorted keys keeps the fingerprint deterministic and makes future
    fields safe to add without relying on delimiter escaping.
    """
    payload = {
        "engine_id": engine_id,
        "glossary_version": glossary_version,
        "model": model,
        "prompt_version": prompt_version,
        "provider": provider,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
