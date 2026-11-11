"""Source scanning: claim due sources, read upstream, write articles.

One scan of one source:
  1. Claim: set a lease on the source row, so no other scan touches it.
  2. Read pages newest first. Every item on a page is checked; the next page is
     fetched only if no known item was seen and fewer than `scan_max_items`
     were read. A first scan reads one page of `scan_initial_items` as a
     baseline that never counts as new.
  3. Each page is written in its own transaction, under the source row lock:
     new items get the source's next sequence number (the new-content badge),
     changed items are updated in place without a new sequence.
  4. Finish: release the lease and schedule the next scan, or record the failure
     and back off.

The lease is checked before every write, so a scan that outlived its lease
(and was claimed again by another worker) can no longer write.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reader.core.settings import Settings
from reader.ingestion.adapters.reddit import RedditHotAdapter
from reader.ingestion.adapters.rss import RssAdapter
from reader.ingestion.adapters.x import XAdapter
from reader.ingestion.adapters.youtube import YouTubeAdapter
from reader.ingestion.errors import ScanError
from reader.ingestion.items import Adapter, Item, Page
from reader.ingestion.url_safety import PublicAsyncClient
from reader.ingestion.x_relations import preserve_x_metadata
from reader.models import Article, ProviderUsage, Source, SourceScanRun, Subscription

logger = logging.getLogger(__name__)

YOUTUBE_PROVIDER = "youtube_data_api"
# Leave room for one more scan's calls (channels + playlistItems + videos per page).
_YOUTUBE_HEADROOM = 10


class LeaseLost(Exception):
    """Another scan now owns this source; this one must stop writing."""


@dataclass(frozen=True)
class Claim:
    source_id: UUID
    token: UUID


@dataclass
class ScanResult:
    items_read: int = 0
    new_items: int = 0
    reached_known: bool = False
    chronological: bool = True
    hit_limit: bool = False


# --- Claiming ---------------------------------------------------------------------


async def claim_due_sources(
    factory: async_sessionmaker[AsyncSession], settings: Settings, limit: int
) -> list[Claim]:
    """Lease up to `limit` sources whose next scan is due and that someone follows."""
    now = datetime.now(UTC)
    async with factory() as db:
        followed = exists().where(
            Subscription.source_id == Source.id, Subscription.is_enabled.is_(True)
        )
        sources = list(
            await db.scalars(
                select(Source)
                .where(
                    Source.status != "paused",
                    Source.next_scan_at <= now,
                    (Source.lease_expires_at.is_(None)) | (Source.lease_expires_at < now),
                    followed,
                )
                .order_by(Source.next_scan_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        claims = []
        for source in sources:
            source.lease_token = uuid4()
            source.lease_expires_at = now + timedelta(seconds=settings.scan_lease_seconds)
            claims.append(Claim(source.id, source.lease_token))
        await db.commit()
        return claims


# --- One scan ---------------------------------------------------------------------


async def scan_source(
    factory: async_sessionmaker[AsyncSession], settings: Settings, claim: Claim
) -> None:
    """Scan one claimed source; never raises for upstream failures."""
    async with factory() as db:
        source = await db.get(Source, claim.source_id)
        if source is None or source.lease_token != claim.token:
            return
        run = SourceScanRun(source_id=source.id, status="running")
        db.add(run)
        await db.commit()
        snapshot = _SourceSnapshot.of(source)
        run_id = run.id
    youtube_budget = await _youtube_budget(factory, settings) if snapshot.kind == "youtube" else 0
    try:
        async with asyncio.timeout(settings.scan_timeout_seconds):
            async with _http_client(snapshot.kind) as client:
                adapter = _build_adapter(snapshot, client, settings, youtube_budget > 0)
                try:
                    result = await _read_and_store(factory, settings, claim, snapshot, adapter)
                finally:
                    if isinstance(adapter, YouTubeAdapter) and adapter.api_calls:
                        await _record_youtube_usage(factory, adapter.api_calls)
    except LeaseLost:
        logger.warning("Scan lease lost: source_id=%s", claim.source_id)
        return
    except TimeoutError:
        await _finish_failed(
            factory, claim, run_id, ScanError("scan_timeout", "Scan took too long.")
        )
    except ScanError as exc:
        await _finish_failed(factory, claim, run_id, exc)
    except Exception:
        logger.exception("Unexpected scan failure: source_id=%s", claim.source_id)
        await _finish_failed(
            factory, claim, run_id, ScanError("internal_error", "Unexpected scan failure.")
        )
    else:
        await _finish_succeeded(factory, settings, claim, run_id, snapshot, result)


@dataclass(frozen=True)
class _SourceSnapshot:
    """The source as it was when the scan started; read without holding a lock."""

    id: UUID
    kind: str
    url: str
    config: dict
    initial: bool
    needs_icon: bool

    @classmethod
    def of(cls, source: Source) -> _SourceSnapshot:
        return cls(
            id=source.id,
            kind=source.kind,
            url=source.url,
            config=dict(source.config),
            initial=not source.initial_scan_done,
            needs_icon=source.icon_url is None,
        )


def _http_client(kind: str) -> httpx.AsyncClient:
    # The Scweet service is an operator-configured internal host; everything else
    # is untrusted and must go through the public-only client.
    return httpx.AsyncClient(trust_env=False) if kind == "x" else PublicAsyncClient()


def _build_adapter(
    source: _SourceSnapshot, client: httpx.AsyncClient, settings: Settings, use_youtube_api: bool
) -> Adapter:
    max_bytes = settings.scan_max_response_bytes
    if source.kind == "rss":
        return RssAdapter(client, source.url, max_bytes=max_bytes, find_icon=source.needs_icon)
    if source.kind == "reddit":
        return RedditHotAdapter(client, subreddit=source.config["subreddit"], max_bytes=max_bytes)
    if source.kind == "youtube":
        key = settings.youtube_data_api_key
        return YouTubeAdapter(
            client,
            channel_id=source.config["channel_id"],
            feed_url=source.url,
            api_key=key.get_secret_value() if key and use_youtube_api else None,
            uploads_playlist_id=source.config.get("uploads_playlist_id"),
            max_bytes=max_bytes,
            find_icon=source.needs_icon,
        )
    if source.kind == "x":
        if not settings.scweet_service_url or not settings.scweet_service_token:
            raise ScanError(
                "scweet_not_configured",
                "APP_SCWEET_SERVICE_URL and APP_SCWEET_SERVICE_TOKEN are required.",
                long_lived=True,
            )
        return XAdapter(
            client,
            handle=source.config["handle"],
            service_url=str(settings.scweet_service_url),
            service_token=settings.scweet_service_token.get_secret_value(),
            max_bytes=max_bytes,
            find_icon=source.needs_icon,
        )
    raise ScanError("unsupported_source", f"No scanner for source kind {source.kind}.")


async def _read_and_store(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    claim: Claim,
    source: _SourceSnapshot,
    adapter: Adapter,
) -> ScanResult:
    budget = settings.scan_initial_items if source.initial else settings.scan_max_items
    result = ScanResult()
    cursor: str | None = None
    while True:
        page = await adapter.fetch_page(cursor, budget - result.items_read)
        result.items_read += len(page.items)
        result.chronological = page.chronological
        new, reached_known = await _store_page(factory, claim, page, initial=source.initial)
        result.new_items += new
        result.reached_known = result.reached_known or reached_known
        if source.initial or result.reached_known or not page.next_cursor:
            break
        if result.items_read >= budget:
            result.hit_limit = True
            break
        cursor = page.next_cursor
    return result


async def _store_page(
    factory: async_sessionmaker[AsyncSession], claim: Claim, page: Page, *, initial: bool
) -> tuple[int, bool]:
    """Write one page in one transaction; return (new items, saw a known item)."""
    items = _unique(page.items)
    async with factory() as db:
        source = await _lock_owned_source(db, claim)
        existing = (
            {
                article.native_id: article
                for article in await db.scalars(
                    select(Article).where(
                        Article.source_id == source.id,
                        Article.native_id.in_([item.native_id for item in items]),
                    )
                )
            }
            if items
            else {}
        )
        reached_known = False
        new_items: list[Item] = []
        for item in items:
            article = existing.get(item.native_id)
            if article is None:
                new_items.append(item)
                continue
            if not item.pinned:
                reached_known = True
            _update_article(article, item)
        # Oldest first, so newer items get higher sequence numbers.
        for item in reversed(new_items):
            sequence = None
            if not initial:
                source.latest_sequence += 1
                sequence = source.latest_sequence
            db.add(_new_article(source.id, item, sequence))
        _apply_source_metadata(source, page)
        await db.commit()
        return (0 if initial else len(new_items)), reached_known


async def _lock_owned_source(db: AsyncSession, claim: Claim) -> Source:
    source = await db.scalar(select(Source).where(Source.id == claim.source_id).with_for_update())
    if source is None or source.lease_token != claim.token:
        raise LeaseLost
    return source


def _unique(items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    unique = []
    for item in items:
        if item.native_id not in seen:
            seen.add(item.native_id)
            unique.append(item)
    return unique


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _new_article(source_id: UUID, item: Item, sequence: int | None) -> Article:
    return Article(
        source_id=source_id,
        native_id=item.native_id,
        url=item.url,
        url_hash=url_hash(item.url),
        kind=item.kind,
        title=item.title[:1000],
        author=item.author[:300] if item.author else None,
        published_at=item.published_at,
        summary=item.summary,
        body_html=item.body_html,
        media=[media.to_json() for media in item.media],
        extra=item.extra,
        content_hash=item.content_hash(),
        sequence=sequence,
    )


def _update_article(article: Article, item: Item) -> None:
    """An edited upstream item is rewritten in place; it is not new."""
    content_hash = item.content_hash()
    if content_hash == article.content_hash:
        return
    article.url = item.url
    article.url_hash = url_hash(item.url)
    article.kind = item.kind
    article.title = item.title[:1000]
    article.author = item.author[:300] if item.author else None
    # A missing date in one observation does not erase a known one.
    article.published_at = item.published_at or article.published_at
    article.summary = item.summary
    article.body_html = item.body_html
    article.media = [media.to_json() for media in item.media]
    # A fallback without full X data must not erase relations already parsed.
    article.extra = preserve_x_metadata(article.extra or {}, item.extra)
    article.content_hash = content_hash


def _apply_source_metadata(source: Source, page: Page) -> None:
    if page.source_name and not source.display_name:
        source.display_name = page.source_name.strip()[:300]
    if page.source_icon_url and not source.icon_url:
        source.icon_url = page.source_icon_url
    if page.source_config:
        source.config = {**source.config, **page.source_config}


# --- Finishing --------------------------------------------------------------------


async def _finish_succeeded(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    claim: Claim,
    run_id: UUID,
    snapshot: _SourceSnapshot,
    result: ScanResult,
) -> None:
    now = datetime.now(UTC)
    async with factory() as db:
        try:
            source = await _lock_owned_source(db, claim)
        except LeaseLost:
            return
        source.initial_scan_done = True
        source.status = "active" if source.status == "pending" else source.status
        source.last_success_at = now
        source.consecutive_failures = 0
        source.last_error_code = source.last_error_message = None
        source.last_new_items = result.new_items
        source.unchanged_scans = 0 if result.new_items else source.unchanged_scans + 1
        # Only a time-ordered source that never reached a known item can have missed some.
        source.may_have_gap = (
            not snapshot.initial and result.chronological and not result.reached_known
        )
        source.next_scan_at = now + next_scan_delay(source)
        source.lease_token = source.lease_expires_at = None
        await db.execute(
            update(SourceScanRun)
            .where(SourceScanRun.id == run_id)
            .values(
                status="success",
                finished_at=now,
                items_read=result.items_read,
                new_items=result.new_items,
            )
        )
        await db.commit()


async def _finish_failed(
    factory: async_sessionmaker[AsyncSession], claim: Claim, run_id: UUID, error: ScanError
) -> None:
    now = datetime.now(UTC)
    async with factory() as db:
        try:
            source = await _lock_owned_source(db, claim)
        except LeaseLost:
            return
        source.consecutive_failures += 1
        source.last_error_code = error.code[:120]
        source.last_error_message = str(error)[:2000]
        source.next_scan_at = now + retry_delay(error, source.consecutive_failures)
        source.lease_token = source.lease_expires_at = None
        await db.execute(
            update(SourceScanRun)
            .where(SourceScanRun.id == run_id)
            .values(
                status="failed",
                finished_at=now,
                error_code=error.code[:120],
                error_message=str(error)[:2000],
            )
        )
        await db.commit()
    logger.info("Scan failed: source_id=%s code=%s", claim.source_id, error.code)


# --- Scheduling -------------------------------------------------------------------

# (usual, fastest, slowest) minutes between scans, per kind.
_INTERVALS = {"rss": (60, 30, 120), "reddit": (60, 30, 120), "youtube": (60, 60, 120)}
_DEFAULT_INTERVAL = (120, 60, 240)


def next_scan_minutes(source: Source) -> int:
    """Busier sources are scanned more often, quiet ones less."""
    usual, fastest, slowest = _INTERVALS.get(source.kind, _DEFAULT_INTERVAL)
    if source.unchanged_scans >= 3:
        return slowest
    if source.last_new_items >= 10:
        return fastest
    if source.last_new_items >= 3:
        return max(fastest, (fastest + usual) // 2)
    return usual


def next_scan_delay(source: Source) -> timedelta:
    return timedelta(minutes=next_scan_minutes(source) * random.uniform(0.9, 1.1))


def retry_delay(error: ScanError, failures: int) -> timedelta:
    if error.retry_after_seconds is not None:
        return timedelta(seconds=max(error.retry_after_seconds, 60))
    if error.long_lived:
        return timedelta(hours=24)
    minutes = (2, 10, 30, 120, 360)[min(max(failures - 1, 0), 4)]
    return timedelta(minutes=minutes * random.uniform(0.9, 1.1))


# --- YouTube Data API quota ----------------------------------------------------------


def _today() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def _youtube_budget(factory: async_sessionmaker[AsyncSession], settings: Settings) -> int:
    """API units left today; 0 means use the Atom feed."""
    if not settings.youtube_data_api_key:
        return 0
    async with factory() as db:
        used = await db.scalar(
            select(ProviderUsage.units).where(
                ProviderUsage.provider == YOUTUBE_PROVIDER, ProviderUsage.window_start == _today()
            )
        )
    left = settings.youtube_daily_quota - (used or 0)
    return left if left > _YOUTUBE_HEADROOM else 0


async def _record_youtube_usage(factory: async_sessionmaker[AsyncSession], units: int) -> None:
    async with factory() as db:
        statement = pg_insert(ProviderUsage).values(
            provider=YOUTUBE_PROVIDER, window_start=_today(), units=units
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[ProviderUsage.provider, ProviderUsage.window_start],
                set_={"units": ProviderUsage.units + statement.excluded.units},
            )
        )
        await db.commit()


# --- The worker job ---------------------------------------------------------------


async def scan_due_sources(factory: async_sessionmaker[AsyncSession], settings: Settings) -> int:
    """Claim and scan due sources, a few at a time; return how many were scanned."""
    claims = await claim_due_sources(factory, settings, settings.scan_concurrency)
    await asyncio.gather(*(scan_source(factory, settings, claim) for claim in claims))
    return len(claims)
