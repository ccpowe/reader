"""Durable, source-fenced rule authoring. Every API uses a caller-owned transaction.

Lock order is runtime (only claims/heartbeat), source, sync state, job. Never keep
these transactions open while invoking a model or crawling. The runtime slot is
released separately after a terminal job transaction commits.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import SourceKind, SourceStatus
from app.ingestion.web_feed import configured_web_feed_url, web_feed_probe_required
from app.ingestion.web_rules import WebRuleError, bounded_execution_report, parse_web_rule
from app.services.web_rules import _activate_validated_rule, rule_version
from app.storage.models import (
    FeedSource,
    SourceSubscription,
    SourceSyncState,
    WebRuleAgentRuntime,
    WebRuleJob,
)

ACTIVE_STATUSES = ("queued", "running", "retry_wait")
TERMINAL_STATUSES = ("succeeded", "failed", "blocked", "cancelled")


class JobGuardError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class JobLimits:
    total_tokens: int = 3_000_000
    model_calls: int = 80
    lease_seconds: int = 120
    validation_ttl_seconds: int = 300
    diagnostic_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if any(value < 1 for value in asdict(self).values()):
            raise ValueError("All rule job limits must be positive")


@dataclass(frozen=True)
class WebRuleJobClaim:
    job_id: UUID
    source_id: UUID
    source_url: str
    lease_token: UUID
    deadline_at: datetime | None
    stage: str
    candidate_rule: dict | None
    base_rule: dict | None
    base_rule_revision: int
    engine_snapshot: dict
    prompt_version: str
    rule_schema_version: str
    validator_version: str
    budget_snapshot: dict
    reason: str
    trigger_evidence: dict
    last_feedback: dict | None
    authoring_checkpoint: dict | None = None


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(UTC)


def source_rule_revision(source: FeedSource) -> int:
    value = (source.config or {}).get("web_rule_revision", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _hash(rule: object) -> str | None:
    return rule_version(rule) if isinstance(rule, dict) else None


def _bounded(value: Any, maximum: int = 20000) -> Any:
    encoded = json.dumps(value, ensure_ascii=False, default=str).encode()
    if len(encoded) <= maximum:
        return json.loads(encoded)
    return {"truncated": True, "summary": encoded[: maximum - 100].decode(errors="ignore")}


def _diagnostic(job: WebRuleJob, *, phase: str, code: str, evidence: Any, now: datetime) -> None:
    entries = list(job.diagnostics or [])
    if code == "authoring_checkpoint":
        entries = [entry for entry in entries if entry.get("code") != code]
    entries.append(
        {
            "at": now.isoformat(),
            "phase": phase[:32],
            "code": code[:120],
            "evidence": _bounded(
                evidence, min(20000, max(100, (_limits(job).diagnostic_bytes - 500) // 2))
            ),
        }
    )
    while (
        entries
        and len(json.dumps(entries, ensure_ascii=False).encode()) > _limits(job).diagnostic_bytes
    ):
        removable = next(
            (
                index
                for index, entry in enumerate(entries)
                if entry.get("code") != "authoring_checkpoint"
            ),
            None,
        )
        if removable is None:
            # Operational settings allow at least 4096 bytes. An invalid direct
            # caller must not turn an oversized checkpoint into valid state.
            entries = []
            break
        entries.pop(removable)
    job.diagnostics = entries


def _limits(job: WebRuleJob) -> JobLimits:
    # Keep recorded token/model allowances and operational controls from old
    # snapshots, discarding retired tool/Goal/attempt/deadline quotas.
    snapshot = job.limits_snapshot or {}
    return JobLimits(
        **{
            field.name: snapshot[field.name]
            for field in fields(JobLimits)
            if field.name in snapshot
        }
    )


def remaining_model_tokens(job: WebRuleJob) -> int:
    """Actual usage plus unknown prior responses, excluding an in-flight call."""
    pending = sum(
        entry["estimated_tokens"]
        for entry in (job.usage_reservations or {}).values()
        if entry.get("state") == "pending"
    )
    unknown = max(0, job.conservative_tokens_reserved - pending)
    return max(0, _limits(job).total_tokens - job.total_tokens - unknown)


def _terminal(job: WebRuleJob, status: str, code: str, now: datetime, message: str = "") -> None:
    reservations = dict(job.usage_reservations or {})
    for reservation_id, entry in reservations.items():
        if entry.get("state") == "pending":
            reservations[reservation_id] = {**entry, "state": "unknown"}
            job.unknown_usage_count += 1
    job.usage_reservations = reservations
    job.status, job.last_error_code = status, code
    job.last_error_message = message[:2000] or None
    job.finished_at = now
    job.lease_token = None
    job.lease_expires_at = None
    job.validation_receipt = None
    _diagnostic(job, phase=job.stage, code=code, evidence={"message": message[:2000]}, now=now)


async def _source_locked(
    session: AsyncSession, source_id: UUID, *, skip_locked: bool = False
) -> tuple[FeedSource | None, SourceSyncState | None]:
    source = await session.scalar(
        select(FeedSource)
        .where(FeedSource.id == source_id)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    if source is None:
        return None, None
    state = await session.scalar(
        select(SourceSyncState)
        .where(SourceSyncState.source_id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return source, state


async def _eligible(session: AsyncSession, source: FeedSource | None) -> bool:
    if source is None or source.kind != SourceKind.WEB or source.status == SourceStatus.PAUSED:
        return False
    if configured_web_feed_url(source.canonical_url, source.config) or web_feed_probe_required(
        source.canonical_url, source.config
    ):
        # Old claims become source_inactive while discovery owns the source.
        # If discovery finds no feed, the same input/job can resume only with
        # its existing usage counters; terminal failures never reset.
        return False
    return bool(
        await session.scalar(
            select(
                exists().where(
                    SourceSubscription.source_id == source.id,
                    SourceSubscription.is_enabled.is_(True),
                )
            )
        )
    )


def _input_matches(job: WebRuleJob, source: FeedSource, state: SourceSyncState | None) -> bool:
    return (
        job.source_url == source.canonical_url
        and job.base_rule_revision == source_rule_revision(source)
        and job.base_rule_hash == _hash(source.config.get("web_rule"))
        and (
            job.reason != "repair"
            or (state is not None and state.web_rule_failure_episode_id == job.failure_episode_id)
        )
    )


async def ensure_web_rule_job(
    session: AsyncSession,
    *,
    source_id: UUID | None = None,
    source: FeedSource | None = None,
    state: SourceSyncState | None = None,
    reason: str = "author",
    failure_episode_id: UUID | None = None,
    now: datetime | None = None,
) -> WebRuleJob | None:
    """Idempotent automatic dispatch; terminal records never refresh model budget.

    Passing source/state requires the caller already to hold source then state
    locks. Passing only source_id takes those locks here.
    """
    now = _now(now)
    if source is None:
        if source_id is None:
            raise ValueError("source or source_id required")
        source, state = await _source_locked(session, source_id)
    if reason not in {"author", "repair"}:
        raise ValueError("reason must be author or repair")
    if not await _eligible(session, source) or state is None:
        return None
    assert source is not None
    if reason == "author":
        try:
            parse_web_rule(source.config.get("web_rule"), source_url=source.canonical_url)
        except (WebRuleError, ValueError, TypeError):
            pass
        else:
            return None
    else:
        failure_episode_id = failure_episode_id or state.web_rule_failure_episode_id
        if failure_episode_id is None or failure_episode_id != state.web_rule_failure_episode_id:
            return None
    revision = source_rule_revision(source)
    key = f"{source.id}:{revision}:{reason}:{failure_episode_id or 'missing'}"
    existing = await session.scalar(
        select(WebRuleJob).where(WebRuleJob.idempotency_key == key).with_for_update()
    )
    active = await session.scalar(
        select(WebRuleJob)
        .where(WebRuleJob.source_id == source.id, WebRuleJob.status.in_(ACTIVE_STATUSES))
        .with_for_update()
    )
    if active is not None:
        if _input_matches(active, source, state):
            return active
        _terminal(active, "cancelled", "input_changed", now)
        await session.flush()
    if existing is not None:
        if (
            existing.status == "cancelled"
            and existing.last_error_code == "source_inactive"
            and _input_matches(existing, source, state)
        ):
            existing.status, existing.available_at, existing.finished_at = "queued", now, None
            existing.last_error_code, existing.last_error_message = None, None
        return existing
    job = WebRuleJob(
        id=uuid4(),
        source_id=source.id,
        reason=reason,
        idempotency_key=key,
        failure_episode_id=failure_episode_id,
        source_url=source.canonical_url,
        base_rule_revision=revision,
        base_rule_hash=_hash(source.config.get("web_rule")),
        base_rule=source.config.get("web_rule"),
        available_at=now,
    )
    session.add(job)
    await session.flush()
    return job


async def cancel_web_rule_jobs_for_feed(
    session: AsyncSession, source_id: UUID, *, now: datetime | None = None
) -> int:
    """Fence active author/repair jobs after a source selects a validated feed.

    The caller must already hold the source then sync-state locks and persist
    the feed selection/revision in this transaction. Do not take the runtime
    lock here: its slot is reclaimed separately by the worker after cancellation.
    Terminal history and its accumulated usage remain unchanged.
    """
    jobs = list(
        await session.scalars(
            select(WebRuleJob)
            .where(WebRuleJob.source_id == source_id, WebRuleJob.status.in_(ACTIVE_STATUSES))
            .with_for_update()
        )
    )
    now = _now(now)
    for job in jobs:
        _terminal(job, "cancelled", "web_feed_selected", now)
    await session.flush()
    return len(jobs)


async def _runtime_locked(session: AsyncSession) -> WebRuleAgentRuntime:
    await session.execute(
        insert(WebRuleAgentRuntime)
        .values(id="default", engine_snapshot={})
        .on_conflict_do_nothing(index_elements=[WebRuleAgentRuntime.id])
    )
    runtime = await session.scalar(
        select(WebRuleAgentRuntime)
        .where(WebRuleAgentRuntime.id == "default")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert runtime is not None
    return runtime


def _claim(job: WebRuleJob, state: SourceSyncState) -> WebRuleJobClaim:
    assert job.lease_token
    return WebRuleJobClaim(
        job_id=job.id,
        source_id=job.source_id,
        source_url=job.source_url,
        lease_token=job.lease_token,
        deadline_at=job.deadline_at,
        stage=job.stage,
        candidate_rule=job.candidate_rule,
        base_rule=job.base_rule,
        base_rule_revision=job.base_rule_revision,
        engine_snapshot=job.engine_snapshot,
        prompt_version=job.prompt_version or "",
        rule_schema_version=job.rule_schema_version or "",
        validator_version=job.validator_version or "",
        budget_snapshot={
            "limits": job.limits_snapshot,
            "model_calls_reserved": job.model_calls_reserved,
            "tool_calls_reserved": job.tool_calls_reserved,
            "validation_calls_reserved": job.validation_calls_reserved,
            "goal_resumptions_reserved": job.goal_resumptions_reserved,
            "total_tokens": job.total_tokens,
            "conservative_tokens_reserved": job.conservative_tokens_reserved,
            "attempt_count": job.attempt_count,
        },
        reason=job.reason,
        trigger_evidence={
            "code": state.last_error_code,
            "message": state.last_error_message,
            "failure_episode_id": str(job.failure_episode_id) if job.failure_episode_id else None,
        },
        last_feedback=job.validation_report,
        authoring_checkpoint=next(
            (
                entry.get("evidence")
                for entry in reversed(job.diagnostics or [])
                if entry.get("code") == "authoring_checkpoint"
            ),
            None,
        ),
    )


async def claim_web_rule_job(
    session: AsyncSession,
    *,
    engine_snapshot: dict,
    limits: JobLimits,
    prompt_version: str = "web-rule-author-v1",
    rule_schema_version: str = "crawl4ai-native-v1",
    validator_version: str = "web-rule-validator-v1",
    browser_execution_identity: dict | None = None,
    cli_execution_identity: dict | None = None,
    now: datetime | None = None,
) -> WebRuleJobClaim | None:
    # Older internal callers may pass a claim's complete execution snapshot.
    # Provider pause matching remains independent of Worker-local artifacts.
    engine_snapshot = dict(engine_snapshot)
    embedded_identity = engine_snapshot.pop("browser_execution_identity", None)
    if browser_execution_identity is None:
        browser_execution_identity = embedded_identity
    embedded_cli_identity = engine_snapshot.pop("cli_execution_identity", None)
    if cli_execution_identity is None:
        cli_execution_identity = embedded_cli_identity
    now = _now(now)
    runtime = await _runtime_locked(session)
    if runtime.current_job_id and runtime.lease_expires_at and runtime.lease_expires_at > now:
        active_status = await session.scalar(
            select(WebRuleJob.status).where(WebRuleJob.id == runtime.current_job_id)
        )
        if active_status == "running":
            return None
    runtime.current_job_id = runtime.lease_token = runtime.lease_expires_at = None
    if runtime.paused_code and runtime.engine_snapshot == engine_snapshot:
        if runtime.resume_at is None or runtime.resume_at > now:
            return None
        runtime.paused_code = runtime.paused_message = runtime.paused_at = runtime.resume_at = None
    elif runtime.paused_code:
        # A changed explicitly configured engine can serve new jobs; started jobs
        # remain bound to their own snapshot and will be blocked below.
        runtime.paused_code = runtime.paused_message = runtime.paused_at = runtime.resume_at = None
    runtime.engine_snapshot = dict(engine_snapshot)
    # No task lock is taken before the source lock. The singleton serializes
    # claimers; source locks serialize subscription and rule management changes.
    ids = (
        await session.execute(
            select(WebRuleJob.id, WebRuleJob.source_id)
            .where(
                or_(
                    WebRuleJob.status.in_(("queued", "retry_wait")),
                    (WebRuleJob.status == "running") & (WebRuleJob.lease_expires_at <= now),
                ),
                WebRuleJob.available_at <= now,
            )
            .order_by(WebRuleJob.available_at, WebRuleJob.created_at)
            .limit(50)
        )
    ).all()
    for job_id, source_id in ids:
        # Another batch may lock sources in UUID order while jobs are ordered
        # by due time. Never wait on a second source while holding the first.
        source, state = await _source_locked(session, source_id, skip_locked=True)
        if source is None:
            continue
        job = await session.scalar(
            select(WebRuleJob)
            .where(WebRuleJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None or job.status not in ACTIVE_STATUSES:
            continue
        if not await _eligible(session, source) or state is None:
            _terminal(job, "cancelled", "source_inactive", now)
            continue
        assert source is not None
        if not _input_matches(job, source, state):
            _terminal(job, "cancelled", "input_changed", now)
            continue
        execution_snapshot = dict(engine_snapshot)
        if browser_execution_identity is not None:
            execution_snapshot["browser_execution_identity"] = browser_execution_identity
        if cli_execution_identity is not None:
            execution_snapshot["cli_execution_identity"] = cli_execution_identity
        if job.started_at is None:
            job.started_at, job.deadline_at = now, None
            job.engine_snapshot, job.limits_snapshot = execution_snapshot, asdict(limits)
            job.prompt_version, job.rule_schema_version, job.validator_version = (
                prompt_version,
                rule_schema_version,
                validator_version,
            )
        elif (
            job.engine_snapshot != execution_snapshot
            or job.prompt_version != prompt_version
            or job.rule_schema_version != rule_schema_version
            or job.validator_version != validator_version
        ):
            _terminal(job, "blocked", "config_changed", now)
            continue
        # Retired task deadlines never shorten a new lease, including when
        # resuming jobs created by an earlier worker version.
        job.deadline_at = None
        # A lost response remains charged at its conservative reservation. It is
        # explicitly unknown and can never be reused by the next attempt.
        reservations = dict(job.usage_reservations)
        for reservation_id, entry in reservations.items():
            if entry.get("state") == "pending":
                reservations[reservation_id] = {**entry, "state": "unknown"}
                job.unknown_usage_count += 1
        job.usage_reservations = reservations
        job.attempt_count += 1
        job.status, job.lease_token = "running", uuid4()
        job.lease_expires_at = now + timedelta(seconds=_limits(job).lease_seconds)
        job.validation_receipt = None
        runtime.current_job_id, runtime.lease_token, runtime.lease_expires_at = (
            job.id,
            job.lease_token,
            job.lease_expires_at,
        )
        await session.flush()
        return _claim(job, state)
    return None


async def _checked(
    session: AsyncSession, claim: WebRuleJobClaim, now: datetime
) -> tuple[WebRuleJob, FeedSource, SourceSyncState]:
    source, state = await _source_locked(session, claim.source_id)
    job = await session.scalar(
        select(WebRuleJob)
        .where(WebRuleJob.id == claim.job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.status != "running" or job.lease_token != claim.lease_token:
        raise JobGuardError("lease_lost")
    if not job.lease_expires_at or job.lease_expires_at <= now:
        raise JobGuardError("lease_lost")
    if not await _eligible(session, source) or state is None:
        raise JobGuardError("source_inactive")
    assert source is not None
    if not _input_matches(job, source, state):
        raise JobGuardError("input_changed")
    return job, source, state


async def check_web_rule_job(
    session: AsyncSession, *, claim: WebRuleJobClaim, now: datetime | None = None
) -> WebRuleJob:
    job, _, _ = await _checked(session, claim, _now(now))
    return job


async def heartbeat_web_rule_job(
    session: AsyncSession, *, claim: WebRuleJobClaim, now: datetime | None = None
) -> bool:
    now = _now(now)
    runtime = await _runtime_locked(session)
    if runtime.current_job_id != claim.job_id or runtime.lease_token != claim.lease_token:
        return False
    try:
        job, _, _ = await _checked(session, claim, now)
    except JobGuardError as exc:
        if exc.code == "lease_lost":
            return False
        # Preserve business causes so Worker termination and source
        # re-enabling can apply their existing recovery policy.
        raise
    job.lease_expires_at = now + timedelta(seconds=_limits(job).lease_seconds)
    runtime.lease_expires_at = job.lease_expires_at
    return True


async def reserve_model_call(
    session: AsyncSession,
    *,
    claim: WebRuleJobClaim,
    estimated_tokens: int,
    now: datetime | None = None,
) -> str:
    job = await check_web_rule_job(session, claim=claim, now=now)
    if any(entry.get("state") == "pending" for entry in job.usage_reservations.values()):
        raise JobGuardError("model_call_in_flight")
    if job.model_calls_reserved >= _limits(job).model_calls:
        raise JobGuardError("model_budget_exhausted")
    if remaining_model_tokens(job) == 0:
        raise JobGuardError("token_budget_exhausted")
    # This estimate only charges unknown/interrupted usage. It is not a
    # prediction used to reject a request before the provider reports usage.
    estimated_tokens = max(1, estimated_tokens)
    reservation = str(uuid4())
    job.model_calls_reserved += 1
    job.conservative_tokens_reserved += estimated_tokens
    job.usage_reservations = {
        **job.usage_reservations,
        reservation: {
            "state": "pending",
            "estimated_tokens": estimated_tokens,
            "lease_token": str(claim.lease_token),
        },
    }
    return reservation


async def record_model_usage(
    session: AsyncSession,
    *,
    claim: WebRuleJobClaim,
    reservation_id: str,
    usage: dict | None,
    now: datetime | None = None,
) -> None:
    # Usage is a ledger fact: record independently of task token allowance,
    # while the reservation's owning lease is still current. Do not unlock a new
    # lease's budget when a stale request eventually returns.
    job = await session.scalar(
        select(WebRuleJob)
        .where(WebRuleJob.id == claim.job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise JobGuardError("lease_lost")
    entry = job.usage_reservations.get(reservation_id)
    if not entry or entry.get("lease_token") != str(claim.lease_token):
        raise JobGuardError("reservation_invalid")
    if entry.get("state") != "pending":
        return
    if job.lease_token != claim.lease_token:
        raise JobGuardError("lease_lost")
    if (
        usage is None
        or not isinstance(usage, dict)
        or not any(
            isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and usage[key] > 0
            for key in ("total_tokens", "input_tokens", "output_tokens")
        )
    ):
        job.unknown_usage_count += 1
        recorded = {**entry, "state": "unknown"}
    else:

        def count(key: str) -> int:
            value = usage.get(key, 0)
            return max(0, value) if isinstance(value, int) else 0

        input_tokens, output_tokens = count("input_tokens"), count("output_tokens")
        total_tokens = max(count("total_tokens"), input_tokens + output_tokens)
        job.input_tokens += input_tokens
        job.output_tokens += output_tokens
        job.total_tokens += total_tokens
        job.conservative_tokens_reserved -= entry["estimated_tokens"]
        recorded = {**entry, "state": "recorded", "usage": _bounded(usage, 2000)}
    job.usage_reservations = {**job.usage_reservations, reservation_id: recorded}


async def reserve_tool_call(
    session: AsyncSession,
    *,
    claim: WebRuleJobClaim,
    tool_name: str,
    validation: bool = False,
    now: datetime | None = None,
) -> None:
    job = await check_web_rule_job(session, claim=claim, now=now)
    job.tool_calls_reserved += 1
    if validation:
        job.validation_calls_reserved += 1
    if job.stage != "activate":
        job.stage = "validate" if validation else "inspect"
    _diagnostic(
        job, phase=job.stage, code="tool_reserved", evidence={"tool": tool_name}, now=_now(now)
    )


async def reserve_goal_resumption(
    session: AsyncSession,
    *,
    claim: WebRuleJobClaim,
    feedback: dict | str,
    now: datetime | None = None,
) -> int:
    """Record a host-requested continuation without a separate round quota."""
    now = _now(now)
    job = await check_web_rule_job(session, claim=claim, now=now)
    job.goal_resumptions_reserved += 1
    _diagnostic(
        job,
        phase="goal",
        code="goal_resumption_reserved",
        evidence={"resumption": job.goal_resumptions_reserved, "feedback": feedback},
        now=now,
    )
    return job.goal_resumptions_reserved


async def save_web_rule_validation(
    session: AsyncSession,
    *,
    claim: WebRuleJobClaim,
    raw_rule: dict,
    report: dict,
    now: datetime | None = None,
) -> dict:
    """Persist real scanner evidence, separately from the model's acceptance judgment."""
    now = _now(now)
    job = await check_web_rule_job(session, claim=claim, now=now)
    previous = job.validation_report or {}
    try:
        rule = parse_web_rule(raw_rule, source_url=claim.source_url).model_dump(mode="json")
    except (WebRuleError, ValueError, TypeError):
        rule = _bounded(raw_rule, 60000)
    if rule == job.candidate_rule and previous.get("assessment"):
        report = {
            **report,
            "assessment": previous["assessment"],
            "limitations": previous.get("limitations", []),
        }
    job.candidate_rule = rule
    _save_validation_report(job, report)
    _diagnostic(
        job,
        phase="execute",
        code="execution_" + str(report.get("status", "error")),
        evidence={key: value for key, value in report.items() if key != "items"},
        now=now,
    )
    if report.get("execution_id"):
        job.validation_receipt = {
            "id": report["execution_id"],
            "job_id": str(job.id),
            "lease_token": str(claim.lease_token),
            "source_url": job.source_url,
            "base_rule_revision": job.base_rule_revision,
            "candidate_hash": rule_version(rule),
            "validator_version": job.validator_version,
            "first_window_completed": report.get("first_window_completed") is True,
            "expires_at": (
                now + timedelta(seconds=_limits(job).validation_ttl_seconds)
            ).isoformat(),
        }
    return dict(job.validation_report)


