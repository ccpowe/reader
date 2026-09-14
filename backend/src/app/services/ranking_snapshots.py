"""Shared, stale-while-revalidate snapshots for external ranking providers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from math import isfinite
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, exists, func, not_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.core.settings import get_settings
from app.domain.enums import ContentKind, MediaType, SourceStatus, SyncPhase
from app.ingestion.candidate_queue import upsert_candidates
from app.ingestion.models import DiscoveredContent, DiscoveredMedia
from app.services.rankings import (
    RankingItem,
    fetch_github_trending,
    fetch_hacker_news,
    fetch_reddit_ranking,
)
from app.storage.models import (
    FeedSource,
    RankingProviderBudget,
    RankingSnapshot,
    SourceSubscription,
    SourceSyncState,
)

logger = logging.getLogger(__name__)

_RECONCILE_REDDIT_SNAPSHOTS_SQL = """
/* reddit_snapshot_reconciliation */
WITH subscribed_reddit AS (
    SELECT DISTINCT lower(split_part(source.canonical_key, ':', 2)) AS subreddit
    FROM feed_sources AS source
    JOIN source_subscriptions AS subscription ON subscription.source_id = source.id
    WHERE source.kind = 'reddit'
      AND source.canonical_key LIKE 'reddit:%'
      AND subscription.is_enabled
)
INSERT INTO ranking_snapshots (
    cache_key, kind, parameters, payload, fetched_at, next_refresh_at,
    last_accessed_at, is_ready, refresh_token, refresh_expires_at,
    manual_refresh_after, last_error
)
SELECT
    'ranking:v2:reddit:' || subscribed.subreddit || ':' || board.sort || ':' ||
        board.cache_window,
    'reddit',
    jsonb_build_object(
        'subreddit', subscribed.subreddit,
        'sort', board.sort,
        'time_filter', board.time_filter
    ),
    jsonb_build_object(
        'title', 'r/' || subscribed.subreddit,
        'subtitle', board.subtitle,
        'items', '[]'::jsonb
    ),
    now(), now(), now(), false, NULL, NULL, NULL, NULL
FROM subscribed_reddit AS subscribed
CROSS JOIN (VALUES
    ('hot', 'week', '-', 'Hot'),
    ('rising', 'week', '-', 'Rising'),
    ('top', 'week', 'week', 'Top')
) AS board(sort, time_filter, cache_window, subtitle)
ON CONFLICT (cache_key) DO NOTHING
"""

SNAPSHOT_ITEM_LIMIT = 100
SNAPSHOT_SCHEMA_VERSION = "v2"

RankingKind = Literal["hacker_news", "reddit", "github"]
RedditSort = Literal["hot", "rising", "top"]
RedditTimeFilter = Literal["day", "week", "month", "year"]


@dataclass(frozen=True)
class RankingRequest:
    kind: RankingKind
    subreddit: str = "MachineLearning"
    sort: RedditSort = "hot"
    time_filter: RedditTimeFilter = "week"

    @property
    def cache_key(self) -> str:
        if self.kind != "reddit":
            return f"ranking:{SNAPSHOT_SCHEMA_VERSION}:{self.kind}"
        window = self.time_filter if self.sort == "top" else "-"
        return (
            f"ranking:{SNAPSHOT_SCHEMA_VERSION}:reddit:"
            f"{self.subreddit.lower()}:{self.sort}:{window}"
        )

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "subreddit": self.subreddit,
            "sort": self.sort,
            "time_filter": self.time_filter,
        }


@dataclass(frozen=True)
class RankingData:
    kind: RankingKind
    title: str
    subtitle: str
    items: list[RankingItem]
    fetched_at: datetime


class RankingRefreshRateLimited(RuntimeError):
    """A caller attempted a manual refresh during the shared cooldown."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Ranking snapshot manual refresh is rate limited.")
        self.retry_after_seconds = max(1, retry_after_seconds)


class RankingRefreshInProgress(RuntimeError):
    """The first snapshot is already being fetched by another process."""

    def __init__(self, message: str, retry_after_seconds: int = 2) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, retry_after_seconds)


class RankingCycleError(RuntimeError):
    """The worker cycle made no progress (all refreshes failed or timed out)."""

    def __init__(self, message: str, metrics: dict[str, object]) -> None:
        super().__init__(message)
        self.metrics = metrics


