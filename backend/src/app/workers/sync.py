"""Durable, lease-based source scanner orchestration."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse
from uuid import UUID, uuid4

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.settings import Settings, get_settings
from app.domain.enums import (
    ContentKind,
    SourceKind,
    SourceStatus,
    SyncPhase,
    SyncRunStatus,
)
from app.ingestion.candidate_queue import (
    CandidateUpsertResult,
    upsert_candidates,
    upsert_web_frontier,
)
from app.ingestion.feed_discovery import discover_web_feed
from app.ingestion.models import SourceScanError, SourceScanPage, SourceScanRequest
from app.ingestion.sources.apify_x import ApifyXSourceAdapter, ApifyXSourceConfig
from app.ingestion.sources.base import SourceAdapter
from app.ingestion.sources.rss import RSSSourceAdapter, RSSSourceConfig
from app.ingestion.sources.scweet_x import ScweetXSourceAdapter, ScweetXSourceConfig
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.sources.youtube_data import YouTubeSourceAdapter, YouTubeSourceConfig
from app.ingestion.url_safety import PublicAsyncClient
from app.ingestion.web_feed import configured_web_feed_url, web_feed_probe_required
from app.ingestion.web_rules import WebRuleError, parse_web_rule
from app.services.web_feeds import save_web_feed_discovery, schedule_web_feed_probes
from app.services.web_rule_jobs import ensure_web_rule_job
from app.storage.models import (
    FeedSource,
    ProviderQuotaUsage,
    SourceSubscription,
    SourceSyncRun,
    SourceSyncState,
)

logger = logging.getLogger(__name__)
_RESTART_CURSOR = {"_restart_from_head": True}


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
        state.lease_token = token
        state.lease_expires_at = now + timedelta(seconds=settings.ingestion_scan_lease_seconds)
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
                if not state.initial_sync_completed:
                    await _run_initial_scan(session_factory, claim, run_id, source, state, adapter)
                # A degraded provider fallback (notably YouTube Atom) must not
                # orphan an authoritative continuation.  Health and backlog
                # are independent concerns even though ``phase`` is a single
                # display field, so durable continuation always wins here.
                elif state.continuation:
                    await _run_catch_up(session_factory, claim, run_id, source, state, adapter)
                else:
                    await _run_head_scan(session_factory, claim, run_id, source, state, adapter)
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
            .values(
                status=SyncRunStatus.PARTIAL,
                finished_at=now,
                error_code="lease_recovered",
                error_message="A previous scanner lease expired before completion.",
            )
        )
        run = SourceSyncRun(
            source_id=source.id,
            status=SyncRunStatus.RUNNING,
            metrics=_empty_metrics(),
        )
        session.add(run)
        state.last_attempt_at = now
        await session.commit()
        # Detach plain source/state snapshots before upstream I/O.
        return source, state, run.id


async def _run_initial_scan(
    session_factory,
    claim: SourceClaim,
    run_id: UUID,
    source: FeedSource,
    state: SourceSyncState,
    adapter: SourceAdapter,
) -> None:
    settings = get_settings()
    initial_raw = (
        15
        if source.kind in {SourceKind.YOUTUBE, SourceKind.X}
        else settings.ingestion_raw_items_per_sync
    )
    page = await adapter.scan_page(
        SourceScanRequest(
            checkpoint={},
            initial=True,
            conditional=False,
            max_raw_items=initial_raw,
            max_response_bytes=settings.ingestion_max_response_bytes,
        )
    )

    def mutate(
        durable_state: SourceSyncState,
        durable_source: FeedSource,
        run: SourceSyncRun,
        outcome: CandidateUpsertResult,
        now: datetime,
    ) -> None:
        durable_state.initial_sync_completed = True
        durable_state.committed_checkpoint = _with_unchanged_streak(
            page.checkpoint, changed=outcome.changed, previous={}
        )
        durable_state.pending_checkpoint = {}
        durable_state.continuation = None
        durable_state.provider_mode = page.provider_mode
        durable_state.last_complete_at = now if page.authoritative else None
        durable_state.phase = (
            SyncPhase.DEGRADED
            if page.warning_code or page.gap_detected or not page.authoritative
            else SyncPhase.IDLE
        )
        _apply_page_health(durable_state, page)
        durable_state.next_scan_at = _next_normal_scan(durable_source, durable_state, now)
        _release_lease(durable_state)
        durable_source.status = SourceStatus.ACTIVE
        _finish_run(
            run,
            page,
            now,
            partial=bool(page.warning_code or page.gap_detected or page.limit_reached),
        )

    await _persist_page(
        session_factory,
        claim,
        run_id,
        page,
        candidate_items=page.items[:10],
        max_changes=min(10, settings.ingestion_max_items_per_sync),
        baseline_checkpoint={},
        mutate=mutate,
    )


async def _run_head_scan(
    session_factory,
    claim: SourceClaim,
    run_id: UUID,
    source: FeedSource,
    state: SourceSyncState,
    adapter: SourceAdapter,
) -> None:
    settings = get_settings()
    committed = dict(state.committed_checkpoint)
    head_checkpoint: dict[str, Any] | None = None
    cursor: dict[str, Any] | None = None
    changed = 0
    raw_items = 0
    pages = 0
    while pages < settings.ingestion_max_pages_per_sync:
        raw_remaining = settings.ingestion_raw_items_per_sync - raw_items
        if raw_remaining <= 0:
            break
        page = await adapter.scan_page(
            SourceScanRequest(
                checkpoint=committed,
                continuation=cursor,
                initial=False,
                conditional=cursor is None and state.phase == SyncPhase.IDLE,
                max_raw_items=raw_remaining,
                max_response_bytes=settings.ingestion_max_response_bytes,
            )
        )
        pages += 1
        raw_items += page.raw_items
        if head_checkpoint is None:
            head_checkpoint = page.checkpoint
        candidate_remaining = settings.ingestion_max_items_per_sync - changed
        terminal: dict[str, bool] = {"done": False, "stop": False}

        def mutate(
            durable_state: SourceSyncState,
            durable_source: FeedSource,
            run: SourceSyncRun,
            outcome: CandidateUpsertResult,
            now: datetime,
        ) -> None:
            nonlocal changed
            changed += outcome.changed
            coverage_complete = page.completed and not outcome.cap_reached
            if not page.authoritative:
                durable_state.committed_checkpoint = _merge_fallback_checkpoint(
                    durable_state.committed_checkpoint, page.checkpoint
                )
                durable_state.provider_mode = page.provider_mode
                durable_state.phase = SyncPhase.DEGRADED
                _apply_page_health(durable_state, page)
                durable_state.next_scan_at = _next_normal_scan(durable_source, durable_state, now)
                _release_lease(durable_state)
                _finish_run(run, page, now, partial=True)
                terminal.update(done=True, stop=True)
                return
            if coverage_complete:
                final_checkpoint = _with_unchanged_streak(
                    head_checkpoint or page.checkpoint,
                    changed=changed,
                    previous=durable_state.committed_checkpoint,
                )
                durable_state.committed_checkpoint = final_checkpoint
                durable_state.pending_checkpoint = {}
                durable_state.continuation = None
                durable_state.last_complete_at = now
                durable_state.phase = (
                    SyncPhase.DEGRADED if page.gap_detected or page.warning_code else SyncPhase.IDLE
                )
                durable_state.provider_mode = page.provider_mode
                durable_state.consecutive_failures = 0
                durable_state.consecutive_limit_runs = (
                    durable_state.consecutive_limit_runs + 1 if page.limit_reached else 0
                )
                _apply_page_health(durable_state, page)
                durable_state.next_scan_at = _next_normal_scan(durable_source, durable_state, now)
                _release_lease(durable_state)
                _finish_run(
                    run,
                    page,
                    now,
                    partial=bool(page.gap_detected or page.warning_code or page.limit_reached),
                )
                terminal.update(done=True, stop=True)
                return

            durable_state.pending_checkpoint = head_checkpoint or page.checkpoint
            durable_state.phase = SyncPhase.CATCHING_UP
            durable_state.provider_mode = page.provider_mode
            next_cursor = _cursor_for_incomplete_page(cursor, page, outcome)
            durable_state.continuation = {
                "latest_checkpoint": head_checkpoint or page.checkpoint,
                "tasks": [
                    {
                        "lane": "backlog",
                        "cursor": next_cursor,
                        "checkpoint": committed,
                    }
                ],
            }
            _apply_page_health(durable_state, page)
            durable_state.next_scan_at = _next_normal_scan(durable_source, durable_state, now)
            terminal["stop"] = (
                outcome.cap_reached or changed >= settings.ingestion_max_items_per_sync
            )

        outcome = await _persist_page(
            session_factory,
            claim,
            run_id,
            page,
            candidate_items=page.items,
            max_changes=max(candidate_remaining, 0),
            baseline_checkpoint=committed,
            mutate=mutate,
        )
        if terminal["done"]:
            return
        if terminal["stop"] or outcome.cap_reached:
            await _finish_incomplete_run(
                session_factory,
                claim,
                run_id,
                limit_reached="candidates",
            )
            return
        if page.completed:
            return
        cursor = page.next_continuation
        if cursor is None:
            break
    await _finish_incomplete_run(
        session_factory,
        claim,
        run_id,
        limit_reached=(
            "pages"
            if pages >= settings.ingestion_max_pages_per_sync
            else "raw_items"
            if raw_items >= settings.ingestion_raw_items_per_sync
            else "provider_continuation"
        ),
    )


async def _run_catch_up(
    session_factory,
    claim: SourceClaim,
    run_id: UUID,
    source: FeedSource,
    state: SourceSyncState,
    adapter: SourceAdapter,
) -> None:
    settings = get_settings()
    context = dict(state.continuation or {})
    latest_checkpoint = dict(
        context.get("latest_checkpoint")
        if isinstance(context.get("latest_checkpoint"), dict)
        else state.pending_checkpoint or state.committed_checkpoint
    )
    existing_tasks = [dict(task) for task in context.get("tasks", []) if isinstance(task, dict)]
    tasks: list[dict[str, Any]] = [
        {
            "lane": "head",
            "cursor": _RESTART_CURSOR,
            "checkpoint": latest_checkpoint,
            "fresh": True,
        },
        *existing_tasks,
    ]
    changed = 0
    raw_items = 0
    pages = 0
    while tasks and pages < settings.ingestion_max_pages_per_sync:
        task = tasks[0]
        cursor = _provider_cursor(task.get("cursor"))
        target_checkpoint = (
            dict(task["checkpoint"]) if isinstance(task.get("checkpoint"), dict) else {}
        )
        raw_remaining = settings.ingestion_raw_items_per_sync - raw_items
        if raw_remaining <= 0:
            break
        page = await adapter.scan_page(
            SourceScanRequest(
                checkpoint=target_checkpoint,
                continuation=cursor,
                initial=False,
                conditional=False,
                max_raw_items=raw_remaining,
                max_response_bytes=settings.ingestion_max_response_bytes,
            )
        )
        pages += 1
        raw_items += page.raw_items
        candidate_remaining = settings.ingestion_max_items_per_sync - changed
        terminal: dict[str, bool] = {"done": False, "stop": False}
        was_fresh_first_page = bool(task.get("fresh")) and cursor is None

        def mutate(
            durable_state: SourceSyncState,
            durable_source: FeedSource,
            run: SourceSyncRun,
            outcome: CandidateUpsertResult,
            now: datetime,
        ) -> None:
            nonlocal changed, latest_checkpoint, tasks
            changed += outcome.changed
            if not page.authoritative:
                durable_state.committed_checkpoint = _merge_fallback_checkpoint(
                    durable_state.committed_checkpoint, page.checkpoint
                )
                durable_state.phase = SyncPhase.DEGRADED
                durable_state.provider_mode = page.provider_mode
                _apply_page_health(durable_state, page)
                durable_state.next_scan_at = _next_normal_scan(durable_source, durable_state, now)
                _release_lease(durable_state)
                _finish_run(run, page, now, partial=True)
                terminal.update(done=True, stop=True)
                return

            if was_fresh_first_page:
                latest_checkpoint = page.checkpoint
            if outcome.cap_reached:
                task["cursor"] = task.get("cursor") or _RESTART_CURSOR
                task.pop("fresh", None)
                terminal["stop"] = True
            elif page.completed:
                tasks = tasks[1:]
            else:
                task["cursor"] = page.next_continuation or _RESTART_CURSOR
                task.pop("fresh", None)

            if not tasks:
                # A non-authoritative fallback may have refreshed its nested
                # Atom checkpoint while this authoritative backlog was paused.
                # Preserve that independent progress when committing the new
                # authoritative head checkpoint.
                final_checkpoint = _merge_fallback_checkpoint(
                    latest_checkpoint,
                    durable_state.committed_checkpoint,
                )
                durable_state.committed_checkpoint = _with_unchanged_streak(
                    final_checkpoint,
                    changed=changed,
                    previous=durable_state.committed_checkpoint,
                )
                durable_state.pending_checkpoint = {}
                durable_state.continuation = None
                durable_state.last_complete_at = now
                durable_state.phase = (
                    SyncPhase.DEGRADED if page.gap_detected or page.warning_code else SyncPhase.IDLE
                )
                durable_state.provider_mode = page.provider_mode
                durable_state.consecutive_failures = 0
                durable_state.consecutive_limit_runs = (
                    durable_state.consecutive_limit_runs + 1 if page.limit_reached else 0
                )
                _apply_page_health(durable_state, page)
                durable_state.next_scan_at = _next_normal_scan(durable_source, durable_state, now)
                _release_lease(durable_state)
                _finish_run(
                    run,
                    page,
                    now,
                    partial=bool(page.gap_detected or page.warning_code or page.limit_reached),
                )
                terminal.update(done=True, stop=True)
                return

            durable_state.pending_checkpoint = state.pending_checkpoint or latest_checkpoint
            durable_state.continuation = {
                "latest_checkpoint": latest_checkpoint,
                "tasks": [_serializable_task(value) for value in tasks],
            }
            durable_state.phase = SyncPhase.CATCHING_UP
            durable_state.provider_mode = page.provider_mode
            _apply_page_health(durable_state, page)
            durable_state.next_scan_at = _next_normal_scan(durable_source, durable_state, now)
            if changed >= settings.ingestion_max_items_per_sync:
                terminal["stop"] = True

        outcome = await _persist_page(
            session_factory,
            claim,
            run_id,
            page,
            candidate_items=page.items,
            max_changes=max(candidate_remaining, 0),
            baseline_checkpoint=target_checkpoint,
            mutate=mutate,
        )
        if terminal["done"]:
            return
        if terminal["stop"] or outcome.cap_reached:
            await _finish_incomplete_run(
                session_factory,
                claim,
                run_id,
                limit_reached="candidates",
            )
            return
    await _finish_incomplete_run(
        session_factory,
        claim,
        run_id,
        limit_reached=(
            "pages"
            if pages >= settings.ingestion_max_pages_per_sync
            else "raw_items"
            if raw_items >= settings.ingestion_raw_items_per_sync
            else "provider_continuation"
        ),
    )


async def _persist_page(
    session_factory,
    claim: SourceClaim,
    run_id: UUID,
    page: SourceScanPage,
    *,
    candidate_items,
    max_changes: int,
    baseline_checkpoint: dict[str, Any],
    mutate: Callable[
        [SourceSyncState, FeedSource, SourceSyncRun, CandidateUpsertResult, datetime], None
    ],
) -> CandidateUpsertResult:
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
            items=tuple(candidate_items),
            observed_at=now,
            max_changes=max_changes,
            baseline_hashes=_checkpoint_hashes(baseline_checkpoint),
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
        metrics = dict(run.metrics or {})
        metrics["pages"] = int(metrics.get("pages", 0)) + 1
        metrics["raw_items"] = int(metrics.get("raw_items", 0)) + page.raw_items
        metrics["candidates_changed"] = int(metrics.get("candidates_changed", 0)) + outcome.changed
        metrics["duplicates"] = int(metrics.get("duplicates", 0)) + outcome.duplicates
        metrics["normalized_items"] = int(metrics.get("normalized_items", 0)) + len(page.items)
        if page.limit_reached:
            limits = list(metrics.get("limits", []))
            if page.limit_reached not in limits:
                limits.append(page.limit_reached)
            metrics["limits"] = limits
        run.metrics = metrics
        run.provider_mode = page.provider_mode
        state.last_attempt_at = now
        mutate(state, source, run, outcome, now)
        if page.provider_mode == "web_rss":
            state.web_rule_structural_failures = 0
        if (
            source.kind == SourceKind.WEB
            and page.web_listing_evidence.get("is_head")
            and not (run.metrics or {}).get("web_head_observed")
        ):
            await _record_web_structure_health(
                session,
                source,
                state,
                code=page.warning_code,
                evidence=page.web_listing_evidence,
                now=now,
            )
            metrics = dict(run.metrics or {})
            metrics["web_listing"] = page.web_listing_evidence
            metrics["web_head_observed"] = True
            run.metrics = metrics
        await session.commit()
        return outcome


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
        state.phase = SyncPhase.CATCHING_UP
        state.consecutive_limit_runs += 1
        state.next_scan_at = _next_normal_scan(durable_source, state, now)
        _release_lease(state)
        run.status = SyncRunStatus.PARTIAL
        run.finished_at = now
        metrics = dict(run.metrics or {})
        metrics["coverage_complete"] = False
        limits = list(metrics.get("limits", []))
        if limit_reached not in limits:
            limits.append(limit_reached)
        metrics["limits"] = limits
        run.metrics = metrics
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
        state.last_error_code = exc.code
        state.last_error_message = str(exc)[:2000]
        if exc.code == "web_rule_required":
            state.phase = SyncPhase.DEGRADED
            state.next_scan_at = None
            status = SyncRunStatus.FAILED
        elif exc.code == "cursor_invalid":
            state.phase = SyncPhase.CATCHING_UP
            state.continuation = {
                "latest_checkpoint": state.pending_checkpoint or state.committed_checkpoint,
                "tasks": [
                    {
                        "lane": "backlog",
                        "cursor": _RESTART_CURSOR,
                        "checkpoint": state.committed_checkpoint,
                    }
                ],
            }
            state.next_scan_at = _next_normal_scan(source, state, now)
            status = SyncRunStatus.PARTIAL
        else:
            state.phase = SyncPhase.DEGRADED
            state.consecutive_failures += 1
            delay = _retry_delay(exc, state.consecutive_failures)
            state.next_scan_at = now + delay
            status = SyncRunStatus.FAILED
        _release_lease(state)
        if run is not None:
            run.status = status
            run.finished_at = now
            run.error_code = exc.code
            run.error_message = str(exc)[:2000]
            if exc.evidence:
                run.metrics = {**(run.metrics or {}), "web_listing": exc.evidence}
        if source.kind == SourceKind.WEB and not (
            exc.evidence.get("is_head")
            and run is not None
            and (run.metrics or {}).get("web_head_observed")
        ):
            await _record_web_structure_health(
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


async def _record_web_structure_health(
    session,
    source: FeedSource,
    state: SourceSyncState,
    *,
    code: str | None,
    evidence: dict[str, Any],
    now: datetime,
) -> None:
    """The caller owns source→state locks and commits this evidence with its scan."""
    if configured_web_feed_url(source.canonical_url, source.config):
        structural = code in {"web_rss_http_404", "web_rss_http_410", "invalid_feed"} and (
            evidence.get("feed_head") is True
        )
        state.web_rule_structural_failures = (
            (state.web_rule_structural_failures or 0) + 1 if structural else 0
        )
        if structural:
            # A retired feed should be rechecked promptly instead of the normal
            # day-long retry for a permanent provider HTTP error.
            state.next_scan_at = now + timedelta(minutes=2)
        if state.web_rule_structural_failures >= 2:
            record = dict(source.config["web_feed_discovery"])
            record.update(status="retry", invalidated_at=now.isoformat(), failure_code=code)
            source.config = {
                **source.config,
                "web_feed_discovery": record,
                "web_rule_revision": int(source.config.get("web_rule_revision") or 0) + 1,
            }
            state.committed_checkpoint = {
                key: value
                for key, value in state.committed_checkpoint.items()
                if key in {"head_ids", "item_hashes"}
            }
            state.pending_checkpoint, state.continuation = {}, None
            state.web_rule_structural_failures = 0
            state.last_error_code = "web_feed_discovery_retry"
            state.last_error_message = "The selected feed changed; checking the website again."
        return
    structural = (
        code in {"web_structure_changed", "web_rule_missing_fields", "web_rule_action_failed"}
        and evidence.get("is_head") is True
        and not (code == "web_rule_missing_fields" and evidence.get("valid_pairs", 0) > 0)
    )
    if structural:
        state.web_rule_structural_failures = (state.web_rule_structural_failures or 0) + 1
        if state.web_rule_failure_episode_id is None:
            state.web_rule_failure_episode_id = uuid4()
        if state.web_rule_structural_failures >= 2:
            await ensure_web_rule_job(session, source=source, state=state, reason="repair", now=now)
    else:
        state.web_rule_structural_failures = 0
        if evidence.get("is_head") and evidence.get("valid_pairs", 0) > 0:
            state.web_rule_failure_episode_id = None
        if code == "web_rule_required":
            await ensure_web_rule_job(session, source=source, state=state, reason="author", now=now)


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


def _empty_metrics() -> dict[str, Any]:
    return {
        "pages": 0,
        "raw_items": 0,
        "normalized_items": 0,
        "candidates_changed": 0,
        "duplicates": 0,
        "limits": [],
    }


def _finish_run(run: SourceSyncRun, page: SourceScanPage, now: datetime, *, partial: bool) -> None:
    run.status = SyncRunStatus.PARTIAL if partial else SyncRunStatus.SUCCESS
    run.finished_at = now
    run.error_code = page.warning_code
    run.error_message = page.warning_message
    metrics = dict(run.metrics or {})
    metrics["coverage_complete"] = bool(
        page.authoritative and page.completed and not page.gap_detected
    )
    metrics["gap_detected"] = page.gap_detected
    run.metrics = metrics


def _apply_page_health(state: SourceSyncState, page: SourceScanPage) -> None:
    state.gap_detected = page.gap_detected
    state.gap_details = page.gap_details
    state.last_error_code = page.warning_code
    state.last_error_message = page.warning_message
    if not page.warning_code:
        state.consecutive_failures = 0


def _release_lease(state: SourceSyncState) -> None:
    state.lease_token = None
    state.lease_expires_at = None


def _cursor_for_incomplete_page(
    input_cursor: dict[str, Any] | None,
    page: SourceScanPage,
    outcome: CandidateUpsertResult,
) -> dict[str, Any]:
    if outcome.cap_reached:
        return input_cursor or _RESTART_CURSOR
    return page.next_continuation or input_cursor or _RESTART_CURSOR


def _provider_cursor(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("_restart_from_head"):
        return None
    return dict(value)


def _serializable_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "lane": str(task.get("lane") or "backlog"),
        "cursor": task.get("cursor") or _RESTART_CURSOR,
        "checkpoint": task.get("checkpoint") if isinstance(task.get("checkpoint"), dict) else {},
    }


def _checkpoint_hashes(checkpoint: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for key, value in checkpoint.items():
        if key == "item_hashes" and isinstance(value, dict):
            hashes.update(
                {str(native_id): str(item_hash) for native_id, item_hash in value.items()}
            )
        elif isinstance(value, dict):
            hashes.update(_checkpoint_hashes(value))
    return hashes


def _with_unchanged_streak(
    checkpoint: dict[str, Any], *, changed: int, previous: dict[str, Any]
) -> dict[str, Any]:
    result = dict(checkpoint)
    previous_streak = int(previous.get("unchanged_runs") or 0)
    result["unchanged_runs"] = previous_streak + 1 if changed == 0 else 0
    result["last_change_count"] = changed
    return result


def _merge_fallback_checkpoint(
    committed: dict[str, Any], fallback: dict[str, Any]
) -> dict[str, Any]:
    result = dict(committed)
    for key, value in fallback.items():
        if key == "atom" or key not in result:
            result[key] = value
    return result


def _normal_interval_minutes(source: FeedSource, state: SourceSyncState) -> int:
    if source.kind == SourceKind.RSS or (
        source.kind == SourceKind.WEB
        and configured_web_feed_url(
            getattr(source, "canonical_url", ""), getattr(source, "config", {})
        )
    ):
        target, minimum, maximum = 60, 30, 120
    elif source.kind == SourceKind.YOUTUBE:
        target, minimum, maximum = 60, 60, 120
    else:
        target, minimum, maximum = 120, 60, 240
    streak = int(state.committed_checkpoint.get("unchanged_runs") or 0)
    if state.phase == SyncPhase.CATCHING_UP:
        return target
    if streak >= 3:
        return maximum
    recent_changes = int(state.committed_checkpoint.get("last_change_count") or 0)
    if recent_changes >= 10:
        return minimum
    if recent_changes >= 3:
        return max(minimum, (minimum + target) // 2)
    return min(max(target, minimum), maximum)


def _next_normal_scan(source: FeedSource, state: SourceSyncState, now: datetime) -> datetime:
    minutes = _normal_interval_minutes(source, state)
    return now + timedelta(minutes=minutes * random.uniform(0.9, 1.1))


def _retry_delay(exc: SourceScanError, failures: int) -> timedelta:
    if exc.retry_after_seconds is not None:
        return timedelta(seconds=max(exc.retry_after_seconds, 60))
    if exc.long_lived:
        return timedelta(hours=24)
    minutes = (2, 10, 30, 120, 360)[min(max(failures - 1, 0), 4)]
    return timedelta(minutes=minutes * random.uniform(0.9, 1.1))


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
