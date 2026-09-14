"""Compatibility edge for the demand-driven Translation Executor."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.translation.domain import TranslationProvider
from app.translation.executor import TranslationExecutor

DEFAULT_TRANSLATION_BATCH_SIZE = 50


async def process_translation_work(
    session_factory: async_sessionmaker[AsyncSession],
    providers: dict[str, TranslationProvider],
    *,
    limit: int = DEFAULT_TRANSLATION_BATCH_SIZE,
    lease_seconds: int = 120,
    max_attempts: int = 8,
) -> int:
    """Run one bounded execution pass without leaking Store details."""
    return await TranslationExecutor(
        session_factory,
        providers,
        batch_size=limit,
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
    ).run_once()