def refresh_interval(request: RankingRequest) -> timedelta:
    """Choose a conservative provider-specific refresh interval."""
    if request.kind == "hacker_news":
        return timedelta(minutes=5)
    if request.kind == "github":
        return timedelta(minutes=30)
    if request.sort == "hot":
        return timedelta(minutes=30)
    if request.sort == "rising":
        return timedelta(hours=1)
    return timedelta(hours=6)


async def get_snapshot(session: AsyncSession, request: RankingRequest) -> RankingData | None:
    snapshot = await session.get(RankingSnapshot, request.cache_key)
    if snapshot is None:
        return None
    snapshot.last_accessed_at = datetime.now(UTC)
    await session.commit()
    return _data_from_snapshot(snapshot) if snapshot.is_ready else None


async def create_or_refresh_snapshot(
    session: AsyncSession, request: RankingRequest, *, force: bool = False
) -> RankingData:
    """Refresh through a durable lease without holding a DB connection over I/O."""
    now = datetime.now(UTC)
    await _ensure_snapshot_row(session, request, now)
    token, reusable = await _claim_snapshot_refresh(session, request, force=force, now=now)
    if reusable is not None:
        return reusable
    assert token is not None

    try:
        lease_seconds = get_settings().ranking_refresh_lease_seconds
        async with asyncio.timeout(_provider_io_timeout_seconds(lease_seconds)):
            data = await fetch_live_ranking(request, limit=_snapshot_item_limit(request))
    except asyncio.CancelledError:
        # Cycle-level cancellation must release the durable lease before the
        # task disappears, otherwise the next worker waits the full lease.
        await _clear_refresh_lease_on_cancel(session, request, token)
        raise
    except Exception as exc:
        await _record_refresh_failure(session, request, token, exc)
        raise
    return await _publish_snapshot(session, request, token, data)


