"""Translation domain and provider integrations."""

from .domain import (
    TranslationDemand,
    TranslationEngine,
    TranslationIdentity,
    TranslationItem,
    TranslationProjection,
    TranslationProvider,
    TranslationProviderError,
    TranslationResult,
    engine_fingerprint,
    source_text_hash,
)

__all__ = [
    "TranslationDemand",
    "TranslationEngine",
    "TranslationIdentity",
    "TranslationItem",
    "TranslationProjection",
    "TranslationProvider",
    "TranslationProviderError",
    "TranslationResult",
    "engine_fingerprint",
    "source_text_hash",
]
