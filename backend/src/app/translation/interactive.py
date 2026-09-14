"""Low-latency translation for explicitly requested visible text.

Source delivery never calls this module. A translation surface invokes it only
after original text is already rendered, so provider latency can be returned
directly without coupling feed, webpage, or video availability to the vendor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import TranslationStatus
from app.translation.domain import (
    TranslationDemand,
    TranslationEngine,
    TranslationItem,
    TranslationProjection,
    TranslationProvider,
)
from app.translation.executor import translate_items_with_isolation
from app.translation.projection import TextInput, TranslationContext, project_texts
from app.translation.store import persist_translation_artifacts

logger = logging.getLogger(__name__)


async def _project_and_commit(
    session: AsyncSession,
    items: list[TextInput],
    *,
    context: TranslationContext,
    ensure_missing: bool,
) -> dict[str, TranslationProjection]:
    """End projection transactions before any external provider I/O."""
    projections = await project_texts(
        session,
        items,
        context=context,
        ensure_missing=ensure_missing,
    )
    await session.commit()
    return projections


@dataclass(frozen=True, slots=True)
class ArtifactPersistence:
    demands: tuple[TranslationDemand, ...]
    engine: TranslationEngine
    translated_texts: dict[str, str]
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class InteractiveResolution:
    projections: dict[str, TranslationProjection]
    persistence: ArtifactPersistence | None = None


async def resolve_interactive_texts(
    session: AsyncSession,
    items: list[TextInput],
    *,
    context: TranslationContext,
    provider: TranslationProvider | None,
    timeout_seconds: float,
    max_items_per_batch: int = 12,
    max_chars_per_batch: int = 5_000,
    max_concurrency: int = 2,
    reserve_budget: Callable[[Sequence[TranslationDemand]], Awaitable[object]] | None = None,
) -> InteractiveResolution:
    """Return hits immediately and translate true misses in the request path."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    projections = await _project_and_commit(
        session,
        items,
        context=context,
        ensure_missing=False,
    )
    promotion_items = [
        item
        for item in items
        if (
            (projection := projections.get(item.item_id)) is not None
            and projection.status in {TranslationStatus.PENDING, TranslationStatus.RUNNING}
            and projection.priority < (item.priority or 0)
        )
    ]
    if promotion_items:
        promoted = await _project_and_commit(
            session,
            promotion_items,
            context=context,
            ensure_missing=True,
        )
        projections.update(promoted)

    direct_items = [
        item for item in items if _requires_direct_translation(projections.get(item.item_id))
    ]
    demands = tuple(_demand(item, context) for item in direct_items)
    if reserve_budget is not None:
        # The callback reserves the request dimension even for a pure cache
        # hit. For misses it atomically reserves request, user characters,
        # and provider characters before any provider I/O starts.
        await reserve_budget(demands)
    if not context.can_translate or context.engine is None:
        return InteractiveResolution(projections)
    if not direct_items:
        return InteractiveResolution(projections)
    if provider is None:
        durable = await _project_and_commit(
            session,
            direct_items,
            context=context,
            ensure_missing=True,
        )
        return InteractiveResolution({**projections, **durable})

    demand_by_id = {demand.item_id: demand for demand in demands}
    direct_batches = _chunk_inputs(
        direct_items,
        max_chars=max_chars_per_batch,
        max_items=max_items_per_batch,
    )
    semaphore = asyncio.Semaphore(max_concurrency)
    provider_started = perf_counter()
    tasks = {
        asyncio.create_task(
            _translate_direct_batch(
                provider,
                batch,
                demand_by_id=demand_by_id,
                target_locale=context.target_locale,
                semaphore=semaphore,
            )
        ): batch
        for batch in direct_batches
    }
    try:
        done, unfinished_tasks = await asyncio.wait(tasks, timeout=timeout_seconds)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    # A task can settle between ``wait`` returning and cancellation below.
    newly_done = {task for task in unfinished_tasks if task.done()}
    done.update(newly_done)
    unfinished_tasks.difference_update(newly_done)

    unfinished_ids = {item.item_id for task in unfinished_tasks for item in tasks[task]}
    for task in unfinished_tasks:
        task.cancel()
    if unfinished_tasks:
        await asyncio.gather(*unfinished_tasks, return_exceptions=True)
        logger.warning(
            "Interactive translation deadline reached: provider=%s items=%d "
            "completed=%d unfinished=%d batches=%d provider_ms=%d timeout_seconds=%.1f",
            provider.name,
            len(direct_items),
            len(direct_items) - len(unfinished_ids),
            len(unfinished_ids),
            len(direct_batches),
            round((perf_counter() - provider_started) * 1_000),
            timeout_seconds,
        )
    else:
        logger.info(
            "Interactive translation timings: provider=%s items=%d batches=%d "
            "concurrency=%d provider_ms=%d",
            provider.name,
            len(direct_items),
            len(direct_batches),
            min(max_concurrency, len(direct_batches)),
            round((perf_counter() - provider_started) * 1_000),
        )

    outcomes = {}
    for task in done:
        outcomes.update(task.result())

    translated_texts: dict[str, str] = {}
    retry_items: list[TextInput] = []
    for demand, item in zip(demands, direct_items, strict=True):
        previous_projection = projections.get(demand.item_id)
        was_terminal_failure = (
            previous_projection is not None
            and previous_projection.status == TranslationStatus.FAILED
        )
        if demand.item_id in unfinished_ids:
            # A request-path retry of exhausted/terminal Work is deliberately
            # only one provider attempt. A deadline must not silently start a
            # fresh durable retry cycle; the next interactive request may try
            # the failed text again.
            if not was_terminal_failure:
                retry_items.append(item)
            continue
        identity = demand.identity(context.engine)
        outcome = outcomes[demand.item_id]
        if outcome.succeeded and outcome.translated_text:
            translated_text = outcome.translated_text.strip()
            translated_texts[demand.item_id] = translated_text
            projections[demand.item_id] = TranslationProjection(
                item_id=demand.item_id,
                source_hash=identity.source_hash,
                target_locale=identity.target_locale,
                status=TranslationStatus.SUCCEEDED,
                translated_text=translated_text,
                priority=demand.priority,
            )
        elif outcome.retryable and not was_terminal_failure:
            retry_items.append(item)
        else:
            projections[demand.item_id] = TranslationProjection(
                item_id=demand.item_id,
                source_hash=identity.source_hash,
                target_locale=identity.target_locale,
                status=TranslationStatus.FAILED,
                error_code=outcome.error_code,
                error_retryable=outcome.retryable,
                priority=demand.priority,
            )

    if retry_items:
        durable = await _project_and_commit(
            session,
            retry_items,
            context=context,
            ensure_missing=True,
        )
        for item in retry_items:
            projection = durable.get(item.item_id)
            outcome = outcomes.get(item.item_id)
            if projection is None or outcome is None or outcome.error_code is None:
                continue
            durable[item.item_id] = replace(
                projection,
                error_code=outcome.error_code,
                error_retryable=outcome.retryable,
                retry_after_ms=(
                    max(round(outcome.retry_after_seconds * 1_000), 0)
                    if outcome.retry_after_seconds is not None
                    else projection.retry_after_ms
                ),
            )
        projections.update(durable)

    persistence = (
        ArtifactPersistence(
            demands=tuple(demand for demand in demands if demand.item_id in translated_texts),
            engine=context.engine,
            translated_texts=translated_texts,
        )
        if translated_texts
        else None
    )
    return InteractiveResolution(projections, persistence)


