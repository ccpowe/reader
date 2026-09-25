"""Source health projections for subscription reads."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.domain.enums import SourceKind, SourceStatus, SyncPhase
from app.ingestion.web_feed import configured_web_feed_url, web_feed_probe_required
from app.services.subscriptions import web_source_needs_rule
from app.storage.models import FeedSource, SourceSyncState, WebRuleAgentRuntime, WebRuleJob
from app.translation.engines import engine_descriptor
from app.web_rule_agent.configuration import rule_agent_snapshot


async def web_rule_health_projections(
    session: AsyncSession, sources: list[tuple[FeedSource, SourceSyncState]]
) -> dict[UUID, tuple[SyncPhase, str | None]]:
    """Load only current input/episode jobs once, without changing scan evidence."""
    feed_sources = {
        source.id: (source, state)
        for source, state in sources
        if source.kind == SourceKind.WEB
        and source.status != SourceStatus.PAUSED
        and (
            configured_web_feed_url(source.canonical_url, source.config)
            or web_feed_probe_required(source.canonical_url, source.config)
        )
    }
    projections = {
        source_id: project_web_rule_health(source, state, None, agent_unavailable=False)
        for source_id, (source, state) in feed_sources.items()
    }
    waiting = [
        (source, state)
        for source, state in sources
        if source.kind == SourceKind.WEB
        and source.status != SourceStatus.PAUSED
        and source.id not in feed_sources
        and (web_source_needs_rule(source) or state.web_rule_failure_episode_id is not None)
    ]
    if not waiting:
        return projections
    conditions = []
    for source, state in waiting:
        missing = web_source_needs_rule(source)
        conditions.append(
            and_(
                WebRuleJob.source_id == source.id,
                WebRuleJob.base_rule_revision
                == int((source.config or {}).get("web_rule_revision") or 0),
                WebRuleJob.reason == ("author" if missing else "repair"),
                WebRuleJob.failure_episode_id.is_(None)
                if missing
                else (WebRuleJob.failure_episode_id == state.web_rule_failure_episode_id),
            )
        )
    jobs = list(
        await session.scalars(
            select(WebRuleJob)
            .where(or_(*conditions))
            .distinct(WebRuleJob.source_id)
            .order_by(WebRuleJob.source_id, WebRuleJob.created_at.desc(), WebRuleJob.id.desc())
        )
    )
    by_source = {job.source_id: job for job in jobs}
    runtime = await session.get(WebRuleAgentRuntime, "default")
    settings = get_settings()
    engine_id = settings.web_rule_agent_engine_id
    descriptor = engine_descriptor(settings, engine_id) if engine_id != "disabled" else None
    unavailable = (
        descriptor is None
        or not descriptor.available
        or bool(
            runtime is not None
            and runtime.paused_code
            and (runtime.resume_at is None or runtime.resume_at > datetime.now(UTC))
            and runtime.engine_snapshot == rule_agent_snapshot(settings, descriptor)
        )
    )
    projections.update(
        {
            source.id: project_web_rule_health(
                source,
                state,
                by_source.get(source.id),
                agent_unavailable=unavailable,
            )
            for source, state in waiting
        }
    )
    return projections


def project_web_rule_health(
    source: FeedSource,
    state: SourceSyncState,
    job: WebRuleJob | None,
    *,
    agent_unavailable: bool,
) -> tuple[SyncPhase, str | None]:
    baseline = (state.phase, state.last_error_code)
    if source.status == SourceStatus.PAUSED or source.kind != SourceKind.WEB:
        return baseline
    if configured_web_feed_url(source.canonical_url, source.config):
        if state.last_error_code == "web_feed_discovering" or str(
            state.last_error_code or ""
        ).startswith("web_rule_"):
            return (
                SyncPhase.IDLE if state.phase == SyncPhase.DEGRADED else state.phase,
                None,
            )
        return baseline
    if web_feed_probe_required(source.canonical_url, source.config):
        if state.last_error_code is None or str(state.last_error_code).startswith("web_rule_"):
            return state.phase, "web_feed_discovering"
        return baseline
    missing = web_source_needs_rule(source)
    episode = state.web_rule_failure_episode_id
    if not missing and episode is None:
        return baseline
    # Keep the pure projection fenced too: callers cannot project a historical
    # task merely because it was once the most recent job for this source.
    if job is not None and (
        job.base_rule_revision != int((source.config or {}).get("web_rule_revision") or 0)
        or job.reason != ("author" if missing else "repair")
        or job.failure_episode_id != (None if missing else episode)
    ):
        job = None
    access_error_codes = {
        "web_http_401",
        "web_http_403",
        "web_challenge_required",
        "robots_disallowed",
    }
    if state.last_error_code in access_error_codes:
        # A later scan can be denied while a previous structural repair episode
        # is still open. Its latest access failure takes priority over repair UI.
        return baseline
    if (
        job is not None
        and job.status == "failed"
        and getattr(job, "last_error_code", None) in access_error_codes
    ):
        # An unavailable authoring engine cannot explain or repair an upstream
        # access restriction. Keep the actual current task's cause visible.
        return SyncPhase.DEGRADED, job.last_error_code
    if agent_unavailable:
        return SyncPhase.DEGRADED, "web_rule_agent_unavailable"
    if job is None:
        return (SyncPhase.DEGRADED, "web_rule_authoring") if missing else baseline
    if job.status in {"queued", "running", "retry_wait"}:
        return SyncPhase.DEGRADED, "web_rule_authoring" if missing else "web_rule_repairing"
    if job.status == "blocked":
        return SyncPhase.DEGRADED, "web_rule_agent_unavailable"
    if job.status == "failed":
        return SyncPhase.DEGRADED, "web_rule_authoring_failed"
    return baseline
