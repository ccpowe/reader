"""reader-worker: the background loops.

    source_scan        every APP_WORKER_POLL_INTERVAL_SECONDS: scan due sources
    title_translation  every APP_TITLE_TRANSLATION_INTERVAL_SECONDS: translate queued titles
    cleanup            daily: prune articles, scan history, unused translations
                       and ended sessions

Each loop records a heartbeat after every run; /worker-ready reads them.
`reader-worker --once` runs every job one time and exits.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reader.accounts.service import delete_ended_sessions
from reader.core.db import build_engine, build_session_factory
from reader.core.settings import Settings, get_settings
from reader.ingestion.retention import prune_articles, prune_scan_runs
from reader.ingestion.scanner import scan_due_sources
from reader.system.heartbeats import record_loop_alive, record_loop_result, record_loop_start
from reader.translation.service import Translator
from reader.translation.titles import prune_translations, translate_pending_titles

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
# While a job runs, its heartbeat is refreshed this often, so a long but healthy run
# never looks stuck; a dead process stops refreshing.
ALIVE_INTERVAL_SECONDS = 60

Job = Callable[[], Awaitable[object]]


async def cleanup(factory: async_sessionmaker[AsyncSession], settings: Settings) -> dict[str, int]:
    async with factory() as db:
        return {
            "articles": await prune_articles(db, settings.articles_kept_per_source),
            "scan_runs": await prune_scan_runs(db, settings.scan_runs_kept_per_source),
            "translations": await prune_translations(db, settings.translation_unused_keep_days),
            "sessions": await delete_ended_sessions(db),
        }


def jobs(
    factory: async_sessionmaker[AsyncSession], settings: Settings, translator: Translator
) -> dict[str, tuple[int, Job]]:
    """Loop name -> (interval in seconds, job)."""
    return {
        "source_scan": (
            settings.worker_poll_interval_seconds,
            lambda: scan_due_sources(factory, settings),
        ),
        "title_translation": (
            settings.title_translation_interval_seconds,
            lambda: translate_pending_titles(translator, settings.title_translation_batch),
        ),
        "cleanup": (CLEANUP_INTERVAL_SECONDS, lambda: cleanup(factory, settings)),
    }


async def run_once(
    factory: async_sessionmaker[AsyncSession], name: str, interval: int, job: Job
) -> bool:
    """Run one job and record its heartbeat; return whether it succeeded."""
    async with factory() as db:
        await record_loop_start(db, name, interval)

    async def keep_alive() -> None:
        while True:
            await asyncio.sleep(ALIVE_INTERVAL_SECONDS)
            try:
                async with factory() as db:
                    await record_loop_alive(db, name, interval)
            except Exception:
                logger.warning("Could not refresh heartbeat: %s", name)

    alive = asyncio.create_task(keep_alive())
    error_code = None
    try:
        await job()
    except Exception as exc:
        logger.exception("Worker job failed: %s", name)
        error_code = type(exc).__name__
    finally:
        alive.cancel()
    async with factory() as db:
        await record_loop_result(db, name, interval, error_code=error_code)
    return error_code is None


async def _loop(
    factory: async_sessionmaker[AsyncSession], name: str, interval: int, job: Job
) -> None:
    while True:
        try:
            await run_once(factory, name, interval, job)
        except Exception:
            # Even the heartbeat failed (database down): keep trying.
            logger.exception("Worker loop iteration failed: %s", name)
        await asyncio.sleep(interval)


async def _main(once: bool) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    translator = Translator(factory, settings)
    try:
        loops = jobs(factory, settings, translator)
        if once:
            for name, (interval, job) in loops.items():
                await run_once(factory, name, interval, job)
        else:
            await asyncio.gather(
                *(_loop(factory, name, interval, job) for name, (interval, job) in loops.items())
            )
    finally:
        await translator.aclose()
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reader-worker")
    parser.add_argument("--once", action="store_true", help="run every job once and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=get_settings().log_level)
    try:
        asyncio.run(_main(args.once))
    except KeyboardInterrupt:
        logger.info("Worker stopped.")


if __name__ == "__main__":
    main()
