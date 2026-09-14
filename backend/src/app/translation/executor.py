"""Translation Executor Module.

The executor owns provider routing, batch isolation, retry classification, and
lease completion. Worker processes only decide when to invoke this module.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.storage.models import TranslationWork
from app.translation.domain import (
    AUTO_SOURCE_LOCALE,
    TranslationItem,
    TranslationProvider,
    TranslationProviderError,
)
from app.translation.rich_text import token_diagnostics, uses_reader_format, validate_tokens
from app.translation.store import (
    WorkOutcome,
    claim_translation_work,
    persist_work_outcomes,
    renew_translation_work_leases,
)

logger = logging.getLogger(__name__)


class TranslationExecutor:
    """Claim and fulfil durable work through registered provider Adapters."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        providers_by_fingerprint: dict[str, TranslationProvider],
        *,
        batch_size: int = 50,
        lane_name: str = "all",
        lease_seconds: int = 120,
        max_attempts: int = 8,
        min_priority: int | None = None,
        max_priority: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._providers_by_fingerprint = providers_by_fingerprint
        self.batch_size = batch_size
        self.lane_name = lane_name
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._min_priority = min_priority
        self._max_priority = max_priority

    async def run_once(self) -> int:
        async with self.open_session() as session:
            return await self.run_once_with_session(session)

    def open_session(self):
        """Open one Store session; long-running workers may keep it checked out."""
        return self._session_factory()

    async def run_once_with_session(self, session: AsyncSession) -> int:
        """Run a pass on an existing transaction-free Store connection."""
        claim_started = perf_counter()
        claimed = await claim_translation_work(
            session,
            engine_fingerprints=list(self._providers_by_fingerprint),
            limit=self.batch_size,
            lease_seconds=self._lease_seconds,
            min_priority=self._min_priority,
            max_priority=self._max_priority,
        )
        claim_ms = _elapsed_ms(claim_started)
        if not claimed:
            return 0

        async with _translation_lease_heartbeat(
            self._session_factory,
            claimed,
            lease_seconds=self._lease_seconds,
        ):
            return await self._run_claimed_work(session, claimed, claim_ms=claim_ms)

    async def _run_claimed_work(
        self,
        session: AsyncSession,
        claimed: Sequence[TranslationWork],
        *,
        claim_ms: int,
    ) -> int:
        """Execute an already-leased batch while its heartbeat runs independently."""

        grouped: dict[tuple[object, ...], list[TranslationWork]] = defaultdict(list)
        for work in sorted(
            claimed,
            key=lambda item: (-item.priority, item.available_at, item.created_at),
        ):
            grouped[_route_key(work)].append(work)

        applied = 0
        for route_work in grouped.values():
            provider = self._providers_by_fingerprint[route_work[0].engine_fingerprint]
            provider_started = perf_counter()
            outcomes = await _translate_with_isolation(
                provider,
                route_work,
                max_attempts=self._max_attempts,
            )
            provider_ms = _elapsed_ms(provider_started)
            persist_started = perf_counter()
            route_applied = await persist_work_outcomes(session, route_work, outcomes)
            applied += route_applied
            succeeded = sum(outcome.succeeded for outcome in outcomes.values())
            retrying = sum(
                not outcome.succeeded and outcome.retryable for outcome in outcomes.values()
            )
            failed = len(outcomes) - succeeded - retrying
            queue_waits = [
                max((datetime.now(UTC) - item.created_at).total_seconds() * 1_000, 0)
                for item in route_work
            ]
            logger.info(
                "Translation route timings: lane=%s engine=%s provider=%s model=%s purpose=%s "
                "items=%d claim_ms=%d "
                "queue_wait_avg_ms=%d queue_wait_max_ms=%d provider_ms=%d persist_ms=%d "
                "succeeded=%d retrying=%d failed=%d",
                self.lane_name,
                route_work[0].engine_id,
                route_work[0].provider_name,
                route_work[0].model_name,
                route_work[0].purpose,
                route_applied,
                claim_ms,
                round(sum(queue_waits) / len(queue_waits)),
                round(max(queue_waits)),
                provider_ms,
                _elapsed_ms(persist_started),
                succeeded,
                retrying,
                failed,
            )
        return applied


