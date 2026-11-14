"""Boards: the latest snapshot of each board, fetched when someone looks after it expired.

There is no background refresh and no history. Opening a board:
  - fresh snapshot              → returned as is;
  - expired                     → fetched now, waiting up to `board_fetch_wait_seconds`;
                                  on timeout or failure the old snapshot is returned
                                  (marked stale); a slow fetch keeps going and is saved;
  - manual refresh              → ignores expiry, at most once a minute per board;
  - upstream rate limit (429)   → `paused_until` is set and only stored data is served.
One fetch per board runs at a time in this process.

Product Hunt "yesterday" is a finished Los Angeles day: once fetched after that day
ended it never expires. Its key holds the date, so "today" rolls over by itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reader.boards import fetchers
from reader.boards.fetchers import BoardItem
from reader.core.errors import AppError
from reader.core.settings import Settings
from reader.ingestion.errors import ScanError
from reader.models import BoardSnapshot

logger = logging.getLogger(__name__)

NEVER = datetime(1970, 1, 1, tzinfo=UTC)  # fetched_at of a board never fetched yet
RATE_LIMIT_PAUSE = timedelta(minutes=10)
# A bot check does not clear by itself soon; do not knock on every open.
CHALLENGE_PAUSE = timedelta(hours=1)

BOARDS = ("hn", "github", "techtwitter", "product_hunt", "reddit")
VARIANTS = {
    "hn": ("top",),
    "techtwitter": ("trending",),
    "github": ("daily", "weekly", "monthly"),
    "product_hunt": ("today", "yesterday"),
    "reddit": tuple(fetchers.REDDIT_VARIANTS),
}
_TTL = {
    "hn": timedelta(minutes=5),
    "techtwitter": timedelta(minutes=15),
    "github": timedelta(minutes=30),
    "product_hunt": timedelta(minutes=30),
}
_REDDIT_TTL = {"hot": timedelta(minutes=30), "rising": timedelta(hours=1)}
_REDDIT_TOP_TTL = timedelta(hours=6)


@dataclass(frozen=True)
class Target:
    """One board variant, resolved at a moment in time."""

    board: str
    variant: str
    subreddit: str | None
    key: str
    params: dict
    ttl: timedelta
    # After this moment the content can no longer change (Product Hunt's past days).
    final_after: datetime | None
    fetch: Callable[[], Awaitable[list[BoardItem]]]

    def is_fresh(self, fetched_at: datetime, now: datetime) -> bool:
        if self.final_after is not None and fetched_at >= self.final_after:
            return True
        return now - fetched_at < self.ttl


def resolve(
    settings: Settings,
    board: str,
    variant: str | None,
    subreddit: str | None,
    now: datetime | None = None,
) -> Target:
    """Validate the request and say what to fetch, or raise 422."""
    now = now or datetime.now(UTC)
    if board not in BOARDS:
        raise AppError(404, "board_not_found", "No such board.")
    variant = variant or VARIANTS[board][0]
    if variant not in VARIANTS[board]:
        raise AppError(422, "invalid_variant", f"{board} variants: {', '.join(VARIANTS[board])}.")
    limit = settings.board_items
    if board == "reddit":
        if not subreddit:
            raise AppError(422, "invalid_subreddit", "Reddit boards need a subreddit.")
        try:
            name = fetchers.normalize_subreddit(subreddit)
        except ValueError:
            raise AppError(422, "invalid_subreddit", "Invalid subreddit name.") from None
        return Target(
            board,
            variant,
            name,
            f"reddit:{name}:{variant}",
            {"subreddit": name},
            _REDDIT_TTL.get(variant, _REDDIT_TOP_TTL),
            None,
            lambda: fetchers.fetch_reddit(name, variant, limit),
        )
    if subreddit:
        raise AppError(422, "invalid_subreddit", "Only Reddit boards take a subreddit.")
    if board == "product_hunt":
        day = fetchers.product_hunt_day(now, variant)
        token = settings.product_hunt_token
        secret = token.get_secret_value() if token else None
        _, day_end = fetchers.product_hunt_bounds(day)
        return Target(
            board,
            variant,
            None,
            f"product_hunt:{day.isoformat()}",
            {"day": day.isoformat()},
            _TTL[board],
            day_end,
            lambda: fetchers.fetch_product_hunt(day, limit, secret),
        )
    fetch = {
        "hn": lambda: fetchers.fetch_hacker_news(limit),
        "techtwitter": lambda: fetchers.fetch_techtwitter(limit),
        "github": lambda: fetchers.fetch_github_trending(variant, limit),
    }[board]
    key = board if len(VARIANTS[board]) == 1 else f"{board}:{variant}"
    return Target(board, variant, None, key, {}, _TTL[board], None, fetch)


@dataclass(frozen=True)
class BoardView:
    items: list[BoardItem]
    fetched_at: datetime | None
    stale: bool
    # Why the board is stale, when a fetch just failed or is paused.
    error_code: str | None
    paused_until: datetime | None


def _view(snapshot: BoardSnapshot | None, target: Target, now: datetime) -> BoardView:
    if snapshot is None:
        return BoardView([], None, True, None, None)
    has_data = snapshot.fetched_at > NEVER
    return BoardView(
        items=[BoardItem.model_validate(item) for item in snapshot.items],
        fetched_at=snapshot.fetched_at if has_data else None,
        stale=not has_data or not target.is_fresh(snapshot.fetched_at, now),
        error_code=snapshot.last_error,
        paused_until=snapshot.paused_until
        if snapshot.paused_until and snapshot.paused_until > now
        else None,
    )


class Boards:
    """Per-process board access; owns in-flight fetches."""

    def __init__(self, factory: async_sessionmaker[AsyncSession], settings: Settings):
        self.factory = factory
        self.settings = settings
        self._fetches: dict[str, asyncio.Task] = {}

    async def aclose(self) -> None:
        for task in list(self._fetches.values()):
            task.cancel()
        await asyncio.gather(*self._fetches.values(), return_exceptions=True)

    async def _load(self, key: str) -> BoardSnapshot | None:
        async with self.factory() as db:
            return await db.get(BoardSnapshot, key)

    async def get(self, target: Target, *, refresh: bool = False) -> BoardView:
        now = datetime.now(UTC)
        snapshot = await self._load(target.key)
        view = _view(snapshot, target, now)
        if view.paused_until is not None:
            return self._usable(view)
        if refresh:
            if not await self._take_refresh_slot(target, now):
                return self._usable(view)
        elif snapshot is not None and not view.stale:
            return view
        task = self._fetches.get(target.key)
        if task is None:
            task = asyncio.create_task(self._fetch_and_store(target))
            self._fetches[target.key] = task
            task.add_done_callback(lambda _t, key=target.key: self._fetches.pop(key, None))
        try:
            await asyncio.wait_for(asyncio.shield(task), self.settings.board_fetch_wait_seconds)
        except TimeoutError:
            # Still fetching: what is shown is older than what was asked for.
            return self._usable(replace(view, stale=True), pending=True)
        except ScanError:
            # Recorded on the snapshot; the stored copy is what is left.
            latest = _view(await self._load(target.key), target, datetime.now(UTC))
            return self._usable(replace(latest, stale=True))
        return self._usable(_view(await self._load(target.key), target, datetime.now(UTC)))

    def _usable(self, view: BoardView, *, pending: bool = False) -> BoardView:
        """Stored data if there is any; otherwise say why there is none."""
        if view.fetched_at is not None:
            return view
        if pending:
            raise AppError(
                503,
                "board_loading",
                "The board is still loading; try again shortly.",
                retry_after=5,
            )
        retry_after = None
        if view.paused_until is not None:
            retry_after = max(int((view.paused_until - datetime.now(UTC)).total_seconds()), 1)
        raise AppError(
            502,
            "board_unavailable",
            f"The board could not be fetched ({view.error_code or 'unknown'}).",
            retry_after=retry_after,
        )

    async def _take_refresh_slot(self, target: Target, now: datetime) -> bool:
        """Claim this minute's manual refresh; False if one happened recently."""
        cooldown = timedelta(seconds=self.settings.board_manual_refresh_cooldown_seconds)
        async with self.factory() as db:
            statement = pg_insert(BoardSnapshot).values(
                key=target.key,
                board=target.board,
                params=target.params,
                items=[],
                fetched_at=NEVER,
                manual_refresh_at=now,
            )
            statement = statement.on_conflict_do_update(
                index_elements=[BoardSnapshot.key],
                set_={"manual_refresh_at": now},
                where=or_(
                    BoardSnapshot.manual_refresh_at.is_(None),
                    BoardSnapshot.manual_refresh_at < now - cooldown,
                ),
            ).returning(BoardSnapshot.key)
            taken = await db.scalar(statement)
            await db.commit()
        return taken is not None

    async def _fetch_and_store(self, target: Target) -> None:
        # The data reflects the board when the fetch began; a fetch that started
        # before a Product Hunt day ended must not count as that day's final copy.
        started = datetime.now(UTC)
        try:
            items = await target.fetch()
        except ScanError as exc:
            logger.info("Board fetch failed: board=%s code=%s", target.key, exc.code)
            paused = None
            if exc.code.endswith("_http_429"):
                wait = exc.retry_after_seconds
                paused = datetime.now(UTC) + (timedelta(seconds=wait) if wait else RATE_LIMIT_PAUSE)
            elif exc.code.endswith("_challenge_required"):
                paused = datetime.now(UTC) + CHALLENGE_PAUSE
            await self._record(target, last_error=exc.code[:120], paused_until=paused)
            raise
        await self._record(
            target,
            items=[item.model_dump(mode="json") for item in items],
            fetched_at=started,
            last_error=None,
            paused_until=None,
        )

    async def _record(self, target: Target, **values) -> None:
        async with self.factory() as db:
            statement = pg_insert(BoardSnapshot).values(
                key=target.key,
                board=target.board,
                params=target.params,
                items=values.get("items", []),
                fetched_at=values.get("fetched_at", NEVER),
                last_error=values.get("last_error"),
                paused_until=values.get("paused_until"),
            )
            await db.execute(
                statement.on_conflict_do_update(index_elements=[BoardSnapshot.key], set_=values)
            )
            await db.commit()


async def prune_snapshots(db: AsyncSession, keep_days: int) -> int:
    """Snapshots nobody has fetched for `keep_days` (nobody opened them)."""
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    removed = (
        await db.execute(
            BoardSnapshot.__table__.delete().where(
                BoardSnapshot.fetched_at < cutoff,
                or_(
                    BoardSnapshot.manual_refresh_at.is_(None),
                    BoardSnapshot.manual_refresh_at < cutoff,
                ),
            )
        )
    ).rowcount
    await db.commit()
    return removed