async def sync_due_ranking_snapshots(
    session: AsyncSession,
    *,
    limit: int = 20,
    session_factory=None,
    concurrency: int | None = None,
    cycle_timeout_seconds: float | None = None,
    metrics_out: dict[str, object] | None = None,
) -> int:
    """Refresh stale snapshots in the dedicated worker, never on page navigation."""
    now = datetime.now(UTC)
    settings = get_settings()
    inactive_before = now - timedelta(days=settings.ranking_snapshot_inactive_ttl_days)
    from app.translation.lifecycle import lock_shared_lifecycle

    await lock_shared_lifecycle(session)
    await reconcile_reddit_snapshots(session)
    active_reddit_subscription = _active_reddit_subscription_predicate()
    core_reddit_snapshot = _core_reddit_snapshot_predicate()
    await session.execute(
        delete(RankingSnapshot).where(
            or_(
                _legacy_ranking_snapshot_predicate(),
                and_(
                    RankingSnapshot.kind == "reddit",
                    RankingSnapshot.last_accessed_at < inactive_before,
                    or_(
                        not_(active_reddit_subscription),
                        not_(core_reddit_snapshot),
                    ),
                ),
            )
        )
    )
    await session.commit()
    active_reddit_subscription = _active_reddit_subscription_predicate()
    core_reddit_snapshot = _core_reddit_snapshot_predicate()
    snapshots = list(
        await session.scalars(
            select(RankingSnapshot)
            .options(
                load_only(
                    RankingSnapshot.cache_key,
                    RankingSnapshot.kind,
                    RankingSnapshot.parameters,
                )
            )
            .where(
                RankingSnapshot.cache_key.like(f"ranking:{SNAPSHOT_SCHEMA_VERSION}:%"),
                RankingSnapshot.next_refresh_at <= now,
                or_(
                    RankingSnapshot.kind != "reddit",
                    and_(
                        active_reddit_subscription,
                        or_(
                            core_reddit_snapshot,
                            RankingSnapshot.last_accessed_at >= inactive_before,
                        ),
                    ),
                ),
            )
            .order_by(RankingSnapshot.next_refresh_at)
            .limit(limit)
        )
    )
    # The list is now detached from a live transaction before any provider I/O.
    await session.commit()
    metrics: dict[str, object] = {
        "status": "success",
        "requested": len(snapshots),
        "attempted": 0,
        "retired": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "timed_out": 0,
        "started_at": now.isoformat(),
        "results": [],
    }
    valid_snapshots: list[tuple[object, RankingRequest]] = []
    for snapshot in snapshots:
        try:
            request = _request_from_snapshot(snapshot)
        except (TypeError, ValueError, AttributeError) as exc:
            await _retire_invalid_snapshot(session, snapshot, reason=str(exc))
            metrics["retired"] = int(metrics["retired"]) + 1
            _record_cycle_result(metrics, snapshot.cache_key, "skipped", exc)
            continue
        if request.cache_key != snapshot.cache_key:
            await _retire_invalid_snapshot(
                session,
                snapshot,
                reason=f"canonical cache key is {request.cache_key}",
            )
            _record_cycle_result(
                metrics,
                snapshot.cache_key,
                "skipped",
                ValueError("snapshot cache key mismatch"),
            )
            metrics["retired"] = int(metrics["retired"]) + 1
            continue
        valid_snapshots.append((snapshot, request))
    metrics["attempted"] = len(valid_snapshots)

    # A production worker gives each provider call its own session. The
    # original session remains available for the scheduler and is never held
    # across external I/O. Direct callers without a factory retain the
    # deterministic sequential behavior used by lightweight unit tests.
    if session_factory is None:
        for snapshot, request in valid_snapshots:
            result = await _refresh_one_snapshot(session, snapshot.cache_key, request)
            _merge_cycle_result(metrics, result)
    else:
        worker_concurrency = max(
            1,
            concurrency if concurrency is not None else settings.ranking_worker_concurrency,
        )
        timeout_seconds = max(
            0.001,
            cycle_timeout_seconds
            if cycle_timeout_seconds is not None
            else settings.ranking_worker_cycle_timeout_seconds,
        )
        semaphore = asyncio.Semaphore(worker_concurrency)

        async def run_one(snapshot, request):
            async with semaphore:
                async with session_factory() as independent_session:
                    return await _refresh_one_snapshot(
                        independent_session,
                        snapshot.cache_key,
                        request,
                    )

        tasks = {
            asyncio.create_task(run_one(snapshot, request)): snapshot.cache_key
            for snapshot, request in valid_snapshots
        }
        if not tasks:
            metrics["finished_at"] = datetime.now(UTC).isoformat()
            if metrics_out is not None:
                metrics_out.update(metrics)
            return len(snapshots)
        done, pending = await asyncio.wait(list(tasks), timeout=timeout_seconds)
        for task in done:
            if task.cancelled():
                _record_cycle_result(metrics, tasks[task], "timed_out")
                continue
            _merge_cycle_result(metrics, task.result())
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in pending:
                _record_cycle_result(metrics, tasks[task], "timed_out")

    if int(metrics["failed"]) or int(metrics["timed_out"]):
        if int(metrics["succeeded"]) == 0 and int(metrics["attempted"]):
            metrics["status"] = "timeout" if int(metrics["timed_out"]) else "failed"
            metrics["finished_at"] = datetime.now(UTC).isoformat()
            if metrics_out is not None:
                metrics_out.update(metrics)
            raise RankingCycleError("Ranking cycle made no progress.", metrics)
        metrics["status"] = "partial"
    metrics["finished_at"] = datetime.now(UTC).isoformat()
    if metrics_out is not None:
        metrics_out.update(metrics)
    return len(snapshots)


async def _refresh_one_snapshot(
    session: AsyncSession,
    cache_key: str,
    request: RankingRequest,
) -> dict[str, object]:
    started = perf_counter()
    try:
        await create_or_refresh_snapshot(session, request)
    except asyncio.CancelledError:
        raise
    except (RankingRefreshInProgress, RankingRefreshRateLimited) as exc:
        logger.debug("Ranking snapshot refresh deferred: cache_key=%s", cache_key)
        return {
            "cache_key": cache_key,
            "status": "skipped",
            "error_code": type(exc).__name__,
            "duration_ms": max(round((perf_counter() - started) * 1_000), 0),
        }
    except Exception as exc:
        logger.warning(
            "Ranking snapshot refresh failed: cache_key=%s error=%s",
            cache_key,
            exc,
        )
        return {
            "cache_key": cache_key,
            "status": "failed",
            "error_code": type(exc).__name__,
            "duration_ms": max(round((perf_counter() - started) * 1_000), 0),
        }
    logger.info("Ranking snapshot refreshed: cache_key=%s", cache_key)
    return {
        "cache_key": cache_key,
        "status": "succeeded",
        "duration_ms": max(round((perf_counter() - started) * 1_000), 0),
    }


