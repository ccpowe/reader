"""Synchronous scan progress, source health, and run transitions.

The worker owns I/O and source→state locking. These transitions mutate locked ORM
rows in the caller's transaction; none performs I/O or commits.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from app.domain.enums import SourceKind, SourceStatus, SyncPhase, SyncRunStatus
from app.ingestion.candidate_queue import CandidateUpsertResult
from app.ingestion.models import (
    DiscoveredContent,
    SourceScanError,
    SourceScanPage,
    SourceScanRequest,
)
from app.storage.models import FeedSource, SourceSyncRun, SourceSyncState

_RESTART_CURSOR = {"_restart_from_head": True}


class PageDisposition(Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    CANDIDATE_LIMIT = "candidate_limit"


@dataclass
class ScanProgress:
    """The in-memory page budget and durable progress of one claimed scan."""

    source: FeedSource
    state: SourceSyncState
    max_pages: int
    max_raw_items: int
    max_changes: int
    max_response_bytes: int

    def __post_init__(self) -> None:
        self.mode = (
            "initial"
            if not self.state.initial_sync_completed
            else "catchup"
            if self.state.continuation
            else "head"
        )
        self.committed = dict(self.state.committed_checkpoint)
        self.current_baseline = self.committed
        self.pending = self.state.pending_checkpoint
        self.head_checkpoint: dict[str, Any] | None = None
        self.cursor: dict[str, Any] | None = None
        self.head_exhausted = False
        self.changed = 0
        self.raw_items = 0
        self.pages = 0
        self.latest_checkpoint: dict[str, Any] = {}
        self.tasks: list[dict[str, Any]] = []
        self.head_first = False
        if self.mode == "catchup":
            context = dict(self.state.continuation or {})
            self.latest_checkpoint = dict(
                context.get("latest_checkpoint")
                if isinstance(context.get("latest_checkpoint"), dict)
                else self.pending or self.committed
            )
            durable = [dict(task) for task in context.get("tasks", []) if isinstance(task, dict)]
            fresh = {
                "lane": "head",
                "cursor": _RESTART_CURSOR,
                "checkpoint": self.latest_checkpoint,
                "fresh": True,
            }
            # Runs alternate which work gets the budget first: durable tasks
            # always advance every other run, and new content is never delayed
            # by more than one run however long the backlog is.
            self.head_first = bool(context.get("head_first"))
            self.tasks = [fresh, *durable] if self.head_first else [*durable, fresh]

    def next_request(self) -> SourceScanRequest | None:
        if self.mode == "initial":
            if self.pages:
                return None
            initial_raw = (
                15 if self.source.kind in {SourceKind.YOUTUBE, SourceKind.X} else self.max_raw_items
            )
            return SourceScanRequest(
                checkpoint={},
                initial=True,
                conditional=False,
                max_raw_items=initial_raw,
                max_response_bytes=self.max_response_bytes,
            )
        remaining = self.max_raw_items - self.raw_items
        if self.pages >= self.max_pages or remaining <= 0:
            return None
        if self.mode == "catchup":
            if not self.tasks:
                return None
            task = self.tasks[0]
            checkpoint = task.get("checkpoint")
            self.current_baseline = dict(checkpoint) if isinstance(checkpoint, dict) else {}
            return SourceScanRequest(
                checkpoint=self.current_baseline,
                continuation=_provider_cursor(task.get("cursor")),
                initial=False,
                conditional=False,
                max_raw_items=remaining,
                max_response_bytes=self.max_response_bytes,
            )
        if self.head_exhausted:
            return None
        return SourceScanRequest(
            checkpoint=self.committed,
            continuation=self.cursor,
            initial=False,
            conditional=self.cursor is None and self.state.phase == SyncPhase.IDLE,
            max_raw_items=remaining,
            max_response_bytes=self.max_response_bytes,
        )

    def baseline_checkpoint(self) -> dict[str, Any]:
        return {} if self.mode == "initial" else self.current_baseline

    def candidate_items(self, page: SourceScanPage) -> tuple[DiscoveredContent, ...]:
        return page.items[:10] if self.mode == "initial" else page.items

    def candidate_budget(self) -> int:
        return (
            min(10, self.max_changes)
            if self.mode == "initial"
            else max(self.max_changes - self.changed, 0)
        )

    def incomplete_reason(self) -> str:
        if self.pages >= self.max_pages:
            return "pages"
        if self.raw_items >= self.max_raw_items:
            return "raw_items"
        return "provider_continuation"

    def apply_page(
        self,
        state: SourceSyncState,
        source: FeedSource,
        run: SourceSyncRun,
        page: SourceScanPage,
        outcome: CandidateUpsertResult,
        now: datetime,
    ) -> PageDisposition:
        """Apply the enqueued page and its checkpoint in the same transaction."""
        self.pages += 1
        self.raw_items += page.raw_items
        self.changed += outcome.changed
        _record_page(run, state, page, outcome, now)
        if self.mode == "initial":
            state.initial_sync_completed = True
            state.committed_checkpoint = _with_unchanged_streak(
                page.checkpoint, changed=outcome.changed, previous={}
            )
            state.pending_checkpoint = {}
            state.continuation = None
            state.provider_mode = page.provider_mode
            state.last_complete_at = now if page.authoritative else None
            state.phase = (
                SyncPhase.DEGRADED
                if page.warning_code or page.gap_detected or not page.authoritative
                else SyncPhase.IDLE
            )
            _apply_page_health(state, page)
            state.next_scan_at = next_normal_scan(source, state, now)
            _release_lease(state)
            source.status = SourceStatus.ACTIVE
            _finish_run(
                run,
                page,
                now,
                partial=bool(page.warning_code or page.gap_detected or page.limit_reached),
            )
            return PageDisposition.COMPLETE

        if not page.authoritative:
            state.provider_mode = page.provider_mode
            state.phase = SyncPhase.DEGRADED
            _apply_page_health(state, page)
            # A truncated fallback must replay against the previous baseline;
            # it cannot advance either Atom coverage or the authoritative backlog.
            if outcome.cap_reached:
                return PageDisposition.CANDIDATE_LIMIT
            state.committed_checkpoint = _merge_fallback_checkpoint(
                state.committed_checkpoint, page.checkpoint
            )
            state.next_scan_at = next_normal_scan(source, state, now)
            _release_lease(state)
            _finish_run(run, page, now, partial=True)
            return PageDisposition.COMPLETE

        if self.mode == "head":
            if self.head_checkpoint is None:
                self.head_checkpoint = page.checkpoint
            checkpoint = self.head_checkpoint or page.checkpoint
            if page.completed and not outcome.cap_reached:
                self._complete_coverage(state, source, run, page, now, checkpoint)
                return PageDisposition.COMPLETE
            state.pending_checkpoint = checkpoint
            state.phase = SyncPhase.CATCHING_UP
            state.provider_mode = page.provider_mode
            next_cursor = _cursor_for_incomplete_page(self.cursor, page, outcome)
            state.continuation = {
                "latest_checkpoint": checkpoint,
                "tasks": [{"lane": "backlog", "cursor": next_cursor, "checkpoint": self.committed}],
            }
            _apply_page_health(state, page)
            state.next_scan_at = next_normal_scan(source, state, now)
            if outcome.cap_reached or self.changed >= self.max_changes:
                return PageDisposition.CANDIDATE_LIMIT
            self.cursor = page.next_continuation
            self.head_exhausted = page.completed or self.cursor is None
            return PageDisposition.CONTINUE

        task = self.tasks[0]
        cursor = _provider_cursor(task.get("cursor"))
        if task.get("fresh") and cursor is None:
            self.latest_checkpoint = page.checkpoint
        if outcome.cap_reached:
            task["cursor"] = task.get("cursor") or _RESTART_CURSOR
            task.pop("fresh", None)
        elif page.completed:
            self.tasks = self.tasks[1:]
        else:
            task["cursor"] = page.next_continuation or _RESTART_CURSOR
            task.pop("fresh", None)

        if not self.tasks:
            # A fallback may refresh its Atom checkpoint during the backlog.
            final_checkpoint = _merge_fallback_checkpoint(
                self.latest_checkpoint, state.committed_checkpoint
            )
            self._complete_coverage(state, source, run, page, now, final_checkpoint)
            return PageDisposition.COMPLETE
        state.pending_checkpoint = self.pending or self.latest_checkpoint
        state.continuation = {
            "latest_checkpoint": self.latest_checkpoint,
            # An unvisited fresh head is regenerated next run, not accumulated.
            "tasks": [_serializable_task(value) for value in self.tasks if not value.get("fresh")],
            "head_first": not self.head_first,
        }
        state.phase = SyncPhase.CATCHING_UP
        state.provider_mode = page.provider_mode
        _apply_page_health(state, page)
        state.next_scan_at = next_normal_scan(source, state, now)
        if outcome.cap_reached or self.changed >= self.max_changes:
            return PageDisposition.CANDIDATE_LIMIT
        return PageDisposition.CONTINUE

    def _complete_coverage(
        self,
        state: SourceSyncState,
        source: FeedSource,
        run: SourceSyncRun,
        page: SourceScanPage,
        now: datetime,
        checkpoint: dict[str, Any],
    ) -> None:
        state.committed_checkpoint = _with_unchanged_streak(
            checkpoint, changed=self.changed, previous=state.committed_checkpoint
        )
        state.pending_checkpoint = {}
        state.continuation = None
        state.last_complete_at = now
        state.phase = (
            SyncPhase.DEGRADED if page.gap_detected or page.warning_code else SyncPhase.IDLE
        )
        state.provider_mode = page.provider_mode
        state.consecutive_failures = 0
        state.consecutive_limit_runs = state.consecutive_limit_runs + 1 if page.limit_reached else 0
        _apply_page_health(state, page)
        state.next_scan_at = next_normal_scan(source, state, now)
        _release_lease(state)
        _finish_run(
            run,
            page,
            now,
            partial=bool(page.gap_detected or page.warning_code or page.limit_reached),
        )


def recovered_run_values(now: datetime) -> dict[str, Any]:
    return {
        "status": SyncRunStatus.PARTIAL,
        "finished_at": now,
        "error_code": "lease_recovered",
        "error_message": "A previous scanner lease expired before completion.",
    }


def claim_scan(state: SourceSyncState, token: UUID, now: datetime, lease_seconds: int) -> None:
    state.lease_token = token
    state.lease_expires_at = now + timedelta(seconds=lease_seconds)


def start_scan(state: SourceSyncState, now: datetime) -> None:
    state.last_attempt_at = now


def empty_run_metrics() -> dict[str, Any]:
    return {
        "pages": 0,
        "raw_items": 0,
        "normalized_items": 0,
        "candidates_changed": 0,
        "duplicates": 0,
        "limits": [],
    }


def _record_page(
    run: SourceSyncRun,
    state: SourceSyncState,
    page: SourceScanPage,
    outcome: CandidateUpsertResult,
    now: datetime,
) -> None:
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


def finish_incomplete_run(
    source: FeedSource,
    state: SourceSyncState,
    run: SourceSyncRun,
    *,
    now: datetime,
    limit_reached: str,
) -> None:
    state.phase = SyncPhase.CATCHING_UP
    state.consecutive_limit_runs += 1
    state.next_scan_at = next_normal_scan(source, state, now)
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


def record_scan_failure(
    source: FeedSource,
    state: SourceSyncState,
    run: SourceSyncRun | None,
    exc: SourceScanError,
    now: datetime,
) -> None:
    state.last_error_code = exc.code
    state.last_error_message = str(exc)[:2000]
    if exc.code == "cursor_invalid":
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
        state.next_scan_at = next_normal_scan(source, state, now)
        status = SyncRunStatus.PARTIAL
    else:
        state.phase = SyncPhase.DEGRADED
        state.consecutive_failures += 1
        state.next_scan_at = now + retry_delay(exc, state.consecutive_failures)
        status = SyncRunStatus.FAILED
    _release_lease(state)
    if run is not None:
        run.status = status
        run.finished_at = now
        run.error_code = exc.code
        run.error_message = str(exc)[:2000]
        if exc.evidence:
            run.metrics = {**(run.metrics or {}), "error_evidence": exc.evidence}


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
    input_cursor: dict[str, Any] | None, page: SourceScanPage, outcome: CandidateUpsertResult
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


def checkpoint_hashes(checkpoint: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for key, value in checkpoint.items():
        if key == "item_hashes" and isinstance(value, dict):
            hashes.update(
                {str(native_id): str(item_hash) for native_id, item_hash in value.items()}
            )
        elif isinstance(value, dict):
            hashes.update(checkpoint_hashes(value))
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


def normal_interval_minutes(source: FeedSource, state: SourceSyncState) -> int:
    if source.kind == SourceKind.RSS:
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


def next_normal_scan(source: FeedSource, state: SourceSyncState, now: datetime) -> datetime:
    minutes = normal_interval_minutes(source, state)
    return now + timedelta(minutes=minutes * random.uniform(0.9, 1.1))


def retry_delay(exc: SourceScanError, failures: int) -> timedelta:
    if exc.retry_after_seconds is not None:
        return timedelta(seconds=max(exc.retry_after_seconds, 60))
    if exc.long_lived:
        return timedelta(hours=24)
    minutes = (2, 10, 30, 120, 360)[min(max(failures - 1, 0), 4)]
    return timedelta(minutes=minutes * random.uniform(0.9, 1.1))
