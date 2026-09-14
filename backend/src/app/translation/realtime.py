"""Ephemeral, viewport-prioritized translation for WebView text and captions.

Unlike ``interactive`` this module never creates TranslationWork. A process
keeps a short-lived coverage map from every segment identity to its shared
model batch, so an urgent request can join an already-accepted prefetch batch.
Database sessions are deliberately scoped to cache reads and artifact writes;
no connection is held while quota, provider capacity, or a shared result waits.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from time import monotonic, perf_counter
from typing import Any
from uuid import UUID, uuid4

from app.domain.enums import TranslationStatus
from app.translation.domain import (
    TranslationDemand,
    TranslationIdentity,
    TranslationItem,
    TranslationProjection,
    TranslationProvider,
)
from app.translation.executor import translate_items_with_isolation
from app.translation.interactive import ArtifactPersistence, persist_interactive_artifacts
from app.translation.projection import TextInput, TranslationContext, project_texts
from app.translation.quota import TranslationQuotaError, TranslationQuotaStorageUnavailable
from app.translation.rich_text import uses_reader_format, validate_tokens

logger = logging.getLogger(__name__)
_CANCELLATION_GRACE_SECONDS = 0.25


@dataclass(slots=True)
class RealtimeBatchTask:
    """One shared result placeholder and the unique runner that fulfils it."""

    identities: frozenset[TranslationIdentity]
    future: asyncio.Future[dict[TranslationIdentity, TranslationProjection]]
    runner: asyncio.Task[None] | None = None
    task_id: str = field(default_factory=lambda: uuid4().hex)
    retry_at: float = 0


class RealtimeCoordinator:
    """Own shared provider calls independently from individual HTTP requests."""

    def __init__(
        self,
        *,
        session_factory,
        quota=None,
        max_concurrency: int = 16,
        retry_delays: tuple[float, ...] = (2, 5),
        failure_ttl: float = 60,
        terminal_limit: int = 2048,
        max_inflight_tasks: int | None = None,
        max_inflight_chars: int = 500_000,
    ) -> None:
        self._max_inflight_tasks = (
            max_concurrency if max_inflight_tasks is None else max_inflight_tasks
        )
        if self._max_inflight_tasks < 1 or max_inflight_chars < 1 or max_concurrency < 1:
            raise ValueError("Realtime capacity limits must be positive")
        self._max_inflight_chars = max_inflight_chars
        self._inflight_chars = 0
        self._session_factory = session_factory
        self._quota = quota
        self._coverage: dict[TranslationIdentity, RealtimeBatchTask] = {}
        self._terminal: OrderedDict[TranslationIdentity, tuple[float, TranslationProjection]] = (
            OrderedDict()
        )
        self._persisting: set[TranslationIdentity] = set()
        self._retry_delays = retry_delays
        self._failure_ttl = failure_ttl
        self._terminal_limit = terminal_limit
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._owned_tasks: set[asyncio.Task[Any]] = set()
        self._closing = False

    async def resolve(
        self,
        items: Sequence[TextInput],
        *,
        context: TranslationContext,
        provider: TranslationProvider | None,
        user_id: UUID,
        wait_seconds: float,
    ) -> dict[str, TranslationProjection]:
        """Resolve cache hits and wait briefly for shared provider batches.

        The cache session is closed before any quota or provider wait begins.
        Cancelling this subscriber never cancels a shared batch future/runner.
        """
        if self._quota is not None:
            await self._quota.check_allowed(user_id)
        requested_count = len(items)
        cache_started = perf_counter()
        cached = await self._project_cached(items, context=context)
        cache_read_ms = _elapsed_ms(cache_started)
        cache_hits = sum(_succeeded(cached.get(item.item_id)) for item in items)
        if not context.can_translate or context.engine is None or provider is None:
            self._log_resolution(
                items,
                cached,
                requested_count=requested_count,
                cache_hits=cache_hits,
                terminal_hits=0,
                inflight_joins=0,
                provider_misses=0,
                cache_read_ms=cache_read_ms,
            )
            return cached

        missing = [item for item in items if not _succeeded(cached.get(item.item_id))]
        if not missing:
            self._log_resolution(
                items,
                cached,
                requested_count=requested_count,
                cache_hits=cache_hits,
                terminal_hits=0,
                inflight_joins=0,
                provider_misses=0,
                cache_read_ms=cache_read_ms,
            )
            return cached

        demands = [_demand(item, context) for item in missing]
        identities = [demand.identity(context.engine) for demand in demands]
        joined: list[RealtimeBatchTask] = []
        terminal_hits = 0
        inflight_joins = 0
        provider_misses = 0

        async with self._lock:
            uncovered: dict[TranslationIdentity, tuple[TextInput, TranslationDemand]] = {}
            for item, demand, identity in zip(missing, demands, identities, strict=True):
                terminal = self._terminal.get(identity)
                if terminal is not None and identity not in self._persisting:
                    age = monotonic() - terminal[0]
                    ttl = (
                        self._failure_ttl if terminal[1].status == TranslationStatus.FAILED else 300
                    )
                    if age >= ttl:
                        self._terminal.pop(identity, None)
                        terminal = None
                if terminal is not None:
                    created_at, projection = terminal
                    if projection.status == TranslationStatus.FAILED:
                        projection = replace(
                            projection,
                            retry_after_ms=max(
                                0, round((self._failure_ttl - (monotonic() - created_at)) * 1000)
                            ),
                        )
                    self._terminal.move_to_end(identity)
                    logger.info(
                        "Realtime terminal hit: segment_id=%r origin_segment_id=%r "
                        "age_ms=%d status=%s",
                        item.item_id[:200],
                        projection.item_id[:200],
                        round((monotonic() - created_at) * 1000),
                        projection.status.value,
                    )
                    cached[item.item_id] = _projection_for_item(projection, item)
                    terminal_hits += 1
                    continue
                existing = self._coverage.get(identity)
                if existing is not None:
                    joined.append(existing)
                    inflight_joins += 1
                    continue
                uncovered.setdefault(identity, (item, demand))

            self._trim_terminal_locked()
            chars = sum(len(item.text) for item, _demand in uncovered.values())
            if (
                uncovered
                and not self._closing
                and len(self._terminal) < self._terminal_limit
                and len(self._owned_tasks) < self._max_inflight_tasks
                and self._inflight_chars + chars <= self._max_inflight_chars
            ):
                batch = self._create_batch_locked(
                    list(uncovered.values()),
                    context=context,
                    provider=provider,
                    user_id=user_id,
                )
                joined.append(batch)
                provider_misses = len(uncovered)

        unique_batches = list({id(batch): batch for batch in joined}.values())
        if unique_batches:
            # ``asyncio.wait`` observes but does not cancel the shared futures
            # when this HTTP subscriber times out or is cancelled.
            done, _ = await asyncio.wait(
                [batch.future for batch in unique_batches],
                timeout=wait_seconds,
            )
            for future in done:
                try:
                    batch_projections = future.result()
                except (TranslationQuotaError, TranslationQuotaStorageUnavailable):
                    raise
                except asyncio.CancelledError:
                    continue
                except Exception:
                    logger.exception("Realtime translation batch escaped its error boundary.")
                    continue
                for item, identity in zip(missing, identities, strict=True):
                    projection = batch_projections.get(identity)
                    if projection is not None:
                        cached[item.item_id] = _projection_for_item(projection, item)

        result = _pending(cached, missing, context)
        for item, identity in zip(missing, identities, strict=True):
            batch = self._coverage.get(identity)
            if result[item.item_id].status == TranslationStatus.PENDING and batch is None:
                result[item.item_id] = replace(result[item.item_id], retry_after_ms=1000)
                logger.info(
                    "Realtime capacity wait: segment_id=%r retry_after_ms=1000", item.item_id[:200]
                )
            if batch and result[item.item_id].status == TranslationStatus.PENDING:
                result[item.item_id] = replace(
                    result[item.item_id],
                    retry_after_ms=max(1000, round((batch.retry_at - monotonic()) * 1000)),
                )
                logger.info(
                    "Realtime task mapping: segment_id=%r task_id=%s retry_after_ms=%d",
                    item.item_id[:200],
                    batch.task_id,
                    result[item.item_id].retry_after_ms,
                )
        self._log_resolution(
            items,
            result,
            requested_count=requested_count,
            cache_hits=cache_hits,
            terminal_hits=terminal_hits,
            inflight_joins=inflight_joins,
            provider_misses=provider_misses,
            cache_read_ms=cache_read_ms,
        )
        return result

    async def aclose(self, timeout_seconds: float) -> None:
        """Stop accepting batches and drain every coordinator-owned task."""
        async with self._lock:
            self._closing = True

        deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 0)
        while True:
            active = {task for task in self._owned_tasks if not task.done()}
            if not active:
                # Give a just-completed runner one turn to register persistence.
                await asyncio.sleep(0)
                active = {task for task in self._owned_tasks if not task.done()}
                if not active:
                    return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await self._cancel_owned_tasks_with_grace()
                return
            await asyncio.wait(active, timeout=remaining)

    async def _cancel_owned_tasks_with_grace(self) -> None:
        """Bound shutdown even when an integration suppresses cancellation."""
        loop = asyncio.get_running_loop()
        grace_deadline = loop.time() + _CANCELLATION_GRACE_SECONDS
        while True:
            active = {task for task in self._owned_tasks if not task.done()}
            if not active:
                return
            for task in active:
                task.cancel()
            remaining = grace_deadline - loop.time()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(active, timeout=remaining)
            for task in done:
                _consume_task_exception(task)
            if pending:
                break
            # A cancelled runner may have registered a final persistence task
            # before it settled. Loop inside the same grace budget to catch it.

        stubborn = {task for task in self._owned_tasks if not task.done()}
        if stubborn:
            logger.error(
                "Realtime coordinator shutdown abandoned tasks after cancellation grace: "
                "tasks=%d names=%s grace_seconds=%.2f",
                len(stubborn),
                sorted(task.get_name() for task in stubborn),
                _CANCELLATION_GRACE_SECONDS,
            )

    async def _project_cached(
        self,
        items: Sequence[TextInput],
        *,
        context: TranslationContext,
    ) -> dict[str, TranslationProjection]:
        if self._session_factory is None:
            raise RuntimeError("Realtime translation requires a database session factory.")
        async with self._session_factory() as session:
            projections = await project_texts(
                session,
                items,
                context=context,
                ensure_missing=False,
            )
            await session.commit()
            for item in items:
                projection = projections.get(item.item_id)
                if (
                    uses_reader_format(item.purpose.value, item.text)
                    and _succeeded(projection)
                    and validate_tokens(item.text, projection.translated_text)
                ):
                    logger.warning(
                        "Realtime cache rejected: segment_id=%r reason=placeholder_mismatch",
                        item.item_id[:200],
                    )
                    projections.pop(item.item_id, None)
            return projections

    def _create_batch_locked(
        self,
        item_demands: list[tuple[TextInput, TranslationDemand]],
        *,
        context: TranslationContext,
        provider: TranslationProvider,
        user_id: UUID,
    ) -> RealtimeBatchTask:
        """Publish coverage before the runner can wait for quota/provider."""
        assert context.engine is not None
        loop = asyncio.get_running_loop()
        identities = frozenset(demand.identity(context.engine) for _item, demand in item_demands)
        future: asyncio.Future[dict[TranslationIdentity, TranslationProjection]] = (
            loop.create_future()
        )
        # Retrieve an eventual exception even if every HTTP subscriber leaves.
        future.add_done_callback(_consume_future_exception)
        batch = RealtimeBatchTask(identities=identities, future=future)
        for identity in identities:
            self._coverage[identity] = batch
        chars = sum(len(item.text) for item, _demand in item_demands)
        self._inflight_chars += chars
        runner = self._create_owned_task(
            self._run_batch(
                batch,
                [item for item, _demand in item_demands],
                [demand for _item, demand in item_demands],
                context,
                provider,
                user_id,
            ),
            name="realtime-translation-batch",
        )
        runner.add_done_callback(lambda _task: self._release_batch_capacity(batch, chars))
        batch.runner = runner
        return batch

    def _release_batch_capacity(self, batch: RealtimeBatchTask, chars: int) -> None:
        self._inflight_chars -= chars
        # Also covers cancellation before the coroutine's first instruction.
        self._release_coverage_locked(batch)
        if not batch.future.done():
            batch.future.cancel()

    def _create_owned_task(
        self,
        awaitable: Awaitable[None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(awaitable, name=name)
        self._owned_tasks.add(task)
        task.add_done_callback(self._owned_task_done)
        return task

    def _owned_task_done(self, task: asyncio.Task[Any]) -> None:
        self._owned_tasks.discard(task)
        _consume_task_exception(task)

    async def _run_batch(
        self,
        batch: RealtimeBatchTask,
        items: list[TextInput],
        demands: list[TranslationDemand],
        context: TranslationContext,
        provider: TranslationProvider,
        user_id: UUID,
    ) -> None:
        try:
            await self._execute_batch(batch, items, demands, context, provider, user_id)
        except asyncio.CancelledError:
            await self._cancel_batch(batch)
            raise
        except Exception as exc:
            logger.exception("Realtime batch runner failed before settling its placeholder.")
            await self._settle_unexpected_exception(batch, exc)

    async def _execute_batch(
        self,
        batch: RealtimeBatchTask,
        items: list[TextInput],
        demands: list[TranslationDemand],
        context: TranslationContext,
        provider: TranslationProvider,
        user_id: UUID,
    ) -> None:
        assert context.engine is not None
        quota_started = perf_counter()
        try:
            if self._quota is not None:
                chars = sum(len(item.text) for item in items)
                await self._quota.reserve(
                    user_id,
                    requests=1,
                    actual_miss_chars=chars,
                    provider_miss_chars=chars,
                )
        except (TranslationQuotaError, TranslationQuotaStorageUnavailable) as exc:
            logger.warning(
                "Realtime quota rejected: purpose=%s items=%d quota_ms=%d code=%s",
                items[0].purpose.value,
                len(items),
                _elapsed_ms(quota_started),
                exc.code,
            )
            await self._settle_exception(batch, exc)
            return

        quota_ms = _elapsed_ms(quota_started)
        queue_started = perf_counter()
        provider_queue_ms = 0
        provider_ms = 0
        outcomes = {}
        attempts: dict[str, int] = {}
        remaining = [
            TranslationItem(item_id=item.item_id, text=item.text, purpose=item.purpose.value)
            for item in items
        ]
        while remaining:
            async with self._semaphore:
                provider_queue_ms += _elapsed_ms(queue_started)
                provider_started = perf_counter()
                current = await translate_items_with_isolation(
                    provider,
                    remaining,
                    source_locale=None,
                    target_locale=context.target_locale,
                    max_attempts=3,
                    actual_attempts=attempts,
                )
                provider_ms += _elapsed_ms(provider_started)
            outcomes.update(current)
            retry_items = [
                item
                for item in remaining
                if current[item.item_id].retryable
                and not current[item.item_id].succeeded
                and attempts[item.item_id] < 3
            ]
            for item in remaining:
                outcome = current[item.item_id]
                logger.info(
                    "Realtime segment outcome: task_id=%s segment_id=%r attempt=%d "
                    "status=%s reason=%s",
                    batch.task_id,
                    item.item_id[:200],
                    attempts[item.item_id],
                    "succeeded" if outcome.succeeded else "failed",
                    outcome.error_code,
                )
            if not retry_items:
                break
            delay = max(
                max(
                    self._retry_delays[
                        min(attempts[item.item_id] - 1, len(self._retry_delays) - 1)
                    ],
                    current[item.item_id].retry_after_seconds or 0,
                )
                for item in retry_items
            )
            batch.retry_at = monotonic() + delay
            logger.info(
                "Realtime retry scheduled: task_id=%s items=%d retry_after_ms=%d",
                batch.task_id,
                len(retry_items),
                round(delay * 1000),
            )
            await asyncio.sleep(delay)
            batch.retry_at = 0
            remaining = [
                replace(item, validation_feedback="placeholder_mismatch")
                if current[item.item_id].error_code == "placeholder_mismatch"
                else item
                for item in retry_items
            ]
            queue_started = perf_counter()

        succeeded_at = datetime.now(UTC)
        translated: dict[str, str] = {}
        projections: dict[TranslationIdentity, TranslationProjection] = {}
        for item, demand in zip(items, demands, strict=True):
            identity = demand.identity(context.engine)
            outcome = outcomes.get(item.item_id)
            if outcome is not None and outcome.succeeded and outcome.translated_text:
                text = outcome.translated_text.strip()
                translated[item.item_id] = text
                projections[identity] = TranslationProjection(
                    item_id=item.item_id,
                    source_hash=identity.source_hash,
                    target_locale=identity.target_locale,
                    status=TranslationStatus.SUCCEEDED,
                    translated_text=text,
                    cache_expires_at=succeeded_at + timedelta(hours=1),
                    priority=demand.priority,
                )
            else:
                projections[identity] = TranslationProjection(
                    item_id=item.item_id,
                    source_hash=identity.source_hash,
                    target_locale=identity.target_locale,
                    status=TranslationStatus.FAILED,
                    error_code=(outcome.error_code if outcome is not None else "provider_error"),
                    error_retryable=False,
                    retry_after_ms=round(self._failure_ttl * 1000),
                    priority=demand.priority,
                )

        if translated:
            self._persisting.update(
                identity
                for identity, projection in projections.items()
                if projection.status == TranslationStatus.SUCCEEDED
            )
        await self._settle_result(batch, projections)
        logger.info(
            "Realtime provider completed: purpose=%s provider=%s items=%d quota_ms=%d "
            "provider_queue_ms=%d provider_ms=%d statuses=%s",
            items[0].purpose.value,
            provider.name,
            len(items),
            quota_ms,
            provider_queue_ms,
            provider_ms,
            dict(Counter(projection.status.value for projection in projections.values())),
        )
        if translated and self._session_factory is not None:
            # Keep the admission slot until persistence releases the retained text.
            # The subscriber future was already settled above.
            await self._persist(
                ArtifactPersistence(
                    tuple(demand for demand in demands if demand.item_id in translated),
                    context.engine,
                    translated,
                    created_at=succeeded_at,
                ),
                purpose=items[0].purpose.value,
                item_count=len(translated),
            )

    async def _settle_result(
        self,
        batch: RealtimeBatchTask,
        projections: dict[TranslationIdentity, TranslationProjection],
    ) -> None:
        async with self._lock:
            for identity, projection in projections.items():
                if projection.status in {TranslationStatus.SUCCEEDED, TranslationStatus.FAILED}:
                    self._terminal[identity] = (monotonic(), projection)
            self._trim_terminal_locked()
            self._release_coverage_locked(batch)
            if not batch.future.done():
                batch.future.set_result(projections)

    def _trim_terminal_locked(self) -> None:
        for identity in list(self._terminal):
            if len(self._terminal) < self._terminal_limit:
                break
            created_at, projection = self._terminal[identity]
            protected_failure = (
                projection.status == TranslationStatus.FAILED
                and monotonic() - created_at < self._failure_ttl
            )
            if identity not in self._persisting and not protected_failure:
                self._terminal.pop(identity, None)

    async def _settle_exception(
        self,
        batch: RealtimeBatchTask,
        exc: TranslationQuotaError | TranslationQuotaStorageUnavailable,
    ) -> None:
        async with self._lock:
            self._release_coverage_locked(batch)
            if not batch.future.done():
                batch.future.set_exception(exc)

    async def _settle_unexpected_exception(
        self,
        batch: RealtimeBatchTask,
        exc: Exception,
    ) -> None:
        async with self._lock:
            self._release_coverage_locked(batch)
            if not batch.future.done():
                batch.future.set_exception(exc)

    async def _cancel_batch(self, batch: RealtimeBatchTask) -> None:
        async with self._lock:
            self._release_coverage_locked(batch)
            if not batch.future.done():
                batch.future.cancel()

    def _release_coverage_locked(self, batch: RealtimeBatchTask) -> None:
        for identity in batch.identities:
            if self._coverage.get(identity) is batch:
                self._coverage.pop(identity, None)

    async def _persist(
        self,
        persistence: ArtifactPersistence,
        *,
        purpose: str,
        item_count: int,
    ) -> None:
        started = perf_counter()
        try:
            await persist_interactive_artifacts(self._session_factory, persistence)
        finally:
            async with self._lock:
                for demand in persistence.demands:
                    self._persisting.discard(demand.identity(persistence.engine))
                self._trim_terminal_locked()
        logger.info(
            "Realtime persistence finished: purpose=%s items=%d persist_ms=%d",
            purpose,
            item_count,
            _elapsed_ms(started),
        )

    @staticmethod
    def _log_resolution(
        items: Sequence[TextInput],
        projections: dict[str, TranslationProjection],
        *,
        requested_count: int,
        cache_hits: int,
        terminal_hits: int,
        inflight_joins: int,
        provider_misses: int,
        cache_read_ms: int,
    ) -> None:
        logger.info(
            "Realtime resolution: purposes=%s requested_items=%d cache_hits=%d "
            "terminal_hits=%d inflight_joins=%d provider_misses=%d cache_read_ms=%d "
            "statuses=%s",
            dict(Counter(item.purpose.value for item in items)),
            requested_count,
            cache_hits,
            terminal_hits,
            inflight_joins,
            provider_misses,
            cache_read_ms,
            dict(
                Counter(
                    projection.status.value if projection.status is not None else "disabled"
                    for projection in projections.values()
                )
            ),
        )


def _demand(item: TextInput, context: TranslationContext) -> TranslationDemand:
    return TranslationDemand(
        item_id=item.item_id,
        text=item.text,
        purpose=item.purpose,
        target_locale=context.target_locale,
        scope=item.scope,
        owner_id=item.owner_id,
        cache_generation=context.cache_generation,
        priority=item.priority or 0,
    )


def _succeeded(projection: TranslationProjection | None) -> bool:
    return bool(
        projection
        and projection.status == TranslationStatus.SUCCEEDED
        and projection.translated_text
    )


def _projection_for_item(
    projection: TranslationProjection,
    item: TextInput,
) -> TranslationProjection:
    return TranslationProjection(
        item_id=item.item_id,
        source_hash=projection.source_hash,
        target_locale=projection.target_locale,
        status=projection.status,
        translated_text=projection.translated_text,
        cache_expires_at=projection.cache_expires_at,
        error_code=projection.error_code,
        error_retryable=projection.error_retryable,
        retry_after_ms=projection.retry_after_ms,
        priority=item.priority or projection.priority,
    )


def _pending(
    projections: dict[str, TranslationProjection],
    missing: Sequence[TextInput],
    context: TranslationContext,
) -> dict[str, TranslationProjection]:
    for item in missing:
        existing = projections.get(item.item_id)
        if existing is not None and existing.status in {
            TranslationStatus.SUCCEEDED,
            TranslationStatus.FAILED,
        }:
            continue
        projections[item.item_id] = TranslationProjection(
            item_id=item.item_id,
            source_hash=existing.source_hash if existing is not None else "",
            target_locale=context.target_locale,
            status=TranslationStatus.PENDING,
            priority=item.priority or 0,
        )
    return projections


def _consume_future_exception(future: asyncio.Future[Any]) -> None:
    try:
        future.exception()
    except asyncio.CancelledError:
        pass


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


def _elapsed_ms(started: float) -> int:
    return max(round((perf_counter() - started) * 1_000), 0)
