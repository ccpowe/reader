"""Deep Translation Demand Module.

Callers submit product text and receive immediate projections. Identity,
artifact reuse, work creation, priority escalation, and provider configuration
remain local to this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.domain.enums import TranslationStatus
from app.translation.domain import (
    TranslationDemand,
    TranslationEngine,
    TranslationProjection,
)
from app.translation.engines import default_engine_descriptor
from app.translation.store import (
    load_and_ensure_translation_states,
    load_translation_states,
)


@dataclass(frozen=True, slots=True)
class TranslationResolution:
    projections: dict[str, TranslationProjection]
    work_changed: int = 0


async def resolve_translation_demands(
    session: AsyncSession,
    demands: Sequence[TranslationDemand],
    *,
    engine: TranslationEngine,
    ensure_missing: bool = True,
) -> TranslationResolution:
    """Resolve cache hits and optionally ensure every miss has durable work."""
    if not demands:
        return TranslationResolution({})
    if ensure_missing:
        states, work_changed = await load_and_ensure_translation_states(
            session,
            demands,
            engine=engine,
        )
    else:
        identities = {demand.identity(engine) for demand in demands}
        states = await load_translation_states(session, identities, engine=engine)
        work_changed = 0

    projections: dict[str, TranslationProjection] = {}
    for demand in demands:
        identity = demand.identity(engine)
        state = states.get(identity)
        if state is not None and state.is_artifact:
            projections[demand.item_id] = TranslationProjection(
                item_id=demand.item_id,
                source_hash=identity.source_hash,
                target_locale=identity.target_locale,
                status=TranslationStatus.SUCCEEDED,
                translated_text=state.translated_text,
                cache_expires_at=state.cache_expires_at,
                priority=state.priority,
            )
            continue
        status = (
            state.status
            if state is not None
            else TranslationStatus.PENDING
            if ensure_missing
            else None
        )
        projections[demand.item_id] = TranslationProjection(
            item_id=demand.item_id,
            source_hash=identity.source_hash,
            target_locale=identity.target_locale,
            status=status,
            error_code=state.error_code if state is not None else None,
            error_retryable=state.error_retryable if state is not None else None,
            retry_after_ms=_retry_after_ms(state),
            priority=state.priority if state is not None else demand.priority,
        )
    return TranslationResolution(projections, work_changed)


def default_translation_engine(settings: Settings | None = None) -> TranslationEngine | None:
    """Resolve the application-managed engine without constructing its Adapter."""
    settings = settings or get_settings()
    descriptor = default_engine_descriptor(settings)
    return descriptor.to_engine() if descriptor is not None else None


def _retry_after_ms(work) -> int | None:
    if work is None or TranslationStatus(work.status) != TranslationStatus.PENDING:
        return None
    available_at = work.available_at
    if available_at is None:
        return None
    return max(round((available_at - datetime.now(UTC)).total_seconds() * 1000), 0)
