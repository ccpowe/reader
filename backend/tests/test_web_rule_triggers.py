"""Repair classification, source presentation and optional author health."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.api.sources import _project_web_rule_health, _web_rule_health_projections
from app.domain.enums import SourceKind, SourceStatus, SyncPhase
from app.ingestion.models import SourceScanError, SourceScanRequest
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.web_rules import parse_web_rule, web_rule_failure_feedback
from app.workers.sync import _record_web_structure_health, _retry_delay

URL = "https://example.com/blog"
RULE = {
    "id": "example",
    "hosts": ["example.com"],
    "index_paths": ["/blog"],
    "listing": {
        "extraction": {
            "baseSelector": "article",
            "fields": [
                {"name": "url", "type": "attribute", "selector": "a", "attribute": "href"},
                {"name": "title", "type": "text", "selector": "h2"},
            ],
        }
    },
    "next_page_selector": "a.next",
}


def source_state(*, has_rule=True, episode=None):
    source = SimpleNamespace(
        id=uuid4(),
        kind=SourceKind.WEB,
        status=SourceStatus.ACTIVE,
        canonical_url=URL,
        config={
            "web_rule_revision": 2,
            "web_feed_discovery": {"version": 1, "source_url": URL, "status": "not_found"},
            **({"web_rule": RULE} if has_rule else {}),
        },
    )
    state = SimpleNamespace(
        web_rule_structural_failures=0,
        web_rule_failure_episode_id=episode,
        phase=SyncPhase.IDLE,
        last_error_code=None,
    )
    return source, state


@pytest.mark.parametrize("structural_code", ["web_structure_changed", "web_rule_action_failed"])
async def test_repair_requires_two_head_failures_and_recovery_closes_episode(
    monkeypatch, structural_code
):
    source, state = source_state()
    session, enqueue = AsyncMock(), AsyncMock()
    monkeypatch.setattr("app.workers.sync.ensure_web_rule_job", enqueue)

    async def record(code, evidence):
        await _record_web_structure_health(
            session,
            source,
            state,
            code=code,
            evidence=evidence,
            now=datetime.now(UTC),
        )

    await record(structural_code, {"is_head": True, "valid_pairs": 0})
    episode = state.web_rule_failure_episode_id
    assert episode is not None and state.web_rule_structural_failures == 1
    enqueue.assert_not_awaited()
    await record("web_http_429", {})
    assert state.web_rule_structural_failures == 0
    assert state.web_rule_failure_episode_id == episode
    await record("web_rule_missing_fields", {"is_head": True, "valid_pairs": 1})
    enqueue.assert_not_awaited()
    assert state.web_rule_structural_failures == 0 and state.web_rule_failure_episode_id is None
    await record(structural_code, {"is_head": True, "valid_pairs": 0})
    episode = state.web_rule_failure_episode_id
    await record(structural_code, {"is_head": True, "valid_pairs": 0})
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["reason"] == "repair"
    assert state.web_rule_failure_episode_id == episode
    await record(None, {"is_head": True, "not_modified": True})
    assert state.web_rule_structural_failures == 0
    assert state.web_rule_failure_episode_id == episode
    # Visible results can all be duplicates: usable head evidence is sufficient.
    await record(None, {"is_head": True, "valid_pairs": 8})
    assert state.web_rule_failure_episode_id is None


@pytest.mark.parametrize(
    "code,evidence",
    [
        ("web_structure_changed", {}),
        ("web_structure_changed", {"is_head": False}),
        ("web_rule_action_failed", {"is_head": False}),
        ("web_http_403", {}),
        ("robots_disallowed", {}),
        ("source_timeout", {}),
        ("web_http_500", {}),
        ("cursor_invalid", {}),
    ],
)
async def test_non_head_and_transport_failures_never_dispatch_repair(monkeypatch, code, evidence):
    source, state = source_state(episode=uuid4())
    state.web_rule_structural_failures = 1
    episode = state.web_rule_failure_episode_id
    enqueue = AsyncMock()
    monkeypatch.setattr("app.workers.sync.ensure_web_rule_job", enqueue)
    await _record_web_structure_health(
        AsyncMock(),
        source,
        state,
        code=code,
        evidence=evidence,
        now=datetime.now(UTC),
    )
    enqueue.assert_not_awaited()
    assert state.web_rule_structural_failures == 0
    assert state.web_rule_failure_episode_id == episode


async def test_verified_empty_tail_is_complete_but_empty_head_is_structural(monkeypatch):
    async def fetch(_client, url, **_kwargs):
        body = (
            "User-agent: *\nAllow: /\n"
            if url.endswith("robots.txt")
            else (
                '<article><a href="/first"><h2>First</h2></a></article>'
                '<a class="next" href="?page=2">Next</a>'
                if url == URL
                else "<main></main>"
            )
        )
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    async with httpx.AsyncClient() as client:
        adapter = WebBlogSourceAdapter(
            WebBlogSourceConfig(url=URL, rule=parse_web_rule(RULE, source_url=URL)),
            client,
        )
        head = await adapter.scan_page(SourceScanRequest(conditional=False))
        tail = await adapter.scan_page(SourceScanRequest(continuation=head.next_continuation))
        assert tail.completed and not tail.items
        assert tail.web_listing_evidence["is_head"] is False
        empty_head = WebBlogSourceAdapter(
            WebBlogSourceConfig(url=URL + "?page=2", rule=parse_web_rule(RULE, source_url=URL)),
            client,
        )
        with pytest.raises(SourceScanError) as caught:
            await empty_head.scan_page(SourceScanRequest(conditional=False))
    assert caught.value.code == "web_structure_changed"
    assert caught.value.evidence["is_head"] is True
    assert caught.value.evidence["extracted_rows"] == 0
    assert not caught.value.long_lived
    assert _retry_delay(caught.value, 1).total_seconds() < 5 * 60


@pytest.mark.parametrize(
    "code,retryable",
    [
        ("web_http_429", True),
        ("web_http_503", True),
        ("web_http_403", False),
        ("robots_disallowed", False),
        ("web_rule_missing_fields", False),
    ],
)
def test_page_feedback_preserves_error_classification(code, retryable):
    feedback = web_rule_failure_feedback(SourceScanError(code, "detail"), phase="validate")
    assert feedback["retryable"] is retryable
    assert feedback["phase"] == "validate"


@pytest.mark.parametrize("continuation", [False, True])
async def test_listing_action_failure_is_bound_to_head_or_continuation(monkeypatch, continuation):
    async with httpx.AsyncClient() as client:
        adapter = WebBlogSourceAdapter(
            WebBlogSourceConfig(url=URL, rule=parse_web_rule(RULE, source_url=URL)),
            client,
        )
        monkeypatch.setattr(adapter, "_robots_allows", AsyncMock(return_value=True))
        monkeypatch.setattr(adapter, "_validated_next_listing", AsyncMock(return_value=(URL, 0)))
        monkeypatch.setattr(
            adapter,
            "_crawl_listing",
            AsyncMock(
                side_effect=SourceScanError(
                    "web_rule_action_failed",
                    "missing target",
                    evidence={"action_index": 0},
                )
            ),
        )
        with pytest.raises(SourceScanError) as caught:
            await adapter.scan_page(
                SourceScanRequest(
                    continuation={"provider": "web_crawl4ai"} if continuation else None,
                )
            )
    assert caught.value.evidence["is_head"] is (not continuation)
    assert caught.value.evidence["action_index"] == 0


def test_source_projection_ignores_historical_job_and_disabled_agent_when_healthy():
    source, state = source_state()
    old_job = SimpleNamespace(
        base_rule_revision=1, reason="repair", failure_episode_id=uuid4(), status="failed"
    )
    assert _project_web_rule_health(source, state, old_job, agent_unavailable=True) == (
        SyncPhase.IDLE,
        None,
    )
    state.web_rule_failure_episode_id = uuid4()
    assert _project_web_rule_health(source, state, old_job, agent_unavailable=False) == (
        SyncPhase.IDLE,
        None,
    )


@pytest.mark.parametrize(
    "missing,job_status,unavailable,expected",
    [
        (True, "queued", False, "web_rule_authoring"),
        (False, "running", False, "web_rule_repairing"),
        (True, "failed", False, "web_rule_authoring_failed"),
        (False, "blocked", False, "web_rule_agent_unavailable"),
        (True, "queued", True, "web_rule_agent_unavailable"),
    ],
)
def test_source_projection_current_job_and_pause_precedence(
    missing, job_status, unavailable, expected
):
    source, state = source_state(has_rule=not missing, episode=None if missing else uuid4())
    job = SimpleNamespace(
        base_rule_revision=2,
        reason="author" if missing else "repair",
        failure_episode_id=state.web_rule_failure_episode_id,
        status=job_status,
    )
    assert _project_web_rule_health(source, state, job, agent_unavailable=unavailable) == (
        SyncPhase.DEGRADED,
        expected,
    )
    source.status = SourceStatus.PAUSED
    assert _project_web_rule_health(source, state, job, agent_unavailable=unavailable) == (
        SyncPhase.IDLE,
        None,
    )


@pytest.mark.parametrize("code", ["web_http_401", "web_http_403", "web_challenge_required"])
@pytest.mark.parametrize("missing", [True, False])
@pytest.mark.parametrize("unavailable", [True, False])
def test_current_failed_rule_task_preserves_upstream_access_cause(code, missing, unavailable):
    source, state = source_state(has_rule=not missing, episode=None if missing else uuid4())
    job = SimpleNamespace(
        base_rule_revision=2,
        reason="author" if missing else "repair",
        failure_episode_id=state.web_rule_failure_episode_id,
        status="failed",
        last_error_code=code,
    )
    assert _project_web_rule_health(source, state, job, agent_unavailable=unavailable) == (
        SyncPhase.DEGRADED,
        code,
    )
    source.status = SourceStatus.PAUSED
    assert _project_web_rule_health(source, state, job, agent_unavailable=unavailable) == (
        SyncPhase.IDLE,
        None,
    )


def test_historical_access_error_does_not_replace_current_source_health():
    source, state = source_state(has_rule=False)
    job = SimpleNamespace(
        base_rule_revision=1,
        reason="author",
        failure_episode_id=None,
        status="failed",
        last_error_code="web_challenge_required",
    )
    assert _project_web_rule_health(source, state, job, agent_unavailable=False) == (
        SyncPhase.DEGRADED,
        "web_rule_authoring",
    )


@pytest.mark.parametrize("code", ["web_http_403", "web_challenge_required"])
@pytest.mark.parametrize("job_status", ["queued", "running", "failed"])
@pytest.mark.parametrize("unavailable", [True, False])
def test_latest_scan_access_failure_remains_visible_during_an_older_repair_episode(
    code, job_status, unavailable
):
    source, state = source_state(episode=uuid4())
    state.phase = SyncPhase.DEGRADED
    state.last_error_code = code
    job = SimpleNamespace(
        base_rule_revision=2,
        reason="repair",
        failure_episode_id=state.web_rule_failure_episode_id,
        status=job_status,
        last_error_code="web_structure_changed",
    )
    assert _project_web_rule_health(source, state, job, agent_unavailable=unavailable) == (
        SyncPhase.DEGRADED,
        code,
    )


@pytest.mark.parametrize("previous_error", [None, "web_rule_required", "web_rule_authoring_failed"])
def test_pending_feed_probe_does_not_project_rule_agent_failure(previous_error):
    source, state = source_state(has_rule=False, episode=uuid4())
    source.config.pop("web_feed_discovery")
    state.last_error_code = previous_error
    state.phase = SyncPhase.CATCHING_UP
    assert _project_web_rule_health(source, state, None, agent_unavailable=True) == (
        SyncPhase.CATCHING_UP,
        "web_feed_discovering",
    )


def test_retrying_feed_probe_preserves_network_sync_error():
    source, state = source_state(has_rule=False)
    source.config["web_feed_discovery"]["status"] = "retry"
    state.phase, state.last_error_code = SyncPhase.DEGRADED, "network_error"
    assert _project_web_rule_health(source, state, None, agent_unavailable=True) == (
        SyncPhase.DEGRADED,
        "network_error",
    )


@pytest.mark.parametrize("last_error", [None, "network_error", "web_rule_required"])
def test_discovered_feed_health_ignores_historical_rule_episode(last_error):
    source, state = source_state(has_rule=False, episode=uuid4())
    source.config["web_feed_discovery"].update(
        status="found", feed_url="https://example.com/feed.xml"
    )
    state.last_error_code = last_error
    state.phase = SyncPhase.DEGRADED if last_error else SyncPhase.IDLE
    expected = (
        (SyncPhase.DEGRADED, "network_error")
        if last_error == "network_error"
        else (SyncPhase.IDLE, None)
    )
    assert _project_web_rule_health(source, state, None, agent_unavailable=True) == expected


@pytest.mark.parametrize("status", ["found", "retry", None])
async def test_feed_sources_do_not_query_agent_runtime_or_historical_jobs(status):
    source, state = source_state(has_rule=False, episode=uuid4())
    if status:
        source.config["web_feed_discovery"].update(
            status=status, feed_url="https://example.com/feed.xml"
        )
    else:
        source.config.pop("web_feed_discovery")
    session = AsyncMock()
    projected = await _web_rule_health_projections(session, [(source, state)])
    assert source.id in projected
    session.get.assert_not_awaited()
    session.scalars.assert_not_awaited()


@pytest.mark.parametrize("has_usable_article", [True, False])
async def test_repeated_usable_head_missing_fields_never_queues_repair(
    monkeypatch, has_usable_article
):
    source, state = source_state(episode=uuid4())
    enqueue = AsyncMock()
    monkeypatch.setattr("app.workers.sync.ensure_web_rule_job", enqueue)

    async def fetch(_client, url, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            text=(
                "User-agent: *\nAllow: /"
                if url.endswith("robots.txt")
                else (
                    '<article><a href="/good"><h2>Valid article</h2></a></article>'
                    if has_usable_article
                    else ""
                )
                + '<article><a href="/missing-title"></a></article>'
            ),
        )

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    async with httpx.AsyncClient() as client:
        for _ in range(3):
            adapter = WebBlogSourceAdapter(
                WebBlogSourceConfig(url=URL, rule=parse_web_rule(RULE, source_url=URL)), client
            )
            if not has_usable_article:
                with pytest.raises(SourceScanError) as error:
                    await adapter.scan_page(SourceScanRequest(conditional=False))
                assert error.value.code == "web_rule_missing_fields"
                continue
            page = await adapter.scan_page(SourceScanRequest(conditional=False))
            assert page.items and page.warning_code == "web_rule_partial_parse"
            await _record_web_structure_health(
                AsyncMock(),
                source,
                state,
                code=page.warning_code,
                evidence=page.web_listing_evidence,
                now=datetime.now(UTC),
            )
    enqueue.assert_not_awaited()
    if has_usable_article:
        assert state.web_rule_structural_failures == 0 and state.web_rule_failure_episode_id is None