def _save_validation_report(job: WebRuleJob, report: dict) -> None:
    job.validation_report = bounded_execution_report(report)
    job.validation_receipt = None


async def save_web_rule_recovery_check(
    session: AsyncSession,
    *,
    claim: WebRuleJobClaim,
    report: dict,
    now: datetime | None = None,
) -> dict:
    now = _now(now)
    job = await check_web_rule_job(session, claim=claim, now=now)
    # The base rule is not the saved candidate. Its check must never replace
    # candidate execution facts, approval metadata or the candidate receipt.
    _diagnostic(
        job,
        phase="preflight",
        code="recovery_" + str(report.get("status", "error")),
        evidence=bounded_execution_report(report),
        now=now,
    )
    return report


async def web_rule_agent_progress(
    session: AsyncSession, *, claim: WebRuleJobClaim, now: datetime | None = None
) -> dict:
    job = await check_web_rule_job(session, claim=claim, now=now)
    return {
        "model_calls_used": job.model_calls_reserved,
        "model_calls_remaining": max(0, _limits(job).model_calls - job.model_calls_reserved),
        "tokens_used": job.total_tokens,
        "tokens_remaining": remaining_model_tokens(job),
        "unknown_tokens_reserved": job.conservative_tokens_reserved,
    }


async def activate_web_rule_job(
    session: AsyncSession,
    *,
    claim: WebRuleJobClaim,
    validation_id: str,
    raw_rule: dict | None = None,
    assessment: str | None = None,
    limitations: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    now = _now(now)
    job, source, state = await _checked(session, claim, now)
    receipt = job.validation_receipt or {}
    if (
        not receipt
        or receipt.get("id") != validation_id
        or receipt.get("lease_token") != str(claim.lease_token)
        or receipt.get("job_id") != str(job.id)
        or receipt.get("source_url") != source.canonical_url
        or receipt.get("base_rule_revision") != source_rule_revision(source)
        or receipt.get("validator_version") != job.validator_version
        or receipt.get("candidate_hash") != _hash(job.candidate_rule)
        or datetime.fromisoformat(receipt["expires_at"]) <= now
    ):
        raise JobGuardError("validation_expired")
    if receipt.get("first_window_completed") is not True:
        raise JobGuardError("execution_not_usable")
    if raw_rule is not None:
        try:
            final_rule = parse_web_rule(raw_rule, source_url=source.canonical_url).model_dump(
                mode="json"
            )
        except (WebRuleError, ValueError, TypeError) as exc:
            raise JobGuardError("final_rule_mismatch") from exc
        if rule_version(final_rule) != _hash(job.candidate_rule):
            raise JobGuardError("final_rule_mismatch")
    report = dict(job.validation_report or {})
    if assessment is not None:
        report["assessment"] = assessment[:2000]
        report["limitations"] = [str(item)[:1000] for item in (limitations or [])[:8]]
        job.validation_report = report
    if state.lease_token and state.lease_expires_at and state.lease_expires_at > now:
        job.stage = "activate"
        return {
            "status": "activation_pending",
            "code": "source_scan_busy",
            "validation_id": validation_id,
        }
    assert job.candidate_rule is not None
    result = await _activate_validated_rule(
        session,
        source=source,
        state=state,
        rule=job.candidate_rule,
        report=job.validation_report or {},
        now=now,
    )
    job.activated_version = result["version"]
    _terminal(job, "succeeded", "rule_activated", now)
    job.stage = "activate"
    await session.flush()
    return {"status": "succeeded", "code": "rule_activated", "version": result["version"]}


async def finish_web_rule_job(
    session: AsyncSession,
    *,
    claim: WebRuleJobClaim,
    status: str,
    code: str,
    message: str = "",
    retry_after_seconds: float = 0,
    now: datetime | None = None,
) -> None:
    now = _now(now)
    if status not in {"failed", "blocked", "cancelled", "retry_wait"}:
        raise ValueError("Only activation may mark a job succeeded")
    source, state = await _source_locked(session, claim.source_id)
    job = await session.scalar(
        select(WebRuleJob)
        .where(WebRuleJob.id == claim.job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.status != "running" or job.lease_token != claim.lease_token:
        raise JobGuardError("lease_lost")
    if code == "source_recovered" and source is not None and state is not None:
        if not _input_matches(job, source, state) or not await _eligible(session, source):
            status, code = "cancelled", "input_changed"
        else:
            state.web_rule_structural_failures = 0
            state.web_rule_failure_episode_id = None
            state.next_scan_at = now
    if status == "retry_wait":
        if not math.isfinite(retry_after_seconds) or retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be finite and nonnegative")
        delay = max(1, retry_after_seconds)
        available_at = now + timedelta(seconds=delay)
        job.status, job.available_at = "retry_wait", available_at
        job.last_error_code, job.last_error_message = code[:120], message[:2000]
        job.lease_token = job.lease_expires_at = None
        job.validation_receipt = None
        _diagnostic(job, phase=job.stage, code=code, evidence={"message": message[:2000]}, now=now)
    else:
        _terminal(job, status, code, now, message)


async def release_web_rule_slot(session: AsyncSession, *, claim: WebRuleJobClaim) -> None:
    runtime = await _runtime_locked(session)
    if runtime.current_job_id == claim.job_id and runtime.lease_token == claim.lease_token:
        runtime.current_job_id = runtime.lease_token = runtime.lease_expires_at = None


async def pause_web_rule_agent(
    session: AsyncSession,
    *,
    engine_snapshot: dict,
    code: str,
    message: str = "",
    claim: WebRuleJobClaim | None = None,
    now: datetime | None = None,
) -> bool:
    runtime = await _runtime_locked(session)
    if claim is not None and (
        runtime.current_job_id != claim.job_id or runtime.lease_token != claim.lease_token
    ):
        return False
    runtime.engine_snapshot = {
        key: value
        for key, value in engine_snapshot.items()
        if key not in {"browser_execution_identity", "cli_execution_identity"}
    }
    runtime.paused_code = code[:120]
    runtime.paused_message, runtime.paused_at, runtime.resume_at = message[:2000], _now(now), None
    return True


async def resume_web_rule_agent(session: AsyncSession) -> dict:
    runtime = await _runtime_locked(session)
    previous = runtime.paused_code
    runtime.paused_code = runtime.paused_message = runtime.paused_at = runtime.resume_at = None
    return {"status": "resumed", "previous_code": previous, "blocked_jobs_retried": False}


async def record_web_rule_diagnostic(
    session: AsyncSession,
    *,
    claim: WebRuleJobClaim,
    phase: str,
    code: str,
    evidence: dict,
    now: datetime | None = None,
) -> None:
    job = await check_web_rule_job(session, claim=claim, now=now)
    _diagnostic(job, phase=phase, code=code, evidence=evidence, now=_now(now))


async def backfill_web_rule_jobs(
    session: AsyncSession, *, limit: int = 50, now: datetime | None = None
) -> int:
    """Keyset batches skip already recorded input identities, including failures."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    # Filter discovery before LIMIT so pending/feed-backed sources cannot hide
    # sources ready for authoring. Recorded inputs remain revision-keyed,
    # including failures whose model budget must never be refreshed.
    source_ids = (
        await session.scalars(
            select(FeedSource.id)
            .join(SourceSyncState, SourceSyncState.source_id == FeedSource.id)
            .where(
                FeedSource.kind == SourceKind.WEB,
                FeedSource.status != SourceStatus.PAUSED,
                FeedSource.config["web_feed_discovery"].contains(
                    {"version": 1, "status": "not_found"}
                ),
                FeedSource.config["web_feed_discovery"]["source_url"].astext
                == FeedSource.canonical_url,
                or_(
                    FeedSource.config["web_rule"].astext.is_(None),
                    SourceSyncState.last_error_code == "web_rule_required",
                ),
                exists().where(
                    SourceSubscription.source_id == FeedSource.id,
                    SourceSubscription.is_enabled.is_(True),
                ),
                ~exists().where(
                    WebRuleJob.source_id == FeedSource.id,
                    WebRuleJob.base_rule_revision
                    == func.coalesce(FeedSource.config["web_rule_revision"].as_integer(), 0),
                    WebRuleJob.reason == "author",
                    ~(
                        (WebRuleJob.status == "cancelled")
                        & (WebRuleJob.last_error_code == "source_inactive")
                    ),
                ),
            )
            .order_by(FeedSource.id)
            .limit(limit)
        )
    ).all()
    count = 0
    for source_id in source_ids:
        job = await ensure_web_rule_job(session, source_id=source_id, now=now)
        if job is not None and job.status in ACTIVE_STATUSES:
            count += 1
    return count


async def retry_web_rule_job(
    session: AsyncSession, *, job_id: UUID, request_id: UUID, now: datetime | None = None
) -> WebRuleJob:
    """Explicit operator retry grants a new auditable budget, never resets history."""
    now = _now(now)
    previous_source_id = await session.scalar(
        select(WebRuleJob.source_id).where(WebRuleJob.id == job_id)
    )
    if previous_source_id is None:
        raise JobGuardError("job_not_found")
    source, state = await _source_locked(session, previous_source_id)
    previous = await session.get(WebRuleJob, job_id)
    assert previous is not None
    existing = await session.scalar(
        select(WebRuleJob).where(WebRuleJob.idempotency_key == f"manual:{request_id}")
    )
    if existing:
        if existing.retry_of != job_id:
            raise JobGuardError("request_id_conflict")
        return existing
    if previous.status not in {"failed", "blocked", "cancelled"}:
        raise JobGuardError("job_not_retryable")
    if not await _eligible(session, source) or state is None:
        raise JobGuardError("source_inactive")
    assert source is not None
    active = await session.scalar(
        select(WebRuleJob.id).where(
            WebRuleJob.source_id == source.id, WebRuleJob.status.in_(ACTIVE_STATUSES)
        )
    )
    if active:
        raise JobGuardError("source_job_active")
    if not _input_matches(previous, source, state):
        raise JobGuardError("input_changed")
    job = WebRuleJob(
        id=uuid4(),
        source_id=source.id,
        source_url=source.canonical_url,
        reason=previous.reason,
        idempotency_key=f"manual:{request_id}",
        retry_of=previous.id,
        failure_episode_id=previous.failure_episode_id,
        base_rule_revision=source_rule_revision(source),
        base_rule_hash=_hash(source.config.get("web_rule")),
        base_rule=source.config.get("web_rule"),
        available_at=now,
    )
    session.add(job)
    await session.flush()
    return job


def web_rule_job_detail(job: WebRuleJob) -> dict:
    return {column.name: getattr(job, column.name) for column in WebRuleJob.__table__.columns}


async def list_web_rule_jobs(
    session: AsyncSession,
    *,
    status: str | None = None,
    source_id: UUID | None = None,
    limit: int = 50,
) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    query = select(WebRuleJob).order_by(WebRuleJob.created_at.desc()).limit(limit)
    if status:
        query = query.where(WebRuleJob.status == status)
    if source_id:
        query = query.where(WebRuleJob.source_id == source_id)
    return [web_rule_job_detail(row) for row in (await session.scalars(query)).all()]


async def get_web_rule_job(session: AsyncSession, *, job_id: UUID) -> dict:
    job = await session.get(WebRuleJob, job_id)
    if job is None:
        raise JobGuardError("job_not_found")
    return web_rule_job_detail(job)


async def web_rule_agent_status(
    session: AsyncSession, *, engine_snapshot: dict | None = None
) -> dict:
    if engine_snapshot is not None:
        engine_snapshot = {
            key: value
            for key, value in engine_snapshot.items()
            if key not in {"browser_execution_identity", "cli_execution_identity"}
        }
    runtime = await session.get(WebRuleAgentRuntime, "default")
    counts = dict(
        (
            await session.execute(
                select(WebRuleJob.status, func.count()).group_by(WebRuleJob.status)
            )
        ).all()
    )
    paused = bool(
        runtime
        and runtime.paused_code
        and (engine_snapshot is None or runtime.engine_snapshot == engine_snapshot)
    )
    return {
        "paused": paused,
        "mode": "paused" if paused else "active",
        "pending_count": counts.get("queued", 0) + counts.get("retry_wait", 0),
        "queued": counts.get("queued", 0) + counts.get("retry_wait", 0),
        "running": counts.get("running", 0),
        "last_error_code": runtime.paused_code if paused else None,
        "current_job_id": str(runtime.current_job_id)
        if runtime and runtime.current_job_id
        else None,
        "counts": counts,
    }
