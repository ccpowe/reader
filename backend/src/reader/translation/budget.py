"""Server-wide limits on characters sent to translation models, per minute and per day.

Characters are reserved before a model call and never given back: a failed call
still cost the request. Only text that missed the cache is ever counted.
A limit of 0 turns that limit off.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reader.core.settings import Settings
from reader.models import TranslationBudget


def _windows(now: datetime) -> dict[str, tuple[datetime, datetime]]:
    """period -> (window start, next window start)."""
    minute = now.replace(second=0, microsecond=0)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "minute": (minute, minute + timedelta(minutes=1)),
        "day": (day, day + timedelta(days=1)),
    }


def _limits(settings: Settings) -> dict[str, int]:
    return {
        "minute": settings.translation_chars_per_minute,
        "day": settings.translation_chars_per_day,
    }


class QuotaExceeded(Exception):
    def __init__(self, retry_after: float):
        super().__init__("The translation character limit is reached.")
        self.retry_after = retry_after


async def _reserve_in(db: AsyncSession, period: str, start: datetime, chars: int, limit: int):
    """Add `chars` to the window unless that would pass the limit; return whether it did."""
    statement = pg_insert(TranslationBudget).values(
        period=period, window_start=start, chars_used=chars
    )
    statement = statement.on_conflict_do_update(
        index_elements=[TranslationBudget.period, TranslationBudget.window_start],
        set_={"chars_used": TranslationBudget.chars_used + statement.excluded.chars_used},
        where=TranslationBudget.chars_used + statement.excluded.chars_used <= limit,
    ).returning(TranslationBudget.chars_used)
    used = await db.scalar(statement)
    # A first insert bypasses the WHERE; a single request larger than the limit fails here.
    return used is not None and used <= limit


async def reserve(
    factory: async_sessionmaker[AsyncSession], settings: Settings, chars: int
) -> None:
    """Reserve `chars` in every active limit, or reserve nothing and raise QuotaExceeded."""
    now = datetime.now(UTC)
    windows = _windows(now)
    async with factory() as db:
        for period, limit in _limits(settings).items():
            if limit <= 0:
                continue
            start, end = windows[period]
            if not await _reserve_in(db, period, start, chars, limit):
                await db.rollback()
                raise QuotaExceeded(max((end - now).total_seconds(), 1.0))
        await db.commit()


async def usage(factory: async_sessionmaker[AsyncSession], settings: Settings) -> dict:
    """Characters used and allowed in the current windows (for status output)."""
    windows = _windows(datetime.now(UTC))
    async with factory() as db:
        used = {
            period: (
                await db.scalar(
                    select(TranslationBudget.chars_used).where(
                        TranslationBudget.period == period,
                        TranslationBudget.window_start == windows[period][0],
                    )
                )
                or 0
            )
            for period in windows
        }
    return {
        period: {"used": used[period], "limit": limit}
        for period, limit in _limits(settings).items()
    }
