"""Record Web listing health and request rule repair within the caller's scan transaction."""

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from app.ingestion.web_feed import configured_web_feed_url
from app.services.scan_state import reset_for_feed_rediscovery
from app.services.web_rule_jobs import ensure_web_rule_job
from app.storage.models import FeedSource, SourceSyncState


async def record_web_structure_health(
    session,
    source: FeedSource,
    state: SourceSyncState,
    *,
    code: str | None,
    evidence: dict[str, Any],
    now: datetime,
) -> None:
    """Caller holds source→state locks and commits this evidence with its scan."""
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
            reset_for_feed_rediscovery(state)
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
