"""CLI entry point for independent PostgreSQL-backed worker loops."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from app.core.settings import Settings, get_settings
from app.domain.enums import TranslationPriority
from app.services.ranking_snapshots import sync_due_ranking_snapshots
from app.storage.database import build_engine, build_session_factory
from app.translation.domain import TranslationProvider
from app.translation.executor import TranslationExecutor
from app.translation.factory import (
    TranslationProviderConfigurationError,
    build_translation_provider_routes,
    close_translation_providers,
)
from app.translation.notifier import TranslationWorkSignal

from .candidates import process_candidates
from .cleanup import cancel_obsolete_model_work, purge_orphan_sources
from .health import mark_worker_loop_finished, mark_worker_loop_started, prune_worker_heartbeats
from .sync import sync_due_sources
from .web_rules import run_web_rule_author_once

logger = logging.getLogger(__name__)


class WorkerCycleResult(int):
    """Integer-compatible loop result carrying structured heartbeat metrics."""

    def __new__(cls, count: int, metrics: dict[str, object] | None = None):
        result = int.__new__(cls, count)
        result.metrics = metrics
        return result

    metrics: dict[str, object] | None


async def run_once(
    session_factory,
    *,
    translation_session_factory=None,
    translation_providers: dict[str, TranslationProvider] | None = None,
    translation_limit: int = 50,
    translation_lease_seconds: int = 120,
    translation_max_attempts: int = 8,
    web_rule_settings: Settings | None = None,
) -> int:
    """Run one bounded pass, creating translations only from real demand."""
    cleanup_result, source_result, ranking_result = await asyncio.gather(
        _run_cleanup(session_factory),
        sync_due_sources(session_factory),
        _run_rankings(session_factory),
    )
    candidate_result = await process_candidates(session_factory)
    translation_result = 0
    if translation_providers:
        executor = TranslationExecutor(
            translation_session_factory or session_factory,
            translation_providers,
            batch_size=translation_limit,
            lease_seconds=translation_lease_seconds,
            max_attempts=translation_max_attempts,
        )
        translation_result = await executor.run_once()
    rule_result = 0
    if web_rule_settings is not None:
        try:
            rule_result = await run_web_rule_author_once(
                session_factory, settings=web_rule_settings
            )
        except Exception:
            logger.exception("Rule authoring failed independently in the bounded worker cycle.")
    return (
        cleanup_result
        + source_result
        + ranking_result
        + candidate_result
        + translation_result
        + rule_result
    )


async def run_forever(poll_interval_seconds: int) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    worker_instance_id = uuid4()
    candidate_interval = min(poll_interval_seconds, 10)
    try:
        try:
            await prune_worker_heartbeats(session_factory)
        except Exception:
            logger.exception("Unable to prune stale worker heartbeat rows.")
        loops = [
            _poll_loop(
                "source_scanner",
                poll_interval_seconds,
                lambda: sync_due_sources(session_factory),
                health_session_factory=session_factory,
                worker_instance_id=worker_instance_id,
            ),
            _poll_loop(
                "candidate_processor",
                candidate_interval,
                lambda: process_candidates(
                    session_factory, limit=settings.ingestion_candidate_batch_size
                ),
                health_session_factory=session_factory,
                worker_instance_id=worker_instance_id,
            ),
            _poll_loop(
                "ranking_snapshot",
                poll_interval_seconds,
                lambda: _run_rankings(session_factory),
                health_session_factory=session_factory,
                worker_instance_id=worker_instance_id,
            ),
            _poll_loop(
                "orphan_cleanup",
                max(poll_interval_seconds, 3600),
                lambda: _run_cleanup(session_factory),
                health_session_factory=session_factory,
                worker_instance_id=worker_instance_id,
            ),
            _translation_service(
                settings,
                health_session_factory=session_factory,
                worker_instance_id=worker_instance_id,
            ),
            _poll_loop(
                "web_rule_author",
                settings.web_rule_agent_poll_seconds,
                lambda: run_web_rule_author_once(
                    session_factory,
                    settings=settings,
                    heartbeat_callback=lambda metrics: _safe_mark_loop_finished(
                        session_factory,
                        worker_instance_id=worker_instance_id,
                        loop_name="web_rule_author",
                        expected_interval_seconds=settings.web_rule_agent_poll_seconds,
                        error=None,
                        metrics=metrics,
                    ),
                ),
                health_session_factory=session_factory,
                worker_instance_id=worker_instance_id,
            ),
        ]
        await asyncio.gather(*loops)
    finally:
        await engine.dispose()


async def _translation_service(
    settings: Settings,
    *,
    health_session_factory=None,
    worker_instance_id: UUID | None = None,
) -> None:
    """Own and supervise translation-only configuration and resources."""
    while True:
        providers: dict[str, TranslationProvider] = {}
        translation_engine = None
        try:
            providers = build_translation_provider_routes(settings, require_default=True)
            translation_engine = build_engine(settings, autocommit=True, pool_size=3)
            translation_session_factory = build_session_factory(translation_engine)
            executors = (
                TranslationExecutor(
                    translation_session_factory,
                    providers,
                    batch_size=settings.translation_foreground_batch_size,
                    lane_name="foreground",
                    lease_seconds=settings.translation_lease_seconds,
                    max_attempts=settings.translation_max_attempts,
                    min_priority=int(TranslationPriority.PREFETCH),
                ),
                TranslationExecutor(
                    translation_session_factory,
                    providers,
                    batch_size=settings.translation_background_batch_size,
                    lane_name="background",
                    lease_seconds=settings.translation_lease_seconds,
                    max_attempts=settings.translation_max_attempts,
                    max_priority=int(TranslationPriority.PREFETCH) - 1,
                ),
            )
            async with asyncio.TaskGroup() as lanes:
                for executor in executors:
                    lanes.create_task(
                        _run_translation_lane(
                            executor,
                            settings,
                            health_session_factory=health_session_factory,
                            worker_instance_id=worker_instance_id,
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "Translation service failed independently; ingestion remains active and "
                "translation will retry."
            )
            for lane_name in ("foreground", "background"):
                await _safe_mark_loop_finished(
                    health_session_factory,
                    worker_instance_id=worker_instance_id,
                    loop_name=f"translation_{lane_name}",
                    expected_interval_seconds=settings.translation_idle_poll_seconds,
                    error=exc,
                )
        finally:
            await _close_translation_runtime(
                providers,
                translation_engine,
                timeout_seconds=settings.translation_cleanup_timeout_seconds,
            )
        await asyncio.sleep(settings.translation_idle_poll_seconds)


async def _close_translation_runtime(
    providers,
    translation_engine,
    *,
    timeout_seconds: float,
) -> None:
    """Best-effort cleanup that preserves cancellation but isolates every other failure."""
    cancellation: asyncio.CancelledError | None = None
    try:
        await close_translation_providers(providers, timeout_seconds=timeout_seconds)
    except asyncio.CancelledError as exc:
        cancellation = exc
    except BaseException:
        # Defensive boundary in case a future registry cleanup implementation
        # regresses and starts propagating ordinary resource failures.
        logger.exception("Translation Provider cleanup escaped its resource boundary.")

    if translation_engine is not None:
        try:
            async with asyncio.timeout(timeout_seconds):
                await translation_engine.dispose()
        except TimeoutError:
            logger.error("Translation database Engine cleanup timed out and was cancelled.")
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except BaseException:
            logger.exception("Translation database Engine cleanup failed and was isolated.")
    if cancellation is not None:
        raise cancellation


async def _translation_loop(
    executor: TranslationExecutor,
    settings: Settings,
    *,
    health_session_factory=None,
    worker_instance_id: UUID | None = None,
) -> None:
    """Drain work immediately; sleep on NOTIFY only after the queue is quiet."""
    logger.info(
        "Translation executor lane started: lane=%s batch_size=%d",
        executor.lane_name,
        executor.batch_size,
    )
    async with TranslationWorkSignal(settings) as signal:
        while True:
            loop_name = f"translation_{executor.lane_name}"
            await _safe_mark_loop_started(
                health_session_factory,
                worker_instance_id=worker_instance_id,
                loop_name=loop_name,
                expected_interval_seconds=settings.translation_idle_poll_seconds,
            )
            try:
                async with executor.open_session() as session:
                    while True:
                        handled = 0
                        while True:
                            count = await executor.run_once_with_session(session)
                            handled += count
                            await _safe_mark_loop_finished(
                                health_session_factory,
                                worker_instance_id=worker_instance_id,
                                loop_name=loop_name,
                                expected_interval_seconds=settings.translation_idle_poll_seconds,
                                error=None,
                            )
                            if count < executor.batch_size:
                                break
                        if handled:
                            logger.info(
                                "Translation executor drained: lane=%s items=%d",
                                executor.lane_name,
                                handled,
                            )
                        await signal.wait(
                            settings.translation_idle_poll_seconds,
                            fallback_timeout_seconds=(
                                settings.translation_listener_fallback_poll_seconds
                            ),
                        )
            except Exception as exc:
                logger.exception(
                    "Translation executor cycle failed: lane=%s; waiting before retry.",
                    executor.lane_name,
                )
                await _safe_mark_loop_finished(
                    health_session_factory,
                    worker_instance_id=worker_instance_id,
                    loop_name=loop_name,
                    expected_interval_seconds=settings.translation_idle_poll_seconds,
                    error=exc,
                )
                await signal.wait(
                    settings.translation_idle_poll_seconds,
                    fallback_timeout_seconds=settings.translation_listener_fallback_poll_seconds,
                )


async def _run_translation_lane(
    executor: TranslationExecutor,
    settings: Settings,
    *,
    health_session_factory=None,
    worker_instance_id: UUID | None = None,
) -> None:
    """Make every Lane exit fail its generation so TaskGroup cancels all siblings."""
    await _translation_loop(
        executor,
        settings,
        health_session_factory=health_session_factory,
        worker_instance_id=worker_instance_id,
    )
    raise RuntimeError(f"Translation Lane exited unexpectedly: lane={executor.lane_name}")


async def _poll_loop(
    name: str,
    interval_seconds: int,
    operation: Callable[[], Awaitable[int]],
    *,
    health_session_factory=None,
    worker_instance_id: UUID | None = None,
) -> None:
    while True:
        await _safe_mark_loop_started(
            health_session_factory,
            worker_instance_id=worker_instance_id,
            loop_name=name,
            expected_interval_seconds=interval_seconds,
        )
        try:
            result = await operation()
            count = int(result)
            metrics = getattr(result, "metrics", None)
            if count:
                logger.info("%s handled %d item(s).", name, count)
            await _safe_mark_loop_finished(
                health_session_factory,
                worker_instance_id=worker_instance_id,
                loop_name=name,
                expected_interval_seconds=interval_seconds,
                error=None,
                metrics=metrics,
            )
        except Exception as exc:
            logger.exception("%s cycle failed; retrying on its next interval.", name)
            await _safe_mark_loop_finished(
                health_session_factory,
                worker_instance_id=worker_instance_id,
                loop_name=name,
                expected_interval_seconds=interval_seconds,
                error=exc,
                metrics=getattr(exc, "metrics", None),
            )
        await asyncio.sleep(interval_seconds)


async def _safe_mark_loop_started(
    session_factory,
    *,
    worker_instance_id: UUID | None,
    loop_name: str,
    expected_interval_seconds: int,
) -> None:
    if session_factory is None or worker_instance_id is None:
        return
    try:
        await mark_worker_loop_started(
            session_factory,
            worker_instance_id=worker_instance_id,
            loop_name=loop_name,
            expected_interval_seconds=expected_interval_seconds,
        )
    except Exception:
        logger.exception("Worker heartbeat start write failed: loop=%s", loop_name)


async def _safe_mark_loop_finished(
    session_factory,
    *,
    worker_instance_id: UUID | None,
    loop_name: str,
    expected_interval_seconds: int,
    error: Exception | None,
    metrics: dict[str, object] | None = None,
) -> None:
    if session_factory is None or worker_instance_id is None:
        return
    try:
        await mark_worker_loop_finished(
            session_factory,
            worker_instance_id=worker_instance_id,
            loop_name=loop_name,
            expected_interval_seconds=expected_interval_seconds,
            error=error,
            metrics=metrics,
        )
    except Exception:
        logger.exception("Worker heartbeat finish write failed: loop=%s", loop_name)


async def _run_cleanup(session_factory) -> int:
    async with session_factory() as session:
        cancelled = await cancel_obsolete_model_work(session, get_settings())
        return cancelled + await purge_orphan_sources(session)


async def _run_rankings(session_factory) -> WorkerCycleResult:
    metrics: dict[str, object] = {}
    async with session_factory() as session:
        count = await sync_due_ranking_snapshots(
            session,
            session_factory=session_factory,
            metrics_out=metrics,
        )
    return WorkerCycleResult(count, metrics)


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize due Reader sources.")
    parser.add_argument("--once", action="store_true", help="Run one bounded cycle and exit.")
    parser.add_argument("--poll-seconds", type=int, help="Override the polling interval.")
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    poll_interval = args.poll_seconds or settings.worker_poll_interval_seconds
    if poll_interval < 10:
        parser.error("--poll-seconds must be at least 10.")

    if args.once:
        engine = build_engine(settings)
        session_factory = build_session_factory(engine)
        try:
            providers = build_translation_provider_routes(settings, require_default=True)
        except TranslationProviderConfigurationError:
            providers = {}
            logger.exception(
                "Translation configuration is unavailable; bounded ingestion will continue."
            )
        translation_engine = (
            build_engine(settings, autocommit=True, pool_size=3) if providers else None
        )
        translation_session_factory = (
            build_session_factory(translation_engine) if translation_engine is not None else None
        )

        async def run_and_dispose() -> int:
            try:
                return await run_once(
                    session_factory,
                    translation_session_factory=translation_session_factory,
                    translation_providers=providers,
                    translation_limit=settings.translation_batch_size,
                    translation_lease_seconds=settings.translation_lease_seconds,
                    translation_max_attempts=settings.translation_max_attempts,
                    web_rule_settings=settings,
                )
            finally:
                await _close_translation_runtime(
                    providers,
                    translation_engine,
                    timeout_seconds=settings.translation_cleanup_timeout_seconds,
                )
                await engine.dispose()

        count = asyncio.run(run_and_dispose())
        logger.info("Worker handled %d item(s).", count)
        return

    logger.info("Worker started; base polling interval is %d second(s).", poll_interval)
    try:
        asyncio.run(run_forever(poll_interval))
    except KeyboardInterrupt:
        logger.info("Worker stopped.")