def _record_cycle_result(
    metrics: dict[str, object],
    cache_key: str,
    outcome: str,
    error: Exception | None = None,
) -> None:
    if outcome == "failed":
        metrics["failed"] = int(metrics["failed"]) + 1
    elif outcome == "succeeded":
        metrics["succeeded"] = int(metrics["succeeded"]) + 1
    elif outcome == "skipped":
        metrics["skipped"] = int(metrics.get("skipped", 0)) + 1
    elif outcome == "timed_out":
        metrics["timed_out"] = int(metrics.get("timed_out", 0)) + 1
    result: dict[str, object] = {"cache_key": cache_key, "status": outcome}
    if error is not None:
        result["error_code"] = type(error).__name__
    results = metrics.setdefault("results", [])
    if isinstance(results, list):
        results.append(result)


def _merge_cycle_result(metrics: dict[str, object], result: dict[str, object]) -> None:
    outcome = str(result.get("status", "failed"))
    _record_cycle_result(
        metrics,
        str(result.get("cache_key", "unknown")),
        outcome,
    )
    results = metrics.get("results")
    if isinstance(results, list) and results:
        # _record_cycle_result appended the compact result; retain structured
        # duration/error fields from the worker result as well.
        results[-1].update(
            {key: value for key, value in result.items() if key not in {"cache_key", "status"}}
        )


async def _clear_refresh_lease_on_cancel(
    session: AsyncSession,
    request: RankingRequest,
    token: UUID,
) -> None:
    snapshot = await session.scalar(
        select(RankingSnapshot)
        .options(
            load_only(
                RankingSnapshot.cache_key,
                RankingSnapshot.refresh_token,
            )
        )
        .where(RankingSnapshot.cache_key == request.cache_key)
        .with_for_update()
    )
    if snapshot is not None and snapshot.refresh_token == token:
        snapshot.refresh_token = None
        snapshot.refresh_expires_at = None
        snapshot.next_refresh_at = datetime.now(UTC)
        snapshot.last_error = "ranking_refresh_cancelled"
        snapshot.last_metrics = {
            "status": "cancelled",
            "error_code": "ranking_refresh_cancelled",
        }
    await session.commit()


async def fetch_live_ranking(
    request: RankingRequest, *, limit: int = SNAPSHOT_ITEM_LIMIT
) -> RankingData:
    """Provider boundary. Only cache misses and the worker call this function."""
    if request.kind == "hacker_news":
        return RankingData(
            kind=request.kind,
            title="Hacker News",
            subtitle="Top Stories · live",
            items=await fetch_hacker_news(limit),
            fetched_at=datetime.now(UTC),
        )
    if request.kind == "reddit":
        return RankingData(
            kind=request.kind,
            title=f"r/{request.subreddit}",
            subtitle=(
                f"{request.sort.title()} · {request.time_filter}"
                if request.sort == "top"
                else request.sort.title()
            ),
            items=await fetch_reddit_ranking(
                request.subreddit,
                sort=request.sort,
                time_filter=request.time_filter,
                limit=limit,
            ),
            fetched_at=datetime.now(UTC),
        )
    return RankingData(
        kind=request.kind,
        title="GitHub Trending",
        subtitle="Repositories · this week",
        items=await fetch_github_trending(limit),
        fetched_at=datetime.now(UTC),
    )


def _data_from_snapshot(snapshot: RankingSnapshot) -> RankingData:
    raw_items = snapshot.payload.get("items", [])
    items = [
        RankingItem(
            rank=int(raw["rank"]),
            title=str(raw["title"]),
            url=str(raw["url"]),
            source_label=raw.get("source_label"),
            description=raw.get("description"),
            author=raw.get("author"),
            score=raw.get("score"),
            comments=raw.get("comments"),
            language=raw.get("language"),
            stars=raw.get("stars"),
            forks=raw.get("forks"),
            stars_this_period=raw.get("stars_this_period"),
            image_urls=tuple(raw.get("image_urls", [])),
            native_id=raw.get("native_id"),
            published_at=_parse_datetime(raw.get("published_at")),
        )
        for raw in raw_items
        if isinstance(raw, dict)
    ]
    return RankingData(
        kind=snapshot.kind,  # type: ignore[arg-type]
        title=str(snapshot.payload.get("title") or "Ranking"),
        subtitle=str(snapshot.payload.get("subtitle") or ""),
        items=items,
        fetched_at=snapshot.fetched_at,
    )


def _payload_from_data(data: RankingData) -> dict[str, object]:
    return {
        "title": data.title,
        "subtitle": data.subtitle,
        "items": [
            item.__dict__
            | {
                "image_urls": list(item.image_urls),
                "published_at": item.published_at.isoformat() if item.published_at else None,
            }
            for item in data.items
        ],
    }


