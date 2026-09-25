"""Durable, lease-based source scanner orchestration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse, urlunparse
from uuid import UUID, uuid4

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.settings import Settings, get_settings
from app.domain.enums import ContentKind, SourceKind, SourceStatus, SyncRunStatus
from app.ingestion.candidate_queue import upsert_candidates, upsert_web_frontier
from app.ingestion.feed_discovery import discover_web_feed
from app.ingestion.models import SourceScanError, SourceScanPage
from app.ingestion.sources.apify_x import ApifyXSourceAdapter, ApifyXSourceConfig
from app.ingestion.sources.base import SourceAdapter
from app.ingestion.sources.rss import RSSSourceAdapter, RSSSourceConfig
from app.ingestion.sources.scweet_x import ScweetXSourceAdapter, ScweetXSourceConfig
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.sources.youtube_data import YouTubeSourceAdapter, YouTubeSourceConfig
from app.ingestion.url_safety import PublicAsyncClient
from app.ingestion.web_feed import configured_web_feed_url, web_feed_probe_required
from app.ingestion.web_rules import WebRuleError, parse_web_rule
from app.services.scan_state import (
    PageDisposition,
    ScanProgress,
    checkpoint_hashes,
    claim_scan,
    empty_run_metrics,
    finish_incomplete_run,
    mark_web_head_observed,
    record_scan_failure,
    recovered_run_values,
    start_scan,
)
from app.services.web_feeds import save_web_feed_discovery, schedule_web_feed_probes
from app.services.web_scan_health import record_web_structure_health
from app.storage.models import (
    FeedSource,
    ProviderQuotaUsage,
    SourceSubscription,
    SourceSyncRun,
    SourceSyncState,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceClaim:
    source_id: UUID
    lease_token: UUID


async def claim_due_sources(session, *, limit: int = 20) -> list[SourceClaim]:
    """Atomically claim due sources and commit before any upstream request."""
    if limit < 1:
        return []
    settings = get_settings()
    now = datetime.now(UTC)
    await schedule_web_feed_probes(session, now=now)
    states = list(
        await session.scalars(
            select(SourceSyncState)
            .join(FeedSource, FeedSource.id == SourceSyncState.source_id)
            .where(
                FeedSource.status != SourceStatus.PAUSED,
                FeedSource.kind.not_in([SourceKind.REDDIT, SourceKind.HACKER_NEWS]),
                SourceSyncState.next_scan_at.is_not(None),
                SourceSyncState.next_scan_at <= now,
                or_(
                    SourceSyncState.lease_expires_at.is_(None),
                    SourceSyncState.lease_expires_at < now,
                ),
                _enabled_subscription_exists(),
            )
            .order_by(SourceSyncState.next_scan_at, SourceSyncState.source_id)
            .limit(limit)
            .with_for_update(of=FeedSource, skip_locked=True)
        )
    )
    claims: list[SourceClaim] = []
    for state in states:
        # Every path that changes a source and its scan state uses this order.
        source = await session.get(
            FeedSource, state.source_id, with_for_update=True, populate_existing=True
        )
        state = await session.get(
            SourceSyncState, state.source_id, with_for_update=True, populate_existing=True
        )
        # The join can have read this state before acquiring its source lock.
        # Refreshing also replaces an older instance in this session's identity
        # map; never overwrite a lease another scanner has already committed.
        if (
            source is None
            or state is None
            or source.status == SourceStatus.PAUSED
            or source.kind in {SourceKind.REDDIT, SourceKind.HACKER_NEWS}
            or state.next_scan_at is None
            or state.next_scan_at > now
            or (state.lease_expires_at is not None and state.lease_expires_at >= now)
            or not await session.scalar(
                select(SourceSubscription.id)
                .where(
                    SourceSubscription.source_id == state.source_id,
                    SourceSubscription.is_enabled.is_(True),
                )
                .limit(1)
            )
        ):
            continue
        token = uuid4()
        claim_scan(state, token, now, settings.ingestion_scan_lease_seconds)
        claims.append(SourceClaim(state.source_id, token))
    await session.commit()
    return claims


def _enabled_subscription_exists():
    return (
        select(SourceSubscription.id)
        .where(
            SourceSubscription.source_id == SourceSyncState.source_id,
            SourceSubscription.is_enabled.is_(True),
        )
        .exists()
    )


async def sync_due_sources(session_factory, *, limit: int = 20) -> int:
    settings = get_settings()
    claim_limit = min(limit, settings.ingestion_source_concurrency)
    async with session_factory() as session:
        claims = await claim_due_sources(session, limit=claim_limit)
    if not claims:
        return 0
    # Claims are already durable, so concurrency cannot create duplicate scans.
    results = await asyncio.gather(
        *(scan_claimed_source(session_factory, claim) for claim in claims),
        return_exceptions=True,
    )
    completed = 0
    for claim, result in zip(claims, results, strict=True):
        if isinstance(result, Exception):
            logger.error(
                "Unhandled source scan failure: source_id=%s",
                claim.source_id,
                exc_info=(type(result), result, result.__traceback__),
            )
            await _record_unhandled_failure(session_factory, claim, result)
        else:
            completed += 1
    return completed


async def scan_claimed_source(session_factory, claim: SourceClaim) -> None:
    settings = get_settings()
    source, state, run_id = await _start_run(session_factory, claim)
    if source is None or state is None or run_id is None:
        return
    try:
        async with asyncio.timeout(settings.ingestion_source_timeout_seconds):
            async with _build_source_http_client(source, settings) as client:
                if source.kind == SourceKind.WEB and web_feed_probe_required(
                    source.canonical_url, source.config
                ):
                    result = await discover_web_feed(
                        source.canonical_url,
                        client,
                        max_response_bytes=min(
                            settings.ingestion_max_response_bytes, 2 * 1024 * 1024
                        ),
                    )
                    updated = await save_web_feed_discovery(
                        session_factory,
                        source_id=source.id,
                        lease_token=claim.lease_token,
                        run_id=run_id,
                        source_url=source.canonical_url,
                        rule_revision=int((source.config or {}).get("web_rule_revision") or 0),
                        result=result,
                    )
                    if updated is None:
                        return
                    source, state = updated
                    if result.status == "blocked":
                        raise SourceScanError(
                            result.evidence["error_code"],
                            "The website or RSS endpoint denied access; RSS discovery will retry.",
                            long_lived=True,
                        )
                    if result.status == "retry":
                        raise SourceScanError(
                            "web_feed_discovery_retry",
                            "RSS/Atom discovery will retry after a temporary failure.",
                        )
                use_youtube_data_api = True
                if source.kind == SourceKind.YOUTUBE:
                    use_youtube_data_api = await _reserve_youtube_quota(
                        session_factory,
                        units=settings.ingestion_max_pages_per_sync * 3,
                    )
                adapter = _build_adapter(
                    source,
                    client,
                    use_youtube_data_api=use_youtube_data_api,
                )
                # A durable continuation wins even when the provider is degraded.
                await _scan_pages(session_factory, claim, run_id, source, state, adapter)
    except TimeoutError:
        await _record_scan_failure(
            session_factory,
            claim,
            run_id,
            SourceScanError("source_timeout", "Source scan exceeded its time limit."),
        )
    except SourceScanError as exc:
        await _record_scan_failure(session_factory, claim, run_id, exc)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        await _record_scan_failure(
            session_factory,
            claim,
            run_id,
            SourceScanError(type(exc).__name__, str(exc)),
        )


async def _start_run(session_factory, claim: SourceClaim):
    async with session_factory() as session:
        source = await session.get(FeedSource, claim.source_id, with_for_update=True)
        state = await session.scalar(
            select(SourceSyncState)
            .where(SourceSyncState.source_id == claim.source_id)
            .with_for_update()
        )
        if state is None or source is None or state.lease_token != claim.lease_token:
            await session.rollback()
            return None, None, None
        now = datetime.now(UTC)
        await session.execute(
            update(SourceSyncRun)
            .where(
                SourceSyncRun.source_id == source.id,
                SourceSyncRun.status == SyncRunStatus.RUNNING,
            )
            .values(**recovered_run_values(now))
        )
        run = SourceSyncRun(
            source_id=source.id,
            status=SyncRunStatus.RUNNING,
            metrics=empty_run_metrics(),
        )
        session.add(run)
        start_scan(state, now)
        await session.commit()
        # Detach plain source/state snapshots before upstream I/O.
        return source, state, run.id


async def _scan_pages(
    session_factory,
    claim: SourceClaim,
    run_id: UUID,
    source: FeedSource,
    state: SourceSyncState,
    adapter: SourceAdapter,
) -> None:
    settings = get_settings()
    progress = ScanProgress(
        source=source,
        state=state,
        max_pages=settings.ingestion_max_pages_per_sync,
        max_raw_items=settings.ingestion_raw_items_per_sync,
        max_changes=settings.ingestion_max_items_per_sync,
        max_response_bytes=settings.ingestion_max_response_bytes,
    )
    while request := progress.next_request():
        page = await adapter.scan_page(request)
        disposition = await _persist_page(
            session_factory,
            claim,
            run_id,
            page,
            progress=progress,
        )
        if disposition == PageDisposition.COMPLETE:
            return
        if disposition == PageDisposition.CANDIDATE_LIMIT:
            await _finish_incomplete_run(session_factory, claim, run_id, limit_reached="candidates")
            return
    await _finish_incomplete_run(
        session_factory, claim, run_id, limit_reached=progress.incomplete_reason()
    )


async def _persist_page(
    session_factory,
    claim: SourceClaim,
    run_id: UUID,
    page: SourceScanPage,
    progress: ScanProgress,
) -> PageDisposition:
    async with session_factory() as session:
        source = await session.get(FeedSource, claim.source_id, with_for_update=True)
        state = await session.scalar(
            select(SourceSyncState)
            .where(SourceSyncState.source_id == claim.source_id)
            .with_for_update()
        )
        run = await session.get(SourceSyncRun, run_id)
        if state is None or source is None or run is None or state.lease_token != claim.lease_token:
            raise SourceScanError("lease_lost", "Source scanner lease was lost.")
        now = datetime.now(UTC)
        outcome = await upsert_candidates(
            session,
            source_id=source.id,
            items=tuple(progress.candidate_items(page)),
            observed_at=now,
            max_changes=progress.candidate_budget(),
            baseline_hashes=checkpoint_hashes(progress.baseline_checkpoint()),
            counts_as_update=state.initial_sync_completed,
        )
        if page.web_links:
            await upsert_web_frontier(
                session, source_id=source.id, links=page.web_links, observed_at=now
            )
        if page.source_avatar_url and source.kind != SourceKind.REDDIT:
            source.avatar_url = page.source_avatar_url
        if page.source_display_name and _should_update_display_name(source):
            source.display_name = page.source_display_name.strip()[:300]
        if page.source_config_updates:
            source.config = {**source.config, **page.source_config_updates}
        disposition = progress.apply_page(state, source, run, page, outcome, now)
        if (
            source.kind == SourceKind.WEB
            and page.web_listing_evidence.get("is_head")
            and not (run.metrics or {}).get("web_head_observed")
        ):
            await record_web_structure_health(
                session,
                source,
                state,
                code=page.warning_code,
                evidence=page.web_listing_evidence,
                now=now,
            )
            mark_web_head_observed(run, page.web_listing_evidence)
        await session.commit()
        return disposition


async def _finish_incomplete_run(
    session_factory,
    claim: SourceClaim,
    run_id: UUID,
    *,
    limit_reached: str,
) -> None:
    async with session_factory() as session:
        durable_source = await session.get(FeedSource, claim.source_id, with_for_update=True)
        state = await session.scalar(
            select(SourceSyncState)
            .where(SourceSyncState.source_id == claim.source_id)
            .with_for_update()
        )
        run = await session.get(SourceSyncRun, run_id)
        if state is None or run is None or durable_source is None:
            return
        if state.lease_token != claim.lease_token:
            return
        now = datetime.now(UTC)
        finish_incomplete_run(durable_source, state, run, now=now, limit_reached=limit_reached)
        await session.commit()


async def _record_scan_failure(
    session_factory,
    claim: SourceClaim,
    run_id: UUID,
    exc: SourceScanError,
) -> None:
    async with session_factory() as session:
        source = await session.get(FeedSource, claim.source_id, with_for_update=True)
        state = await session.scalar(
            select(SourceSyncState)
            .where(SourceSyncState.source_id == claim.source_id)
            .with_for_update()
        )
        run = await session.get(SourceSyncRun, run_id)
        if state is None or source is None or state.lease_token != claim.lease_token:
            return
        now = datetime.now(UTC)
        record_scan_failure(source, state, run, exc, now)
        if source.kind == SourceKind.WEB and not (
            exc.evidence.get("is_head")
            and run is not None
            and (run.metrics or {}).get("web_head_observed")
        ):
            await record_web_structure_health(
                session,
                source,
                state,
                code=exc.code,
                evidence=exc.evidence,
                now=now,
            )
        await session.commit()
        logger.warning(
            "Source scan failed: source_id=%s code=%s error=%s", source.id, exc.code, exc
        )


async def _record_unhandled_failure(session_factory, claim: SourceClaim, exc: Exception) -> None:
    async with session_factory() as session:
        run_id = await session.scalar(
            select(SourceSyncRun.id)
            .where(
                SourceSyncRun.source_id == claim.source_id,
                SourceSyncRun.status == SyncRunStatus.RUNNING,
            )
            .order_by(SourceSyncRun.started_at.desc())
            .limit(1)
        )
    if run_id:
        await _record_scan_failure(
            session_factory,
            claim,
            run_id,
            SourceScanError(type(exc).__name__, str(exc)),
        )


def _build_adapter(
    source: FeedSource,
    client: httpx.AsyncClient,
    *,
    use_youtube_data_api: bool = True,
) -> SourceAdapter:
    settings = get_settings()
    if source.kind == SourceKind.RSS:
        return RSSSourceAdapter(
            RSSSourceConfig(
                url=source.canonical_url,
                content_kind=ContentKind.ARTICLE,
                site_url=_site_root_url(source),
                resolve_site_avatar=source.avatar_url is None,
            ),
            client,
        )
    if source.kind == SourceKind.WEB:
        feed_url = configured_web_feed_url(source.canonical_url, source.config)
        if feed_url:
            return RSSSourceAdapter(
                RSSSourceConfig(
                    url=feed_url,
                    site_url=source.canonical_url,
                    resolve_site_avatar=source.avatar_url is None,
                    provider_mode="web_rss",
                ),
                client,
            )
        raw_rule = source.config.get("web_rule")
        if raw_rule is None:
            raise SourceScanError(
                "web_rule_required", "Web source needs an accepted native rule.", long_lived=True
            )
        try:
            rule = parse_web_rule(raw_rule, source_url=source.canonical_url)
        except WebRuleError as exc:
            raise SourceScanError(
                "web_rule_required",
                "Stored Web rule is invalid; author a native rule.",
                long_lived=True,
            ) from exc
        return WebBlogSourceAdapter(
            WebBlogSourceConfig(
                url=source.canonical_url,
                resolve_site_avatar=source.avatar_url is None,
                rule=rule,
            ),
            client,
        )
    if source.kind == SourceKind.YOUTUBE:
        channel_id = str(source.config.get("channel_id") or _youtube_channel_id(source))
        api_key = (
            settings.youtube_data_api_key.get_secret_value()
            if settings.youtube_data_api_key
            else None
        )
        return YouTubeSourceAdapter(
            YouTubeSourceConfig(
                channel_id=channel_id,
                feed_url=source.canonical_url,
                api_key=api_key,
                uploads_playlist_id=str(source.config.get("uploads_playlist_id") or "") or None,
                use_data_api=use_youtube_data_api,
            ),
            client,
        )
    if source.kind == SourceKind.X:
        handle = str(source.config["handle"])
        if settings.x_provider == "scweet":
            if not settings.scweet_service_url or not settings.scweet_service_token:
                raise SourceScanError(
                    "scweet_not_configured",
                    "APP_SCWEET_SERVICE_URL and APP_SCWEET_SERVICE_TOKEN are required.",
                    long_lived=True,
                )
            return ScweetXSourceAdapter(
                ScweetXSourceConfig(
                    handle=handle,
                    service_url=str(settings.scweet_service_url),
                    service_token=settings.scweet_service_token,
                ),
                client,
            )
        if not settings.apify_token:
            raise SourceScanError(
                "apify_not_configured",
                "APP_APIFY_TOKEN is required when APP_X_PROVIDER=apify.",
                long_lived=True,
            )
        return ApifyXSourceAdapter(
            ApifyXSourceConfig(handle=handle, token=settings.apify_token), client
        )
    raise SourceScanError("unsupported_source", f"No scanner for source kind {source.kind}.")


def _build_source_http_client(
    source: FeedSource,
    settings: Settings,
) -> httpx.AsyncClient:
    """Keep the operator-configured Scweet edge separate from untrusted URLs."""
    if source.kind == SourceKind.X and settings.x_provider == "scweet":
        return httpx.AsyncClient(trust_env=False)
    return PublicAsyncClient()


async def _reserve_youtube_quota(session_factory, *, units: int = 4) -> bool:
    """Reserve a conservative scan cost without crossing the daily soft budget."""
    settings = get_settings()
    if not settings.youtube_data_api_key or settings.youtube_daily_quota_soft_limit < units:
        return False
    now = datetime.now(UTC)
    async with session_factory() as session:
        statement = (
            pg_insert(ProviderQuotaUsage)
            .values(
                provider="youtube_data_api",
                usage_date=now.date(),
                units=units,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[
                    ProviderQuotaUsage.provider,
                    ProviderQuotaUsage.usage_date,
                ],
                set_={
                    "units": ProviderQuotaUsage.units + units,
                    "updated_at": now,
                },
                where=(ProviderQuotaUsage.units + units <= settings.youtube_daily_quota_soft_limit),
            )
            .returning(ProviderQuotaUsage.units)
        )
        reserved = await session.scalar(statement)
        await session.commit()
        return reserved is not None


def _should_update_display_name(source: FeedSource) -> bool:
    return source.display_name is None or (
        source.kind == SourceKind.YOUTUBE and source.display_name.startswith("YouTube ·")
    )


def _youtube_channel_id(source: FeedSource) -> str:
    value = parse_qs(urlparse(source.canonical_url).query).get("channel_id", [""])[0]
    if not value:
        raise SourceScanError("youtube_channel_missing", "YouTube source has no channel ID.")
    return value


def _site_root_url(source: FeedSource) -> str | None:
    parsed = urlparse(source.canonical_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))
