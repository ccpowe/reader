"""Construct application-managed translation providers from the engine catalog."""

from __future__ import annotations

import asyncio
import logging

from app.core.settings import Settings
from app.llm.factory import ChatModelConfigurationError, build_chat_model
from app.translation.domain import TranslationProvider
from app.translation.engines import (
    EngineDescriptor,
    default_engine_descriptor,
    managed_engine_catalog,
    managed_execution_engine_catalog,
)
from app.translation.providers.langchain import LangChainChatModelProvider

logger = logging.getLogger(__name__)


# Preserve the translation-facing error and constructor names for existing callers.
TranslationProviderConfigurationError = ChatModelConfigurationError


def build_translation_provider(
    settings: Settings,
    descriptor: EngineDescriptor | None = None,
    *,
    request_semaphore: asyncio.Semaphore | None = None,
) -> TranslationProvider | None:
    """Build one catalog engine, defaulting to the application default."""
    descriptor = descriptor or default_engine_descriptor(settings)
    if descriptor is None:
        return None
    if not descriptor.available:
        raise TranslationProviderConfigurationError(
            f"Translation engine {descriptor.engine_id} is unavailable: "
            f"{descriptor.unavailable_reason or 'unknown_reason'}."
        )
    if descriptor.adapter_kind == "langchain":
        return LangChainChatModelProvider(
            build_chat_model(settings, descriptor),
            max_chars_per_request=settings.translation_llm_max_chars_per_request,
            max_concurrent_requests=settings.translation_provider_max_concurrency,
            max_items_per_request=settings.translation_llm_max_items_per_request,
            model_name=descriptor.model_name,
            prompt_version=descriptor.prompt_version,
            provider_name=descriptor.provider_name,
            structured_output_method="json_mode",
            request_semaphore=request_semaphore,
        )
    raise TranslationProviderConfigurationError(
        f"Unsupported translation adapter: {descriptor.adapter_kind}."
    )


def build_translation_providers(
    settings: Settings,
    *,
    require_default: bool = False,
) -> dict[str, TranslationProvider]:
    """Build every available managed engine, keyed by immutable engine ID."""
    default = _validate_required_default(settings) if require_default else None
    providers: dict[str, TranslationProvider] = {}
    semaphores_by_account: dict[tuple[str, str], asyncio.Semaphore] = {}
    for descriptor in managed_engine_catalog(settings):
        if not descriptor.available:
            continue
        semaphore = semaphores_by_account.setdefault(
            _provider_account_key(descriptor),
            asyncio.Semaphore(settings.translation_provider_max_concurrency),
        )
        provider = build_translation_provider(
            settings,
            descriptor,
            request_semaphore=semaphore,
        )
        if provider is not None:
            providers[descriptor.engine_id] = provider

    if default is not None:
        if default.engine_id not in providers:
            raise TranslationProviderConfigurationError(
                f"Default translation engine {default.engine_id} is unavailable: "
                f"{default.unavailable_reason or 'unknown_reason'}."
            )
    return providers


def build_translation_provider_routes(
    settings: Settings,
    *,
    require_default: bool = False,
) -> dict[str, TranslationProvider]:
    """Build exact executor routes keyed by immutable engine fingerprint."""
    default = _validate_required_default(settings) if require_default else None
    routes: dict[str, TranslationProvider] = {}
    providers_by_adapter: dict[tuple[str, str, str], TranslationProvider] = {}
    semaphores_by_account: dict[tuple[str, str], asyncio.Semaphore] = {}
    for descriptor in managed_execution_engine_catalog(settings):
        if not descriptor.available:
            continue
        adapter_key = (
            descriptor.adapter_kind,
            descriptor.provider_name,
            descriptor.model_name,
        )
        account_key = _provider_account_key(descriptor)
        provider = providers_by_adapter.get(adapter_key)
        if provider is None or descriptor.adapter_kind == "langchain":
            semaphore = semaphores_by_account.setdefault(
                account_key,
                asyncio.Semaphore(settings.translation_provider_max_concurrency),
            )
            provider = build_translation_provider(
                settings,
                descriptor,
                request_semaphore=semaphore,
            )
            if provider is not None and descriptor.adapter_kind != "langchain":
                providers_by_adapter[adapter_key] = provider
        if provider is None:
            continue
        fingerprint = descriptor.to_engine().fingerprint
        if fingerprint in routes:
            raise TranslationProviderConfigurationError(
                f"Duplicate translation engine fingerprint for {descriptor.engine_id}."
            )
        routes[fingerprint] = provider

    if default is not None:
        if default.to_engine().fingerprint not in routes:
            raise TranslationProviderConfigurationError(
                f"Default translation engine {default.engine_id} is unavailable: "
                f"{default.unavailable_reason or 'unknown_reason'}."
            )
    return routes


def _provider_account_key(descriptor: EngineDescriptor) -> tuple[str, str]:
    """Identify the configured credential/quota account independently of model or prompt."""
    return descriptor.adapter_kind, descriptor.provider_name


def _validate_required_default(settings: Settings) -> EngineDescriptor:
    """Reject an unusable required route before constructing any closeable Adapter."""
    default = default_engine_descriptor(settings)
    if default is None:
        raise TranslationProviderConfigurationError(
            "APP_TRANSLATION_DEFAULT_ENGINE_ID must name a managed engine."
        )
    if not default.available:
        raise TranslationProviderConfigurationError(
            f"Default translation engine {default.engine_id} is unavailable: "
            f"{default.unavailable_reason or 'unknown_reason'}."
        )
    return default


async def close_translation_providers(
    providers: dict[str, TranslationProvider],
    *,
    timeout_seconds: float | None = None,
) -> None:
    """Close every unique Adapter even when one cleanup hook is broken."""
    seen: set[int] = set()
    cancellation: asyncio.CancelledError | None = None
    for provider in providers.values():
        if id(provider) in seen:
            continue
        seen.add(id(provider))
        close = getattr(provider, "aclose", None)
        if close is not None:
            try:
                if timeout_seconds is None:
                    await close()
                else:
                    async with asyncio.timeout(timeout_seconds):
                        await close()
            except TimeoutError:
                logger.error(
                    "Translation Provider cleanup timed out and was cancelled: provider=%s",
                    getattr(provider, "name", type(provider).__name__),
                )
            except asyncio.CancelledError as exc:
                cancellation = exc
            except BaseException:
                logger.exception(
                    "Translation Provider cleanup failed and was isolated: provider=%s",
                    getattr(provider, "name", type(provider).__name__),
                )
    if cancellation is not None:
        raise cancellation