def _request_from_snapshot(snapshot: RankingSnapshot) -> RankingRequest:
    parameters = snapshot.parameters
    if not isinstance(parameters, dict):
        raise TypeError("snapshot parameters must be an object")
    if snapshot.kind not in {"hacker_news", "reddit", "github"}:
        raise ValueError(f"unsupported ranking kind: {snapshot.kind}")
    if snapshot.kind != "reddit":
        return RankingRequest(kind=snapshot.kind)  # type: ignore[arg-type]

    subreddit = parameters.get("subreddit") or "MachineLearning"
    sort = parameters.get("sort", "hot")
    time_filter = parameters.get("time_filter", "week")
    if not isinstance(subreddit, str) or not subreddit:
        raise ValueError("reddit snapshot subreddit must be a non-empty string")
    if sort not in {"hot", "rising", "top"}:
        raise ValueError(f"unsupported reddit sort: {sort}")
    if time_filter not in {"day", "week", "month", "year"}:
        raise ValueError(f"unsupported reddit time filter: {time_filter}")
    return RankingRequest(
        kind="reddit",
        subreddit=subreddit,
        sort=sort,  # type: ignore[arg-type]
        time_filter=time_filter,  # type: ignore[arg-type]
    )


def _retry_interval(
    exc: Exception,
    *,
    now: datetime | None = None,
    consecutive_rate_limits: int = 0,
) -> timedelta:
    response = getattr(exc, "response", None)
    retry_after = _retry_after_seconds(response, now=now)
    if retry_after is not None:
        return timedelta(seconds=retry_after)
    if getattr(response, "status_code", None) == 429:
        # Reddit RSS does not reliably send Retry-After.  Back off per
        # snapshot while _block_provider_refreshes pauses every Reddit board.
        minutes = min(15 * (2 ** max(consecutive_rate_limits, 0)), 120)
        return timedelta(minutes=minutes)
    return timedelta(minutes=5)


def _retry_after_seconds(response, *, now: datetime | None = None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
        if not isfinite(seconds):
            return None
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - (now or datetime.now(UTC))).total_seconds()
    return min(max(seconds, 1.0), 86_400.0)


def _snapshot_item_limit(request: RankingRequest) -> int:
    if request.kind == "github":
        return 25
    return SNAPSHOT_ITEM_LIMIT


def _provider_io_timeout_seconds(lease_seconds: int) -> float:
    """Leave time to record or publish the result before its DB lease expires."""
    return max(float(lease_seconds) - 30.0, 1.0)


async def ensure_reddit_snapshots(
    session: AsyncSession,
    subreddit: str,
    *,
    commit: bool = True,
) -> None:
    """Schedule all three boards when a community is subscribed."""
    now = datetime.now(UTC)
    requests = (
        RankingRequest(kind="reddit", subreddit=subreddit, sort="hot"),
        RankingRequest(kind="reddit", subreddit=subreddit, sort="rising"),
        RankingRequest(kind="reddit", subreddit=subreddit, sort="top", time_filter="week"),
    )
    for request in requests:
        await session.execute(
            pg_insert(RankingSnapshot)
            .values(
                cache_key=request.cache_key,
                kind="reddit",
                parameters=request.parameters,
                payload={
                    "title": f"r/{subreddit}",
                    "subtitle": request.sort.title(),
                    "items": [],
                },
                fetched_at=now,
                next_refresh_at=now,
                last_accessed_at=now,
                is_ready=False,
                refresh_token=None,
                refresh_expires_at=None,
                manual_refresh_after=None,
                last_error=None,
            )
            .on_conflict_do_update(
                index_elements=[RankingSnapshot.cache_key],
                set_={"last_accessed_at": now},
            )
        )
    if commit:
        await session.commit()


async def reconcile_reddit_snapshots(session: AsyncSession) -> None:
    """Repair missing boards for every durable enabled Reddit subscription."""
    await session.execute(text(_RECONCILE_REDDIT_SNAPSHOTS_SQL))
    await session.commit()


async def user_has_active_reddit_subscription(
    session: AsyncSession,
    user_id: UUID,
    subreddit: str,
) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    SourceSubscription.user_id == user_id,
                    SourceSubscription.is_enabled.is_(True),
                    SourceSubscription.source_id == FeedSource.id,
                    FeedSource.canonical_key == f"reddit:{subreddit.lower()}",
                )
            )
        )
    )