@asynccontextmanager
async def _translation_lease_heartbeat(
    session_factory,
    work: Sequence[TranslationWork],
    *,
    lease_seconds: float,
):
    leases = [
        (item.id, token)
        for item in work
        if (token := getattr(item, "lease_token", None)) is not None
    ]
    if not leases or session_factory is None:
        yield
        return

    async def renew_forever() -> None:
        interval = max(min(lease_seconds / 3, 30.0), 0.01)
        while True:
            await asyncio.sleep(interval)
            try:
                async with session_factory() as heartbeat_session:
                    await renew_translation_work_leases(
                        heartbeat_session,
                        leases,
                        lease_seconds=lease_seconds,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Translation Work lease heartbeat failed; retrying.")

    task = asyncio.create_task(renew_forever())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _translate_with_isolation(
    provider: TranslationProvider,
    work: Sequence[TranslationWork],
    *,
    max_attempts: int = 8,
) -> dict[UUID, WorkOutcome]:
    if not work:
        return {}
    items = [
        TranslationItem(
            item_id=str(item.id),
            text=item.source_text,
            purpose=str(item.purpose),
            context_before=getattr(item, "context_before", None),
            context_after=getattr(item, "context_after", None),
        )
        for item in work
    ]
    string_outcomes = await translate_items_with_isolation(
        provider,
        items,
        source_locale=(
            None if work[0].source_locale == AUTO_SOURCE_LOCALE else work[0].source_locale
        ),
        target_locale=work[0].target_locale,
        attempt_counts={str(item.id): item.attempt_count for item in work},
        max_attempts=max_attempts,
    )
    return {item.id: string_outcomes[str(item.id)] for item in work}


async def translate_items_with_isolation(
    provider: TranslationProvider,
    items: Sequence[TranslationItem],
    *,
    source_locale: str | None,
    target_locale: str,
    attempt_counts: dict[str, int] | None = None,
    max_attempts: int = 8,
    actual_attempts: dict[str, int] | None = None,
) -> dict[str, WorkOutcome]:
    """Translate caller-owned items with the same isolation rules as Work."""
    if not items:
        return {}
    attempts = attempt_counts or {}
    if actual_attempts is not None:
        for item in items:
            actual_attempts[item.item_id] = actual_attempts.get(item.item_id, 0) + 1
        attempts = actual_attempts
    started = perf_counter()
    try:
        results = await provider.translate_batch(
            items,
            source_locale=source_locale,
            target_locale=target_locale,
        )
    except TranslationProviderError as exc:
        if (
            exc.isolate_items
            and len(items) > 1
            and not exc.retry_after_seconds
            and all(attempts.get(item.item_id, 1) < max_attempts for item in items)
        ):
            middle = len(items) // 2
            left = await translate_items_with_isolation(
                provider,
                items[:middle],
                source_locale=source_locale,
                target_locale=target_locale,
                attempt_counts=attempts,
                max_attempts=max_attempts,
                actual_attempts=actual_attempts,
            )
            right = await translate_items_with_isolation(
                provider,
                items[middle:],
                source_locale=source_locale,
                target_locale=target_locale,
                attempt_counts=attempts,
                max_attempts=max_attempts,
                actual_attempts=actual_attempts,
            )
            return {**left, **right}
        latency_ms = _elapsed_ms(started)
        logger.warning(
            "Translation route failed: provider=%s items=%d code=%s retryable=%s",
            provider.name,
            len(items),
            exc.code,
            exc.retryable,
        )
        outcomes: dict[str, WorkOutcome] = {}
        for item in items:
            attempt_count = attempts.get(item.item_id, 1)
            retry_delay = _retry_delay_seconds(attempt_count)
            if exc.retry_after_seconds is not None:
                retry_delay = max(retry_delay, exc.retry_after_seconds)
            outcomes[item.item_id] = WorkOutcome(
                error_code=exc.code,
                error_message=str(exc),
                retryable=(exc.retryable and attempt_count < max_attempts),
                retry_after_seconds=retry_delay,
                provider_latency_ms=latency_ms,
            )
        return outcomes
    except Exception as exc:  # provider SDK exceptions vary by Adapter
        latency_ms = _elapsed_ms(started)
        logger.exception(
            "Unexpected translation route failure: provider=%s items=%d",
            provider.name,
            len(items),
        )
        return {
            item.item_id: WorkOutcome(
                error_code=type(exc).__name__,
                error_message="Unexpected provider failure.",
                retryable=attempts.get(item.item_id, 1) < max_attempts,
                retry_after_seconds=_retry_delay_seconds(attempts.get(item.item_id, 1)),
                provider_latency_ms=latency_ms,
            )
            for item in items
        }

    latency_ms = _elapsed_ms(started)
    result_by_id = {result.item_id: result for result in results}
    outcomes: dict[str, WorkOutcome] = {}
    for item in items:
        result = result_by_id.get(item.item_id)
        translated = result.translated_text.strip() if result is not None else ""
        if not translated:
            attempt_count = attempts.get(item.item_id, 1)
            outcomes[item.item_id] = WorkOutcome(
                error_code="incomplete_response",
                error_message="Provider omitted a non-empty translation result.",
                retryable=attempt_count < max_attempts,
                retry_after_seconds=_retry_delay_seconds(attempt_count),
                provider_latency_ms=latency_ms,
            )
            continue
        token_error = (
            validate_tokens(item.text, translated)
            if uses_reader_format(item.purpose, item.text)
            else None
        )
        if token_error:
            logger.warning(
                "Translation token rejected: segment_id=%r reason=%s tokens=%s",
                item.item_id[:200],
                token_error,
                token_diagnostics(item.text, translated),
            )
            outcomes[item.item_id] = WorkOutcome(
                error_code="placeholder_mismatch",
                error_message=token_error,
                retryable=attempts.get(item.item_id, 1) < max_attempts,
                retry_after_seconds=2,
                provider_latency_ms=latency_ms,
            )
            continue
        outcomes[item.item_id] = WorkOutcome(
            translated_text=translated,
            provider_latency_ms=latency_ms,
        )
    return outcomes


def _route_key(work: TranslationWork) -> tuple[object, ...]:
    return (
        work.engine_id,
        work.provider_name,
        work.engine_fingerprint,
        work.source_locale,
        work.target_locale,
        work.purpose,
        work.scope,
        work.owner_id,
    )


def _retry_delay_seconds(attempt_count: int) -> float:
    delays = (1.0, 2.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0)
    return delays[min(max(attempt_count - 1, 0), len(delays) - 1)]


def _elapsed_ms(started: float) -> int:
    return max(round((perf_counter() - started) * 1000), 0)
