"""Validate, version and activate agent-authored Web rules on shared sources."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import SourceKind, SyncPhase, SyncRunStatus
from app.ingestion.web_rules import WebRuleError, execute_web_rule, parse_web_rule
from app.storage.models import FeedSource, SourceSyncRun, SourceSyncState


def rule_version(rule: dict) -> str:
    return hashlib.sha256(
        json.dumps(rule, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def versioned_config(config: dict, rule: dict, report: dict) -> dict:
    """Bounded history lives with the existing shared-source configuration."""
    config = dict(config)
    history = list(config.get("web_rule_versions") or [])
    previous = config.get("web_rule")
    if previous and not any(row.get("version") == rule_version(previous) for row in history):
        history.append({"version": rule_version(previous), "rule": previous, "validated_at": None})
    version = rule_version(rule)
    history = [row for row in history if row.get("version") != version]
    history.append(
        {
            "version": version,
            "rule": rule,
            "validated_at": datetime.now(UTC).isoformat(),
            "validation": {
                key: report[key]
                for key in (
                    "status",
                    "first_window_completed",
                    "completed_windows",
                    "next_continuation",
                    "scope",
                    "assessment",
                    "limitations",
                )
                if key in report
            },
        }
    )
    config.update(
        {
            "web_rule": rule,
            "web_rule_active_version": version,
            "web_rule_versions": history[-5:],
            "web_rule_candidate": None,
            "web_rule_revision": int(config.get("web_rule_revision", 0)) + 1,
        }
    )
    return config


async def _source(session: AsyncSession, source_id: UUID) -> FeedSource:
    source = await session.get(FeedSource, source_id)
    if source is None or source.kind != SourceKind.WEB:
        raise WebRuleError("source must be an existing Web source")
    return source


async def submit_web_rule(
    session: AsyncSession, *, source_id: UUID, raw_rule: object
) -> dict[str, Any]:
    """A failed candidate is diagnostic data and never replaces the active rule."""
    source = await _source(session, source_id)
    source_url = source.canonical_url
    previous = source.config.get("web_rule")
    previous_revision = source.config.get("web_rule_revision", 0)
    rule = parse_web_rule(raw_rule, source_url=source_url).model_dump(mode="json")
    # No database connection/transaction is held during browser/network work.
    await session.commit()
    report = await execute_web_rule(source_url=source_url, raw_rule=rule)
    locked_source = await session.scalar(
        select(FeedSource)
        .where(FeedSource.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    state = await session.scalar(
        select(SourceSyncState).where(SourceSyncState.source_id == source_id).with_for_update()
    )
    if locked_source is None or state is None:
        raise WebRuleError("source no longer has a synchronisation state")
    if (
        locked_source.canonical_url != source_url
        or locked_source.config.get("web_rule") != previous
        or locked_source.config.get("web_rule_revision", 0) != previous_revision
    ):
        await session.rollback()
        return {
            "status": "rejected",
            "code": "web_rule_conflict",
            "message": "Source changed during validation; retry.",
        }
    if (
        state.lease_token is not None
        and state.lease_expires_at
        and state.lease_expires_at > datetime.now(UTC)
    ):
        await session.rollback()
        return {
            "status": "rejected",
            "code": "web_rule_source_busy",
            "message": "Source scan is running; retry after its lease completes.",
        }
    if report.get("first_window_completed") is not True:
        locked_source.config = {
            **locked_source.config,
            "web_rule_candidate": {
                "version": rule_version(rule),
                "rule": rule,
                "validation": report,
                "tested_at": datetime.now(UTC).isoformat(),
            },
        }
        await session.commit()
        return {"status": "rejected", "validation": report}
    result = await _activate_validated_rule(
        session,
        source=locked_source,
        state=state,
        rule=rule,
        report=report,
        now=datetime.now(UTC),
    )
    await session.commit()
    return result


async def _activate_validated_rule(
    session: AsyncSession,
    *,
    source: FeedSource,
    state: SourceSyncState,
    rule: dict,
    report: dict,
    now: datetime,
) -> dict[str, Any]:
    """Internal activation seam; caller holds source/state locks and owns commit.

    Only an actual first-window execution or a fenced persisted Agent receipt
    may reach here. Model assessment is metadata, never execution authority.
    """
    config = versioned_config(source.config, rule, report)
    # An explicit, validated management replacement must actually select the
    # native adapter. Agent claims cannot reach this seam for feed-backed sources.
    config.pop("web_feed_discovery", None)
    source.config = config
    # Old cursors/feed discovery belong to the previous rule; restart the head
    # while retaining content identity and the existing deduplication store.
    state.committed_checkpoint = {
        key: value
        for key, value in state.committed_checkpoint.items()
        if key in {"head_ids", "item_hashes"}
    }
    state.lease_token = None
    state.lease_expires_at = None
    await session.execute(
        update(SourceSyncRun)
        .where(SourceSyncRun.source_id == source.id, SourceSyncRun.status == SyncRunStatus.RUNNING)
        .values(
            status=SyncRunStatus.PARTIAL,
            finished_at=now,
            error_code="web_rule_replaced",
            error_message="Rule activation invalidated the old scanner lease.",
        )
    )
    state.pending_checkpoint = {}
    state.continuation = None
    state.phase = SyncPhase.IDLE
    state.last_error_code = None
    state.last_error_message = None
    state.consecutive_failures = 0
    state.web_rule_structural_failures = 0
    state.web_rule_failure_episode_id = None
    state.provider_mode = "web_crawl4ai"
    state.next_scan_at = now
    return {
        "status": "accepted",
        "version": config["web_rule_active_version"],
        "validation": report,
        "rule": rule,
    }


async def list_web_rule_versions(session: AsyncSession, *, source_id: UUID) -> dict[str, Any]:
    source = await _source(session, source_id)
    state = await session.get(SourceSyncState, source_id)
    return {
        "active_version": source.config.get("web_rule_active_version"),
        "versions": source.config.get("web_rule_versions", []),
        "candidate": source.config.get("web_rule_candidate"),
        "last_error_code": getattr(state, "last_error_code", None),
        "last_error_message": getattr(state, "last_error_message", None),
        "repair_trigger": "backend rule author worker; scheduled Crawl4AI scans use no model",
    }


async def rollback_web_rule(
    session: AsyncSession, *, source_id: UUID, version: str
) -> dict[str, Any]:
    source = await _source(session, source_id)
    stored = next(
        (row for row in source.config.get("web_rule_versions", []) if row["version"] == version),
        None,
    )
    if stored is None:
        raise WebRuleError("version is not in the retained history (last 5)")
    # Old rules can decay too: rollback receives the same current-page validation.
    return await submit_web_rule(session, source_id=source_id, raw_rule=stored["rule"])


async def list_web_sources_needing_rule(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """List shared Web sources awaiting authoring; does not claim or call a model."""
    if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
        raise WebRuleError("limit must be 1..100 and offset 0..100000")
    rows = (
        await session.execute(
            select(FeedSource, SourceSyncState)
            .join(SourceSyncState, SourceSyncState.source_id == FeedSource.id)
            .where(
                FeedSource.kind == SourceKind.WEB,
                or_(
                    FeedSource.config["web_rule"].astext.is_(None),
                    SourceSyncState.last_error_code == "web_rule_required",
                ),
            )
            .order_by(FeedSource.id)
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    return {
        "sources": [
            {
                "source_id": str(source.id),
                "source_url": source.canonical_url,
                "last_error_code": state.last_error_code,
                "last_error_message": state.last_error_message,
            }
            for source, state in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
        "authoring": "inspect_web_page -> execute_web_rule -> FinalWebRule",
    }