async def _ensure_snapshot_row(
    session: AsyncSession,
    request: RankingRequest,
    now: datetime,
) -> None:
    await session.execute(
        pg_insert(RankingSnapshot)
        .values(
            cache_key=request.cache_key,
            kind=request.kind,
            parameters=request.parameters,
            payload={"title": "Ranking", "subtitle": "", "items": []},
            fetched_at=now,
            next_refresh_at=now,
            last_accessed_at=now,
            is_ready=False,
            refresh_token=None,
            refresh_expires_at=None,
            manual_refresh_after=None,
            last_error=None,
        )
        .on_conflict_do_nothing(index_elements=[RankingSnapshot.cache_key])
    )
    await session.commit()


async def _claim_snapshot_refresh(
    session: AsyncSession,
    request: RankingRequest,
    *,
    force: bool,
    now: datetime,
) -> tuple[UUID | None, RankingData | None]:
    snapshot = await session.scalar(
        select(RankingSnapshot)
        .options(
            load_only(
                RankingSnapshot.cache_key,
                RankingSnapshot.next_refresh_at,
                RankingSnapshot.is_ready,
                RankingSnapshot.refresh_token,
                RankingSnapshot.refresh_expires_at,
                RankingSnapshot.manual_refresh_after,
                RankingSnapshot.last_error,
            )
        )
        .where(RankingSnapshot.cache_key == request.cache_key)
        .with_for_update()
    )
    if snapshot is None:
        raise RuntimeError("Ranking snapshot row disappeared while claiming refresh.")
    snapshot.last_accessed_at = now

    if not force and snapshot.next_refresh_at > now:
        data = await _load_snapshot_data(session, snapshot) if snapshot.is_ready else None
        await session.commit()
        if data is not None:
            return None, data
        retry_after = int((snapshot.next_refresh_at - now).total_seconds()) + 1
        raise RankingRefreshInProgress(
            "Initial ranking snapshot is waiting for its retry window.",
            retry_after,
        )

    if (
        snapshot.refresh_token is not None
        and snapshot.refresh_expires_at is not None
        and snapshot.refresh_expires_at > now
    ):
        data = await _load_snapshot_data(session, snapshot) if snapshot.is_ready else None
        await session.commit()
        if data is None:
            raise RankingRefreshInProgress("Initial ranking snapshot refresh is in progress.")
        return None, data

    if force and snapshot.last_error is not None and snapshot.next_refresh_at > now:
        retry_after = int((snapshot.next_refresh_at - now).total_seconds()) + 1
        await session.commit()
        raise RankingRefreshRateLimited(retry_after)

    if force and snapshot.manual_refresh_after is not None and snapshot.manual_refresh_after > now:
        retry_after = int((snapshot.manual_refresh_after - now).total_seconds()) + 1
        await session.commit()
        raise RankingRefreshRateLimited(retry_after)

    settings = get_settings()
    provider_retry_after = await _reserve_provider_refresh(session, request.kind, now)
    if provider_retry_after is not None:
        snapshot.next_refresh_at = max(
            snapshot.next_refresh_at,
            now + timedelta(seconds=provider_retry_after),
        )
        reusable = (
            await _load_snapshot_data(session, snapshot)
            if snapshot.is_ready and not force
            else None
        )
        await session.commit()
        if reusable is not None:
            return None, reusable
        raise RankingRefreshRateLimited(provider_retry_after)

    token = uuid4()
    snapshot.refresh_token = token
    snapshot.refresh_expires_at = now + timedelta(seconds=settings.ranking_refresh_lease_seconds)
    if force:
        snapshot.manual_refresh_after = now + timedelta(
            seconds=settings.ranking_manual_refresh_cooldown_seconds
        )
    await session.commit()
    return token, None