async def _translate_direct_batch(
    provider: TranslationProvider,
    items: list[TextInput],
    *,
    demand_by_id: dict[str, TranslationDemand],
    target_locale: str,
    semaphore: asyncio.Semaphore,
):
    provider_items = [
        TranslationItem(
            item_id=demand_by_id[item.item_id].item_id,
            text=demand_by_id[item.item_id].text,
            purpose=str(demand_by_id[item.item_id].purpose),
            context_before=demand_by_id[item.item_id].context_before,
            context_after=demand_by_id[item.item_id].context_after,
        )
        for item in items
    ]
    async with semaphore:
        return await translate_items_with_isolation(
            provider,
            provider_items,
            source_locale=None,
            target_locale=target_locale,
        )


def _chunk_inputs(
    items: list[TextInput],
    *,
    max_chars: int,
    max_items: int,
) -> list[list[TextInput]]:
    if max_chars < 1 or max_items < 1:
        raise ValueError("Interactive batch limits must be positive.")
    batches: list[list[TextInput]] = []
    batch: list[TextInput] = []
    characters = 0
    for item in items:
        item_characters = (
            len(item.text) + len(item.context_before or "") + len(item.context_after or "")
        )
        if batch and (len(batch) >= max_items or characters + item_characters > max_chars):
            batches.append(batch)
            batch = []
            characters = 0
        batch.append(item)
        characters += item_characters
    if batch:
        batches.append(batch)
    return batches


async def persist_interactive_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
    persistence: ArtifactPersistence,
) -> None:
    """Best-effort post-response publication for direct interactive results."""
    try:
        async with session_factory() as session:
            await persist_translation_artifacts(
                session,
                persistence.demands,
                persistence.translated_texts,
                engine=persistence.engine,
                created_at=persistence.created_at,
            )
    except Exception:
        # The caller already received valid provider output. Losing this cache
        # write may repeat future work but must not retroactively fail the UI.
        logger.exception("Unable to persist direct interactive translation artifacts.")


def _requires_direct_translation(projection: TranslationProjection | None) -> bool:
    if projection is None or projection.status is None:
        return True
    if projection.status in {TranslationStatus.CANCELLED, TranslationStatus.FAILED}:
        return True
    return projection.status == TranslationStatus.SUCCEEDED and not projection.translated_text


def _demand(item: TextInput, context: TranslationContext) -> TranslationDemand:
    return TranslationDemand(
        item_id=item.item_id,
        text=item.text,
        purpose=item.purpose,
        target_locale=context.target_locale,
        context_before=item.context_before,
        context_after=item.context_after,
        scope=item.scope,
        owner_id=item.owner_id,
        cache_generation=context.cache_generation,
        priority=item.priority or 0,
    )
