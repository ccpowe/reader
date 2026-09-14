"""Translation Projection and delivery rules for Reader callers."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from time import monotonic, perf_counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    TranslationPriority,
    TranslationProviderMode,
    TranslationPurpose,
    TranslationScope,
    TranslationStatus,
)
from app.storage.models import TranslationPreference
from app.translation.demand import resolve_translation_demands
from app.translation.domain import TranslationDemand, TranslationEngine, TranslationProjection
from app.translation.engines import default_engine_descriptor, engine_descriptor

SUPPORTED_TRANSLATION_LOCALES = (
    "zh-CN",
    "en",
    "ja",
    "ko",
    "de",
    "fr",
    "es",
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TranslationContext:
    target_locale: str
    enabled: bool
    engine: TranslationEngine | None
    engine_label: str | None = None
    engine_available: bool = True
    engine_error_code: str | None = None
    cache_generation: str | None = None

    @property
    def can_translate(self) -> bool:
        return bool(self.enabled and self.engine is not None and self.engine_available)


@dataclass(frozen=True, slots=True)
class TitleInput:
    item_id: str
    text: str


@dataclass(frozen=True, slots=True)
class TextInput:
    """One caller-owned segment with an explicit product purpose and scope."""

    item_id: str
    text: str
    purpose: TranslationPurpose
    context_before: str | None = None
    context_after: str | None = None
    scope: TranslationScope = TranslationScope.SHARED
    owner_id: UUID | None = None
    priority: int | None = None


# Compatibility name for title-only callers while the projection API expands
# to article, ranking, webpage, and caption surfaces.
TitleTranslationContext = TranslationContext
_CONTEXT_CACHE_MAX_USERS = 1_024
_translation_context_cache: OrderedDict[UUID, tuple[float, TranslationContext]] = OrderedDict()


async def get_translation_context(
    session: AsyncSession,
    user_id: UUID,
    *,
    fresh: bool = False,
) -> TranslationContext:
    """Resolve one user's application-managed engine and target locale."""
    from app.core.settings import get_settings

    settings = get_settings()
    cache_seconds = settings.translation_context_cache_seconds
    cached = _translation_context_cache.get(user_id) if cache_seconds and not fresh else None
    if cached is not None and cached[0] > monotonic():
        _translation_context_cache.move_to_end(user_id)
        return cached[1]
    _translation_context_cache.pop(user_id, None)
    preference = await session.scalar(
        select(TranslationPreference)
        .where(TranslationPreference.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    return cache_translation_context(user_id, preference)


def cache_translation_context(
    user_id: UUID,
    preference: TranslationPreference | None,
) -> TranslationContext:
    """Project and cache a preference already loaded by another endpoint."""
    from app.core.settings import get_settings

    settings = get_settings()
    if preference is None or preference.provider_mode == TranslationProviderMode.APP_DEFAULT:
        descriptor = default_engine_descriptor(settings)
    elif preference.provider_mode == TranslationProviderMode.APP_MANAGED:
        descriptor = (
            engine_descriptor(settings, preference.engine_id) if preference.engine_id else None
        )
    else:
        descriptor = None
    target_locale = (
        preference.target_locale
        if preference is not None and preference.target_locale
        else _default_target_locale()
    )
    enabled = preference.is_enabled if preference is not None else True
    if not enabled:
        engine_error_code = None
    elif descriptor is None:
        engine_error_code = "engine_unavailable"
    elif not descriptor.available:
        engine_error_code = descriptor.unavailable_reason or "engine_unavailable"
    else:
        engine_error_code = None
    context = TranslationContext(
        target_locale=target_locale,
        enabled=enabled,
        engine=descriptor.to_engine() if descriptor is not None else None,
        engine_label=descriptor.label if descriptor is not None else None,
        engine_available=bool(descriptor is not None and descriptor.available),
        engine_error_code=engine_error_code,
        cache_generation=(
            preference.updated_at.isoformat()
            if preference is not None and preference.updated_at
            else "default"
        ),
    )
    cache_seconds = settings.translation_context_cache_seconds
    if cache_seconds:
        _translation_context_cache[user_id] = (monotonic() + cache_seconds, context)
        _translation_context_cache.move_to_end(user_id)
        while len(_translation_context_cache) > _CONTEXT_CACHE_MAX_USERS:
            _translation_context_cache.popitem(last=False)
    return context


def invalidate_translation_context(user_id: UUID) -> None:
    """Forget one process-local preference projection after a mutation."""
    _translation_context_cache.pop(user_id, None)


async def get_title_translation_context(
    session: AsyncSession,
    user_id: UUID,
) -> TranslationContext:
    """Compatibility wrapper for existing feed and saved title callers."""
    return await get_translation_context(session, user_id)


async def project_texts(
    session: AsyncSession,
    items: Sequence[TextInput],
    *,
    context: TranslationContext,
    ensure_missing: bool = True,
    priority: TranslationPriority = TranslationPriority.INTERACTIVE,
) -> dict[str, TranslationProjection]:
    """Resolve mixed-purpose segments without taking ownership of the transaction."""
    if not items:
        return {}
    if not context.can_translate:
        return {
            item.item_id: TranslationProjection(
                item_id=item.item_id,
                source_hash="",
                target_locale=context.target_locale,
                status=(
                    TranslationStatus.FAILED
                    if context.enabled and context.engine_error_code
                    else None
                ),
                error_code=context.engine_error_code,
                error_retryable=False if context.engine_error_code else None,
            )
            for item in items
        }
    demands = [
        TranslationDemand(
            item_id=item.item_id,
            text=item.text,
            purpose=item.purpose,
            target_locale=context.target_locale,
            context_before=item.context_before,
            context_after=item.context_after,
            scope=item.scope,
            owner_id=item.owner_id,
            cache_generation=context.cache_generation,
            priority=item.priority if item.priority is not None else int(priority),
        )
        for item in items
        if item.text.strip()
    ]
    started = perf_counter()
    resolution = await resolve_translation_demands(
        session,
        demands,
        engine=context.engine,
        ensure_missing=ensure_missing,
    )
    elapsed_ms = max(round((perf_counter() - started) * 1_000), 0)
    logger.log(
        logging.WARNING if elapsed_ms >= 1_000 else logging.DEBUG,
        "Translation projection timings: items=%d ensure_missing=%s changed=%d total_ms=%d",
        len(demands),
        ensure_missing,
        resolution.work_changed,
        elapsed_ms,
    )
    return resolution.projections


async def project_titles(
    session: AsyncSession,
    items: Sequence[TitleInput],
    *,
    context: TranslationContext,
    ensure_missing: bool = True,
    priority: TranslationPriority = TranslationPriority.INTERACTIVE,
) -> dict[str, TranslationProjection]:
    """Resolve titles; the application boundary commits any created demand."""
    return await project_texts(
        session,
        [
            TextInput(
                item_id=item.item_id,
                text=item.text,
                purpose=TranslationPurpose.TITLE,
                scope=TranslationScope.SHARED,
            )
            for item in items
        ],
        context=context,
        ensure_missing=ensure_missing,
        priority=priority,
    )


def translation_response_values(
    context: TranslationContext,
    projection: TranslationProjection | None,
) -> tuple[str | None, str | None, str | None]:
    if not context.enabled:
        return None, None, None
    if projection is None:
        return None, context.target_locale, None
    status = projection.status.value if projection.status is not None else None
    translated = (
        projection.translated_text.strip()
        if projection.status == TranslationStatus.SUCCEEDED
        and projection.translated_text
        and projection.translated_text.strip()
        else None
    )
    return translated, context.target_locale, status


def _default_target_locale() -> str:
    # Keep settings construction local so callers never need translation config.
    from app.core.settings import get_settings

    return get_settings().translation_default_target_locale
