"""Application-owned translation requests and their transaction boundaries."""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.settings import get_settings
from app.domain.enums import TranslationPriority, TranslationPurpose, TranslationScope
from app.storage.locks import acquire_user_transaction_lock
from app.storage.models import Content, SourceEntry, SourceSubscription, UserSavedContent
from app.translation.domain import TranslationDemand, TranslationProjection, TranslationProvider
from app.translation.interactive import persist_interactive_artifacts, resolve_interactive_texts
from app.translation.lifecycle import clear_user_ephemeral_cache, ephemeral_fingerprint
from app.translation.projection import (
    TextInput,
    TitleInput,
    TranslationContext,
    ensure_title_translations,
    get_translation_context,
    translation_response_values,
)
from app.translation.quota import TranslationQuotaStore
from app.translation.realtime import RealtimeCoordinator
from app.translation.rich_text import uses_reader_format

logger = logging.getLogger(__name__)
_EPHEMERAL_PURPOSES = frozenset({TranslationPurpose.WEB_SEGMENT, TranslationPurpose.CAPTION})
_PREFETCH_PURPOSES = frozenset(
    {TranslationPurpose.RANKING_TITLE, TranslationPurpose.RANKING_DESCRIPTION}
)


class TranslationStorageBusy(RuntimeError):
    """Storage could not serve request preparation, execution or delivery."""


@dataclass(frozen=True, slots=True)
class SegmentInput:
    segment_id: str
    text: str
    purpose: TranslationPurpose


@dataclass(slots=True)
class SegmentResolution:
    context: TranslationContext
    projections: dict[str, TranslationProjection]


@dataclass(slots=True)
class TitleResolution:
    context: TranslationContext
    content_ids: list[UUID]
    projections: dict[str, TranslationProjection]


async def resolve_accessible_titles(
    session: AsyncSession,
    user_id: UUID,
    content_ids: Sequence[UUID],
) -> TitleResolution:
    """Resolve only titles in active subscriptions or the user's saved list."""
    requested_ids = list(dict.fromkeys(content_ids))
    active_subscription = exists(
        select(SourceEntry.id)
        .join(SourceSubscription, SourceSubscription.source_id == SourceEntry.source_id)
        .where(
            SourceEntry.content_id == Content.id,
            SourceSubscription.user_id == user_id,
            SourceSubscription.is_enabled.is_(True),
        )
    )
    saved = exists(
        select(UserSavedContent.id).where(
            UserSavedContent.content_id == Content.id,
            UserSavedContent.user_id == user_id,
        )
    )
    contents = list(
        await session.scalars(
            select(Content).where(Content.id.in_(requested_ids), or_(active_subscription, saved))
        )
    )
    content_by_id = {content.id: content for content in contents}
    ordered = [
        content_by_id[content_id] for content_id in requested_ids if content_id in content_by_id
    ]
    context = await get_translation_context(session, user_id)
    projections = await ensure_title_translations(
        session,
        [TitleInput(item_id=str(content.id), text=content.title) for content in ordered],
        context=context,
    )
    await session.commit()
    return TitleResolution(context, [content.id for content in ordered], projections)


