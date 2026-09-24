"""Application-managed translation engine catalog.

The catalog maps stable product-facing route IDs to configured provider models.
Historical route IDs remain opaque compatibility keys for saved preferences and
cache identities. Model configuration, not the route ID, selects the actual model.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib.util import find_spec

from app.core.settings import Settings
from app.llm.codex_auth import subscription_unavailable_reason
from app.translation.domain import TranslationEngine

LEGACY_MANAGED_PROMPT_VERSIONS = ("v1", "v2-caption-context")


@dataclass(frozen=True, slots=True)
class EngineDescriptor:
    engine_id: str
    label: str
    adapter_kind: str
    provider_name: str
    model_name: str
    prompt_version: str
    selectable: bool
    available: bool
    unavailable_reason: str | None = None

    def to_engine(self) -> TranslationEngine:
        return TranslationEngine(
            engine_id=self.engine_id,
            provider_name=self.provider_name,
            model_name=self.model_name,
            prompt_version=self.prompt_version,
        )


def managed_engine_catalog(settings: Settings) -> tuple[EngineDescriptor, ...]:
    """Return stable catalog entries decorated with runtime availability."""
    deepseek_package_available = find_spec("langchain_deepseek") is not None
    deepseek_has_key = settings.deepseek_api_key is not None
    deepseek_available = deepseek_package_available and deepseek_has_key
    if not deepseek_package_available:
        deepseek_reason = "langchain_deepseek_not_installed"
    elif not deepseek_has_key:
        deepseek_reason = "missing_deepseek_api_key"
    else:
        deepseek_reason = None

    if find_spec("langchain_openai") is None:
        openrouter_reason = "langchain_openai_not_installed"
    elif settings.openrouter_api_key is None:
        openrouter_reason = "missing_openrouter_api_key"
    else:
        openrouter_reason = None

    codex_reason = subscription_unavailable_reason(settings.codex_subscription_auth_file)
    return (
        EngineDescriptor(
            engine_id="deepseek-v4-flash",
            label=f"DeepSeek · {settings.deepseek_model}",
            adapter_kind="langchain",
            provider_name="deepseek",
            model_name=settings.deepseek_model,
            prompt_version=settings.translation_prompt_version,
            selectable=True,
            available=deepseek_available,
            unavailable_reason=deepseek_reason,
        ),
        EngineDescriptor(
            engine_id="openrouter-minimax-m3",
            label=f"OpenRouter · {settings.openrouter_model}",
            adapter_kind="langchain",
            provider_name="openrouter",
            model_name=settings.openrouter_model,
            prompt_version=settings.translation_prompt_version,
            selectable=True,
            available=openrouter_reason is None,
            unavailable_reason=openrouter_reason,
        ),
        EngineDescriptor(
            engine_id="codex-subscription",
            label=f"Codex · {settings.codex_subscription_model}",
            adapter_kind="langchain",
            provider_name="codex-subscription",
            model_name=settings.codex_subscription_model,
            prompt_version=settings.translation_prompt_version,
            selectable=True,
            available=codex_reason is None,
            unavailable_reason=codex_reason,
        ),
    )


def managed_execution_engine_catalog(settings: Settings) -> tuple[EngineDescriptor, ...]:
    """Return every exact engine configuration this worker can execute safely.

    Product selection exposes only the current descriptor from
    :func:`managed_engine_catalog`. Executors additionally retain explicit
    legacy prompt implementations so pending Work created by an older release
    is never run through a differently-versioned prompt.
    """
    descriptors: list[EngineDescriptor] = []
    for current in managed_engine_catalog(settings):
        descriptors.append(current)
        for prompt_version in LEGACY_MANAGED_PROMPT_VERSIONS:
            if prompt_version == current.prompt_version:
                continue
            descriptors.append(
                replace(
                    current,
                    label=f"{current.label} ({prompt_version} compatibility)",
                    prompt_version=prompt_version,
                    selectable=False,
                )
            )
    return tuple(descriptors)


def engine_descriptor(settings: Settings, engine_id: str) -> EngineDescriptor | None:
    normalized = engine_id.strip().lower()
    return next(
        (
            descriptor
            for descriptor in managed_engine_catalog(settings)
            if descriptor.engine_id == normalized
        ),
        None,
    )


def default_engine_descriptor(settings: Settings) -> EngineDescriptor | None:
    engine_id = settings.translation_default_engine_id.strip().lower()
    if engine_id in {"", "disabled", "none"}:
        return None
    return engine_descriptor(settings, engine_id)