async def _publish_snapshot(
    session: AsyncSession,
    request: RankingRequest,
    token: UUID,
    data: RankingData,
) -> RankingData:
    from app.translation.lifecycle import lock_shared_lifecycle

    await lock_shared_lifecycle(session)
    snapshot = await session.scalar(
        select(RankingSnapshot)
        .options(
            load_only(
                RankingSnapshot.cache_key,
                RankingSnapshot.is_ready,
                RankingSnapshot.refresh_token,
                RankingSnapshot.last_metrics,
            )
        )
        .where(RankingSnapshot.cache_key == request.cache_key)
        .with_for_update()
    )
    if snapshot is None:
        raise RuntimeError("Ranking snapshot row disappeared before publish.")
    if snapshot.refresh_token != token:
        data = await _load_snapshot_data(session, snapshot) if snapshot.is_ready else None
        await session.commit()
        if data is not None:
            return data
        raise RankingRefreshInProgress("Ranking snapshot lease expired before publish.")

    now = datetime.now(UTC)
    snapshot.kind = request.kind
    snapshot.parameters = request.parameters
    snapshot.payload = _payload_from_data(data)
    snapshot.fetched_at = now
    snapshot.next_refresh_at = now + refresh_interval(request)
    snapshot.last_accessed_at = now
    snapshot.is_ready = True
    snapshot.refresh_token = None
    snapshot.refresh_expires_at = None
    snapshot.last_error = None
    snapshot.last_metrics = {
        "status": "succeeded",
        "item_count": len(data.items),
    }
    if request.kind == "reddit" and request.sort == "hot":
        await _enqueue_reddit_hot_admissions(session, request, data, now)
    await session.commit()
    return RankingData(
        kind=data.kind,
        title=data.title,
        subtitle=data.subtitle,
        items=data.items,
        fetched_at=now,
    )


async def _record_refresh_failure(
    session: AsyncSession,
    request: RankingRequest,
    token: UUID,
    exc: Exception,
) -> None:
    now = datetime.now(UTC)
    is_rate_limited = getattr(getattr(exc, "response", None), "status_code", None) == 429
    snapshot = await session.scalar(
        select(RankingSnapshot)
        .options(
            load_only(
                RankingSnapshot.cache_key,
                RankingSnapshot.refresh_token,
                RankingSnapshot.refresh_expires_at,
                RankingSnapshot.next_refresh_at,
                RankingSnapshot.last_error,
                RankingSnapshot.last_metrics,
            )
        )
        .where(RankingSnapshot.cache_key == request.cache_key)
        .with_for_update()
    )
    previous_rate_limits = 0
    if snapshot is not None and is_rate_limited:
        previous_metrics = getattr(snapshot, "last_metrics", None)
        if isinstance(previous_metrics, dict):
            previous_rate_limits = int(previous_metrics.get("rate_limit_streak", 0) or 0)
    retry_until = now + _retry_interval(
        exc,
        now=now,
        consecutive_rate_limits=previous_rate_limits,
    )
    if snapshot is not None and snapshot.refresh_token == token:
        snapshot.refresh_token = None
        snapshot.refresh_expires_at = None
        snapshot.last_error = str(exc)[:2000]
        snapshot.next_refresh_at = retry_until
        snapshot.last_metrics = {
            "status": "failed",
            "error_code": type(exc).__name__,
            "retry_after_seconds": max(round((retry_until - now).total_seconds()), 1),
            "rate_limit_streak": previous_rate_limits + 1 if is_rate_limited else 0,
        }
    # A late response still carries provider-wide rate-limit evidence even if
    # this snapshot lease has already been replaced. Never let the stale token
    # erase the current lease, but do propagate its 429 backoff globally.
    if is_rate_limited:
        await _block_provider_refreshes(
            session,
            request.kind,
            until=retry_until,
        )
    await session.commit()


async def _reserve_provider_refresh(
    session: AsyncSession,
    provider: str,
    now: datetime,
) -> int | None:
    await session.execute(
        pg_insert(RankingProviderBudget)
        .values(
            provider=provider,
            window_started_at=now,
            refresh_count=0,
            blocked_until=None,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[RankingProviderBudget.provider])
    )
    budget = await session.scalar(
        select(RankingProviderBudget)
        .where(RankingProviderBudget.provider == provider)
        .with_for_update()
    )
    if budget is None:
        raise RuntimeError("Ranking Provider budget row disappeared while reserving refresh.")
    if budget.blocked_until is not None and budget.blocked_until > now:
        return int((budget.blocked_until - now).total_seconds()) + 1

    settings = get_settings()
    if provider == "reddit":
        # A token-bucket count can still admit a burst when several snapshot
        # sessions arrive together.  Reserve the next slot durably instead:
        # every process observes the same gate, and a later 429 extends it.
        interval_seconds = 60 / settings.reddit_ranking_refreshes_per_minute
        budget.blocked_until = now + timedelta(seconds=interval_seconds)
        budget.refresh_count += 1
        budget.updated_at = now
        return None

    window = timedelta(minutes=1)
    if budget.window_started_at + window <= now:
        budget.window_started_at = now
        budget.refresh_count = 0
    limit = settings.ranking_provider_refreshes_per_minute
    if budget.refresh_count >= limit:
        return int((budget.window_started_at + window - now).total_seconds()) + 1
    budget.refresh_count += 1
    budget.updated_at = now
    return None