async def resolve_segments(
    session: AsyncSession,
    user_id: UUID,
    segments: Sequence[SegmentInput],
    *,
    providers: Mapping[str, TranslationProvider],
    coordinator: RealtimeCoordinator | None,
    quota: TranslationQuotaStore | None,
    session_factory: async_sessionmaker[AsyncSession] | None,
    defer: Callable[[Callable[[], Awaitable[None]]], None],
    request_id: str = "",
) -> SegmentResolution:
    """Own the preference, demand, execution and delivery sequence for UI text.

    Realtime runners belong to the coordinator, not to the HTTP subscriber.
    Standard provider successes are returned before deferred artifact publication.
    """
    try:
        await acquire_user_transaction_lock(
            session, namespace="translation-preference", user_id=user_id
        )
        context = await get_translation_context(session, user_id, fresh=True)
        await clear_user_ephemeral_cache(
            session, user_id, keep_fingerprint=ephemeral_fingerprint(context)
        )
        # Never hold the preference transaction through quota or external I/O.
        await session.commit()
    except SQLAlchemyTimeoutError as exc:
        raise TranslationStorageBusy() from exc

    items = [
        TextInput(
            item_id=segment.segment_id,
            text=segment.text,
            purpose=segment.purpose,
            scope=(
                TranslationScope.USER
                if segment.purpose in _EPHEMERAL_PURPOSES
                else TranslationScope.SHARED
            ),
            owner_id=user_id if segment.purpose in _EPHEMERAL_PURPOSES else None,
            priority=int(
                TranslationPriority.PREFETCH
                if segment.purpose in _PREFETCH_PURPOSES
                else TranslationPriority.INTERACTIVE
            ),
        )
        for segment in segments
    ]
    provider = providers.get(context.engine.engine_id) if context.engine is not None else None
    settings = get_settings()
    realtime_items: list[TextInput] = []
    standard_items: list[TextInput] = []
    for item in items:
        if item.purpose in _EPHEMERAL_PURPOSES or uses_reader_format(item.purpose.value, item.text):
            realtime_items.append(item)
        else:
            standard_items.append(item)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request_id):
        request_id = uuid4().hex
    for item in items:
        logger.info(
            "Translation request mapping: request_id=%s segment_id=%r format=reader-rich-text-v1",
            request_id,
            item.item_id,
        )

    projections: dict[str, TranslationProjection] = {}
    if realtime_items:
        if coordinator is None:
            raise RuntimeError("Realtime translation coordinator is unavailable")
        try:
            projections.update(
                await coordinator.resolve(
                    realtime_items,
                    context=context,
                    provider=provider,
                    user_id=user_id,
                    wait_seconds=settings.translation_realtime_wait_seconds,
                )
            )
        except SQLAlchemyTimeoutError as exc:
            raise TranslationStorageBusy() from exc
    if standard_items:
        try:
            resolution = await resolve_interactive_texts(
                session,
                standard_items,
                context=context,
                provider=provider,
                timeout_seconds=settings.translation_interactive_timeout_seconds,
                max_items_per_batch=settings.translation_interactive_max_items_per_batch,
                max_chars_per_batch=settings.translation_interactive_max_chars_per_batch,
                max_concurrency=settings.translation_interactive_max_concurrency,
                reserve_budget=(
                    _reserve_request_and_misses(quota, user_id) if quota is not None else None
                ),
            )
        except SQLAlchemyTimeoutError as exc:
            raise TranslationStorageBusy() from exc
        if resolution.persistence is not None and session_factory is not None:
            persistence = resolution.persistence

            async def publish() -> None:
                await persist_interactive_artifacts(session_factory, persistence)

            defer(publish)
        projections.update(resolution.projections)

    # The preference may have changed during provider I/O. Never deliver an
    # old user-scoped web/caption response after that change.
    try:
        latest_context = await get_translation_context(session, user_id, fresh=True)
        await session.commit()
    except SQLAlchemyTimeoutError as exc:
        raise TranslationStorageBusy() from exc
    route_changed = (
        latest_context.cache_generation != context.cache_generation
        or latest_context.engine != context.engine
        or not latest_context.enabled
    )
    if route_changed:
        for segment in segments:
            if (
                segment.purpose in _EPHEMERAL_PURPOSES
                and (projection := projections.get(segment.segment_id)) is not None
            ):
                projections[segment.segment_id] = replace(
                    projection,
                    translated_text=None,
                    cache_expires_at=None,
                    status=None,
                    error_code="translation_route_changed",
                )
    logger.info(
        "Translation segments resolved: purposes=%s items=%d characters=%d statuses=%s errors=%s",
        dict(Counter(segment.purpose.value for segment in segments)),
        len(segments),
        sum(len(segment.text) for segment in segments),
        dict(
            Counter(
                translation_response_values(context, projections.get(segment.segment_id))[2]
                or "disabled"
                for segment in segments
            )
        ),
        dict(
            Counter(
                projection.error_code
                for projection in projections.values()
                if projection.error_code
            )
        ),
    )
    return SegmentResolution(context, projections)


def _reserve_request_and_misses(quota: TranslationQuotaStore, user_id: UUID):
    async def reserve(demands: Sequence[TranslationDemand]) -> object:
        actual_miss_chars = sum(
            len(demand.text) + len(demand.context_before or "") + len(demand.context_after or "")
            for demand in demands
        )
        return await quota.reserve(
            user_id,
            requests=1,
            actual_miss_chars=actual_miss_chars,
            provider_miss_chars=actual_miss_chars,
        )

    return reserve
