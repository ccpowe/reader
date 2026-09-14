"""Schedule and persist model-free feed discovery under the source scanner lease."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select

from app.domain.enums import SourceKind, SourceStatus, SyncPhase, SyncRunStatus
from app.ingestion.feed_discovery import FeedDiscoveryResult
from app.ingestion.web_feed import WEB_FEED_DISCOVERY_VERSION, web_feed_probe_required
from app.services.web_rule_jobs import cancel_web_rule_jobs_for_feed
from app.storage.models import FeedSource, SourceSubscription, SourceSyncRun, SourceSyncState


async def schedule_web_feed_probes(session, *, limit: int = 50, now=None) -> int:
    """Wake old missing-rule sources, even with authoring disabled or terminal jobs."""
    now = now or datetime.now(UTC)
    # Keep the negative cache for completed probes, while waking the historical
    # bug's one-request 403 records. No discovery-version bump or author budget
    # reset is needed; saving the next result naturally removes this exception.
    legacy_blocked = FeedSource.config["web_feed_discovery"]["evidence"].contains(
        {"requests": 1, "attempts": [{"origin": "source", "status_code": 403}]}
    )
    no_feed = and_(
        FeedSource.config["web_feed_discovery"].contains(
            {
                "version": WEB_FEED_DISCOVERY_VERSION,
                "status": "not_found",
            }
        ),
        FeedSource.config["web_feed_discovery"]["source_url"].astext == FeedSource.canonical_url,
        ~func.coalesce(legacy_blocked, False),
    )
    sources = list(
        await session.scalars(
            select(FeedSource)
            .join(SourceSyncState, SourceSyncState.source_id == FeedSource.id)
            .where(
                FeedSource.kind == SourceKind.WEB,
                FeedSource.status != SourceStatus.PAUSED,
                SourceSyncState.next_scan_at.is_(None),
                or_(
                    SourceSyncState.lease_expires_at.is_(None),
                    SourceSyncState.lease_expires_at < now,
                ),
                or_(
                    FeedSource.config["web_rule"].astext.is_(None),
                    SourceSyncState.last_error_code == "web_rule_required",
                ),
                ~func.coalesce(no_feed, False),
                exists().where(
                    SourceSubscription.source_id == FeedSource.id,
                    SourceSubscription.is_enabled.is_(True),
                ),
            )
            .order_by(FeedSource.id)
            .limit(limit)
            .with_for_update(of=FeedSource, skip_locked=True)
        )
    )
    count = 0
    for source in sources:
        state = await session.get(
            SourceSyncState, source.id, with_for_update=True, populate_existing=True
        )
        if state is None or state.next_scan_at is not None:
            continue
        if state.lease_expires_at and state.lease_expires_at >= now:
            continue
        state.next_scan_at = now
        if web_feed_probe_required(source.canonical_url, source.config):
            state.phase = SyncPhase.IDLE
            state.last_error_code = "web_feed_discovering"
            state.last_error_message = "Checking the webpage for RSS/Atom."
        count += 1
    return count


async def save_web_feed_discovery(
    session_factory,
    *,
    source_id: UUID,
    lease_token: UUID,
    run_id: UUID,
    source_url: str,
    rule_revision: int,
    result: FeedDiscoveryResult,
) -> tuple[FeedSource, SourceSyncState] | None:
    """Accept only the current scanner's result; preserve source/content identity."""
    async with session_factory() as session:
        source = await session.get(FeedSource, source_id, with_for_update=True)
        state = await session.get(SourceSyncState, source_id, with_for_update=True)
        run = await session.get(SourceSyncRun, run_id)
        now = datetime.now(UTC)
        if (
            source is None
            or state is None
            or run is None
            or source.kind != SourceKind.WEB
            or state.lease_token != lease_token
            or state.lease_expires_at is None
            or state.lease_expires_at <= now
            or source.canonical_url != source_url
            or int((source.config or {}).get("web_rule_revision") or 0) != rule_revision
            or not web_feed_probe_required(source_url, source.config)
        ):
            return None
        if source.status == SourceStatus.PAUSED or not await session.scalar(
            select(SourceSubscription.id)
            .where(
                SourceSubscription.source_id == source_id, SourceSubscription.is_enabled.is_(True)
            )
            .limit(1)
        ):
            state.lease_token = state.lease_expires_at = None
            run.status, run.finished_at = SyncRunStatus.PARTIAL, now
            run.error_code = "source_inactive"
            await session.commit()
            return None
        record = {
            "version": WEB_FEED_DISCOVERY_VERSION,
            "source_url": source_url,
            "status": result.status,
            "feed_url": result.feed_url,
            "checked_at": now.isoformat(),
            "evidence": result.evidence,
        }
        source.config = {**(source.config or {}), "web_feed_discovery": record}
        run.metrics = {**(run.metrics or {}), "feed_discovery": record}
        if result.status == "found":
            # Cursors/validators belong to the old endpoint. URL identities are
            # shared with Web extraction, so retain article anchors to detect a
            # gap if the new feed cannot reach previously observed history.
            source.config = {**source.config, "web_rule_revision": rule_revision + 1}
            state.committed_checkpoint = {
                key: value
                for key, value in state.committed_checkpoint.items()
                if key in {"head_ids", "item_hashes"}
            }
            state.pending_checkpoint, state.continuation = {}, None
            state.web_rule_structural_failures = 0
            state.web_rule_failure_episode_id = None
            state.provider_mode = "web_rss"
            state.consecutive_failures = state.consecutive_limit_runs = 0
            state.last_error_code = state.last_error_message = None
            state.gap_detected = False
            state.phase = SyncPhase.IDLE
            await cancel_web_rule_jobs_for_feed(session, source_id, now=now)
        await session.commit()
        return source, state