async def _block_provider_refreshes(
    session: AsyncSession,
    provider: str,
    *,
    until: datetime,
) -> None:
    budget = await session.scalar(
        select(RankingProviderBudget)
        .where(RankingProviderBudget.provider == provider)
        .with_for_update()
    )
    if budget is not None and (budget.blocked_until is None or budget.blocked_until < until):
        budget.blocked_until = until
        budget.updated_at = datetime.now(UTC)


async def _load_snapshot_data(
    session: AsyncSession,
    snapshot: RankingSnapshot,
) -> RankingData:
    """Load the large cached value only for a caller that will return it."""
    await session.refresh(
        snapshot,
        attribute_names=["kind", "payload", "fetched_at"],
    )
    return _data_from_snapshot(snapshot)


async def _retire_invalid_snapshot(
    session: AsyncSession,
    snapshot: RankingSnapshot,
    *,
    reason: str,
) -> None:
    """Remove a corrupt derived row without racing a concurrent repair."""
    await session.execute(
        delete(RankingSnapshot)
        .where(
            RankingSnapshot.cache_key == snapshot.cache_key,
            RankingSnapshot.kind == snapshot.kind,
            RankingSnapshot.parameters == snapshot.parameters,
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    logger.warning(
        "Retired invalid ranking snapshot: cache_key=%s reason=%s",
        snapshot.cache_key,
        reason,
    )


def _legacy_ranking_snapshot_predicate():
    return or_(
        RankingSnapshot.cache_key.in_(("ranking:hacker_news", "ranking:github")),
        RankingSnapshot.cache_key.like("ranking:reddit:%"),
    )


def _core_reddit_snapshot_predicate():
    sort = RankingSnapshot.parameters["sort"].astext
    time_filter = RankingSnapshot.parameters["time_filter"].astext
    return or_(
        sort.in_(("hot", "rising")),
        and_(sort == "top", time_filter == "week"),
    )


def _active_reddit_subscription_predicate():
    subreddit = func.lower(RankingSnapshot.parameters["subreddit"].astext)
    return exists(
        select(1)
        .select_from(FeedSource)
        .join(SourceSubscription, SourceSubscription.source_id == FeedSource.id)
        .where(
            SourceSubscription.is_enabled.is_(True),
            FeedSource.canonical_key == func.concat("reddit:", subreddit),
        )
    ).correlate(RankingSnapshot)


async def _enqueue_reddit_hot_admissions(
    session: AsyncSession,
    request: RankingRequest,
    data: RankingData,
    observed_at: datetime,
) -> None:
    source = await session.scalar(
        select(FeedSource)
        .join(SourceSubscription, SourceSubscription.source_id == FeedSource.id)
        .where(
            FeedSource.canonical_key == f"reddit:{request.subreddit.lower()}",
            SourceSubscription.is_enabled.is_(True),
        )
        .limit(1)
    )
    if source is None:
        return
    items = tuple(
        content
        for item in data.items[:10]
        if item.native_id
        if (
            content := DiscoveredContent(
                native_id=item.native_id,
                kind=ContentKind.POST,
                title=item.title,
                external_url=item.url,
                published_at=item.published_at,
                author_name=item.author,
                excerpt_html=item.description,
                raw_metadata={
                    "subreddit": request.subreddit,
                    "ranking": "hot",
                    "rank": item.rank,
                },
                media=tuple(
                    DiscoveredMedia(
                        original_url=url,
                        media_type=MediaType.IMAGE,
                        sort_order=index,
                    )
                    for index, url in enumerate(item.image_urls)
                ),
            )
        )
    )
    await upsert_candidates(
        session,
        source_id=source.id,
        items=items,
        observed_at=observed_at,
        max_changes=None,
        force_feed_sort_at=observed_at,
    )
    source.status = SourceStatus.ACTIVE
    state = await session.get(SourceSyncState, source.id)
    if state is not None:
        state.phase = SyncPhase.IDLE
        state.provider_mode = "reddit_snapshot"
        state.initial_sync_completed = True
        state.last_attempt_at = observed_at
        state.last_complete_at = observed_at
        state.next_scan_at = None
        state.last_error_code = None
        state.last_error_message = None


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
