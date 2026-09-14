"""Feed discovery, scanner leases and rule lifecycle on guarded PostgreSQL."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from test_web_rule_jobs_postgres import ENGINE, _call, _job_database

from app.core.settings import Settings
from app.domain.enums import SourceKind, SyncPhase, SyncRunStatus
from app.ingestion.feed_discovery import FeedDiscoveryResult
from app.ingestion.source_identity import canonical_web_identity, web_article_native_id
from app.services import web_rule_jobs as jobs
from app.services.subscriptions import subscribe_to_shared_source
from app.services.web_feeds import save_web_feed_discovery, schedule_web_feed_probes
from app.storage.models import (
    Content,
    FeedSource,
    SourceSubscription,
    SourceSyncRun,
    SourceSyncState,
    WebRuleAgentRuntime,
    WebRuleJob,
)
from app.translation.engines import engine_descriptor
from app.web_rule_agent.configuration import rule_agent_snapshot
from app.workers import candidates, sync
from app.workers.web_rules import run_web_rule_author_once

URL = "https://example.com/blog"
FEED_URL = "https://example.com/blog/rss.xml"
PAGE = (
    '<html><head><link rel="alternate" type="application/rss+xml" '
    'href="/blog/rss.xml"></head></html>'
)


def _rss(*titles: str) -> str:
    entries = "".join(
        f"<item><guid>https://example.com/{title.lower()}</guid>"
        f"<link>https://example.com/{title.lower()}</link><title>{title}</title>"
        f"<description>{title} summary.</description></item>"
        for title in titles
    )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<title>Example</title><link>{URL}</link><description>News</description>"
        f"{entries}</channel></rss>"
    )


@asynccontextmanager
async def _web_database():
    async with _job_database() as (factory, source_id):
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            source.canonical_url = URL
            source.canonical_key = canonical_web_identity(URL).canonical_key
            source.config = {}
            # Keep this suite focused on discovery and feed requests, with no
            # additional site-icon request after every accepted RSS page.
            source.avatar_url = "https://example.com/favicon.ico"
            state = await session.get(SourceSyncState, source_id)
            state.next_scan_at = None
            state.phase = SyncPhase.IDLE
            state.last_error_code = "web_rule_required"
            state.initial_sync_completed = False
            subscription = await session.scalar(
                select(SourceSubscription).where(SourceSubscription.source_id == source_id)
            )
            user_id = subscription.user_id
            await session.commit()
        yield factory, source_id, user_id


def _controlled_http(monkeypatch, responses):
    requests = []

    async def fetch(_client, url, **kwargs):
        requests.append(url)
        status, body = responses.get(url, (404, "Not found"))
        return httpx.Response(status, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.ingestion.feed_discovery.safe_get", fetch)
    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fetch)
    return requests


async def _claim_scan(factory, source_id, *, reschedule=False):
    if reschedule:
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            state.next_scan_at = datetime.now(UTC)
            await session.commit()
    async with factory() as session:
        claims = await sync.claim_due_sources(session, limit=100)
    return next(claim for claim in claims if claim.source_id == source_id)


async def _assert_no_jobs(factory, source_id):
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WebRuleJob)
                .where(WebRuleJob.source_id == source_id)
            )
            == 0
        )


@pytest.mark.postgres
async def test_feed_selection_keeps_url_anchors_to_report_unreachable_history(monkeypatch):
    _controlled_http(monkeypatch, {URL: (200, PAGE), FEED_URL: (200, _rss("One"))})
    async with _web_database() as (factory, source_id, _):
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            state.initial_sync_completed = True
            state.committed_checkpoint = {
                "head_ids": [web_article_native_id("https://example.com/older")],
                "validators": {"etag": "old-web-etag"},
            }
            await session.commit()
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.initial_sync_completed and state.provider_mode == "web_rss"
            assert state.gap_detected
            assert "etag" not in state.committed_checkpoint.get("validators", {})
        await _assert_no_jobs(factory, source_id)


@pytest.mark.postgres
@pytest.mark.parametrize("agent_mode", ["disabled", "paused"])
@pytest.mark.parametrize("initial_empty", [False, True])
async def test_subscribe_prefers_rss_and_syncs_updates_without_agent(
    monkeypatch,
    agent_mode,
    initial_empty,
):
    responses = {URL: (200, PAGE), FEED_URL: (200, _rss() if initial_empty else _rss("One"))}
    requests = _controlled_http(monkeypatch, responses)
    monkeypatch.setattr(candidates, "_enqueue_default_title_translation", AsyncMock())
    settings = Settings(
        _env_file=None,
        web_rule_agent_engine_id="disabled" if agent_mode == "disabled" else "deepseek-v4-flash",
        deepseek_api_key="test-only-never-used",
    )
    monkeypatch.setattr(sync, "get_settings", lambda: settings)
    model_factory = Mock(side_effect=AssertionError("RSS must never call the rule model"))
    async with _web_database() as (factory, source_id, user_id):
        if agent_mode == "paused":
            descriptor = engine_descriptor(settings, settings.web_rule_agent_engine_id)
            async with factory() as session:
                runtime = await session.get(WebRuleAgentRuntime, "default")
                runtime.engine_snapshot = rule_agent_snapshot(settings, descriptor)
                runtime.paused_code = "provider_unavailable"
                runtime.paused_at = datetime.now(UTC)
                runtime.resume_at = None
                await session.commit()
        async with factory() as session:
            source, _, state, created = await subscribe_to_shared_source(
                session,
                user_id=user_id,
                identity=canonical_web_identity(URL),
                display_name="Example",
                folder_name=None,
            )
            assert source.id == source_id and not created
            assert state.next_scan_at is not None
            assert state.last_error_code == "web_feed_discovering"
        assert (
            await run_web_rule_author_once(
                factory,
                settings=settings,
                model_factory=model_factory,
            )
            == 0
        )
        await _assert_no_jobs(factory, source_id)
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        await candidates.process_candidates(factory)
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            state = await session.get(SourceSyncState, source_id)
            assert source.kind == SourceKind.WEB and source.canonical_url == URL
            assert source.config["web_feed_discovery"]["status"] == "found"
            assert source.config["web_feed_discovery"]["feed_url"] == FEED_URL
            assert state.provider_mode == "web_rss"
            assert state.initial_sync_completed and state.last_error_code is None
            assert set(
                await session.scalars(
                    select(Content.title).where(Content.authority_source_id == source_id)
                )
            ) == (set() if initial_empty else {"One"})
        responses[FEED_URL] = (200, _rss("Two", "One"))
        await sync.scan_claimed_source(
            factory,
            await _claim_scan(factory, source_id, reschedule=True),
        )
        await candidates.process_candidates(factory)
        async with factory() as session:
            assert set(
                await session.scalars(
                    select(Content.title).where(Content.authority_source_id == source_id)
                )
            ) == {"One", "Two"}
            source = await session.get(FeedSource, source_id)
            assert source.id == source_id and source.kind == SourceKind.WEB
        assert requests.count(URL) == 1  # No repeat discovery during normal sync.
        assert requests.count(FEED_URL) == 3  # Validate, initial scan, next scan.
        await _assert_no_jobs(factory, source_id)
        model_factory.assert_not_called()


@pytest.mark.postgres
async def test_no_feed_creates_one_author_job_and_does_not_reset_terminal_budget(monkeypatch):
    requests = _controlled_http(monkeypatch, {URL: (200, "<html><body>News</body></html>")})
    async with _web_database() as (factory, source_id, user_id):
        assert await _call(factory, jobs.ensure_web_rule_job, source_id=source_id) is None
        assert await _call(factory, jobs.backfill_web_rule_jobs) == 0
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            state = await session.get(SourceSyncState, source_id)
            assert source.config["web_feed_discovery"]["status"] == "not_found"
            assert state.next_scan_at is None and state.last_error_code == "web_rule_required"
            job = await session.scalar(select(WebRuleJob).where(WebRuleJob.source_id == source_id))
            assert job.status == "queued"
            job_id = job.id
        claim = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            limits=jobs.JobLimits(),
        )
        reservation = await _call(
            factory,
            jobs.reserve_model_call,
            claim=claim,
            estimated_tokens=111,
        )
        await _call(
            factory,
            jobs.record_model_usage,
            claim=claim,
            reservation_id=reservation,
            usage={"input_tokens": 50, "output_tokens": 11, "total_tokens": 61},
        )
        await _call(
            factory, jobs.finish_web_rule_job, claim=claim, status="failed", code="test_failure"
        )
        async with factory() as session:
            await subscribe_to_shared_source(
                session,
                user_id=user_id,
                identity=canonical_web_identity(URL),
                display_name=None,
                folder_name=None,
            )
        assert await _call(factory, jobs.backfill_web_rule_jobs) == 0
        again = await _call(factory, jobs.ensure_web_rule_job, source_id=source_id)
        assert again.id == job_id and again.status == "failed"
        assert again.model_calls_reserved == 1 and again.total_tokens == 61
        assert again.deadline_at == claim.deadline_at
        assert requests.count(URL) == 1
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(WebRuleJob)
                    .where(WebRuleJob.source_id == source_id)
                )
                == 1
            )


@pytest.mark.postgres
async def test_temporary_discovery_failure_retries_without_authoring(monkeypatch):
    requests = _controlled_http(monkeypatch, {URL: (200, PAGE), FEED_URL: (503, "Try later")})
    async with _web_database() as (factory, source_id, _):
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            state = await session.get(SourceSyncState, source_id)
            assert source.config["web_feed_discovery"]["status"] == "retry"
            assert state.last_error_code == "web_feed_discovery_retry"
            assert state.next_scan_at > datetime.now(UTC)
            assert state.lease_token is None
        assert await _call(factory, jobs.backfill_web_rule_jobs) == 0
        assert await _call(factory, jobs.ensure_web_rule_job, source_id=source_id) is None
        await _assert_no_jobs(factory, source_id)
        assert requests[:2] == [URL, FEED_URL]
        assert len(requests) == 7


@pytest.mark.postgres
@pytest.mark.parametrize("active_status", ["queued", "running", "retry_wait"])
async def test_feed_selection_cancels_active_job_and_preserves_terminal_history(
    monkeypatch,
    active_status,
):
    _controlled_http(monkeypatch, {URL: (200, PAGE), FEED_URL: (200, _rss("One"))})
    async with _web_database() as (factory, source_id, _):
        async with factory() as session:
            terminal = WebRuleJob(
                id=uuid4(),
                source_id=source_id,
                source_url=URL,
                reason="author",
                idempotency_key=f"historical:{uuid4()}",
                status="failed",
                model_calls_reserved=2,
                total_tokens=25,
                last_error_code="old_failure",
                finished_at=datetime.now(UTC),
            )
            active = WebRuleJob(
                id=uuid4(),
                source_id=source_id,
                source_url=URL,
                reason="author",
                idempotency_key=f"active:{uuid4()}",
                status=active_status,
                model_calls_reserved=3,
                total_tokens=90,
                lease_token=uuid4() if active_status == "running" else None,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=1)
                if active_status == "running"
                else None,
            )
            session.add_all([terminal, active])
            await session.commit()
            terminal_id, active_id = terminal.id, active.id
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        async with factory() as session:
            terminal = await session.get(WebRuleJob, terminal_id)
            active = await session.get(WebRuleJob, active_id)
            assert (terminal.status, terminal.last_error_code) == ("failed", "old_failure")
            assert terminal.model_calls_reserved == 2 and terminal.total_tokens == 25
            assert (active.status, active.last_error_code) == ("cancelled", "web_feed_selected")
            assert active.model_calls_reserved == 3 and active.total_tokens == 90
            assert active.lease_token is None and active.validation_receipt is None
            assert active.finished_at is not None
            assert (await session.get(FeedSource, source_id)).config["web_rule_revision"] == 1


@pytest.mark.postgres
@pytest.mark.parametrize("changed", ["lease", "lease_expired", "revision", "url", "disabled"])
async def test_stale_discovery_result_cannot_replace_current_source_state(changed):
    async with _web_database() as (factory, source_id, _):
        claim = await _claim_scan(factory, source_id)
        source, _, run_id = await sync._start_run(factory, claim)
        assert source is not None and run_id is not None
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            state = await session.get(SourceSyncState, source_id)
            if changed == "lease":
                state.lease_token = uuid4()
            elif changed == "lease_expired":
                state.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif changed == "revision":
                source.config = {"web_rule_revision": 1}
            elif changed == "url":
                source.canonical_url = "https://example.com/other"
            else:
                subscription = await session.scalar(
                    select(SourceSubscription).where(SourceSubscription.source_id == source_id)
                )
                subscription.is_enabled = False
            expected_config, expected_lease = dict(source.config), state.lease_token
            await session.commit()
        result = await save_web_feed_discovery(
            factory,
            source_id=source_id,
            lease_token=claim.lease_token,
            run_id=run_id,
            source_url=URL,
            rule_revision=0,
            result=FeedDiscoveryResult("found", FEED_URL, {"requests": 2}),
        )
        assert result is None
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            state = await session.get(SourceSyncState, source_id)
            assert source.config == expected_config
            if changed == "disabled":
                assert state.lease_token is None
                run = await session.get(SourceSyncRun, run_id)
                assert run.status == SyncRunStatus.PARTIAL and run.error_code == "source_inactive"
            else:
                assert state.lease_token == expected_lease
        await _assert_no_jobs(factory, source_id)


@pytest.mark.postgres
async def test_claim_wakes_migrated_missing_rule_source_without_scheduled_scan():
    async with _web_database() as (factory, source_id, _):
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.next_scan_at is None and state.last_error_code == "web_rule_required"
        claim = await _claim_scan(factory, source_id)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert state.next_scan_at is not None
            assert state.lease_token == claim.lease_token
            assert state.last_error_code == "web_feed_discovering"
        await _assert_no_jobs(factory, source_id)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "failed_response",
    [
        (404, "Not found"),
        (410, "Gone"),
        (200, "<html><body>Moved</body></html>"),
        (200, '<rss version="2.0"><channel><item><link>/x</link></item></channel></rss>'),
    ],
    ids=["404", "410", "invalid_feed", "missing_article_fields"],
)
async def test_broken_selected_feed_is_rediscovered_and_new_feed_continues_updates(
    monkeypatch,
    failed_response,
):
    replacement_feed = "https://example.com/blog/rss-v2.xml"
    responses = {URL: (200, PAGE), FEED_URL: (200, _rss("One"))}
    requests = _controlled_http(monkeypatch, responses)
    monkeypatch.setattr(candidates, "_enqueue_default_title_translation", AsyncMock())
    async with _web_database() as (factory, source_id, _):
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        await candidates.process_candidates(factory)
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            state.committed_checkpoint = {
                **state.committed_checkpoint,
                "validators": {"etag": "old-feed-etag", "last_modified": "old-feed-date"},
            }
            state.pending_checkpoint = {"validators": {"etag": "old-pending-etag"}}
            await session.commit()
        responses[FEED_URL] = failed_response
        responses[URL] = (200, PAGE.replace("/blog/rss.xml", "/blog/rss-v2.xml"))
        responses[replacement_feed] = (200, _rss("Two", "One"))
        for failure_count in (1, 2):
            started = datetime.now(UTC)
            await sync.scan_claimed_source(
                factory,
                await _claim_scan(factory, source_id, reschedule=True),
            )
            async with factory() as session:
                source = await session.get(FeedSource, source_id)
                state = await session.get(SourceSyncState, source_id)
                assert source.config["web_feed_discovery"]["status"] == (
                    "found" if failure_count == 1 else "retry"
                )
                assert state.initial_sync_completed
                if failure_count == 2:
                    assert started + timedelta(seconds=100) <= state.next_scan_at
                    assert state.next_scan_at <= datetime.now(UTC) + timedelta(seconds=140)
                    assert not state.committed_checkpoint.get("validators")
                    assert not state.pending_checkpoint.get("validators")
                    assert state.continuation is None
                    assert state.lease_token is None
        assert requests.count(URL) == 1
        await _assert_no_jobs(factory, source_id)
        await sync.scan_claimed_source(
            factory,
            await _claim_scan(factory, source_id, reschedule=True),
        )
        await candidates.process_candidates(factory)
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            state = await session.get(SourceSyncState, source_id)
            assert source.kind == SourceKind.WEB and source.canonical_url == URL
            assert source.config["web_feed_discovery"]["status"] == "found"
            assert source.config["web_feed_discovery"]["feed_url"] == replacement_feed
            assert state.provider_mode == "web_rss" and state.last_error_code is None
            titles = list(
                await session.scalars(
                    select(Content.title).where(Content.authority_source_id == source_id)
                )
            )
            assert sorted(titles) == ["One", "Two"]
        assert requests.count(URL) == 2
        assert requests.count(replacement_feed) == 2
        await _assert_no_jobs(factory, source_id)


@pytest.mark.postgres
async def test_selected_feed_temporary_503_does_not_trigger_rediscovery(monkeypatch):
    responses = {URL: (200, PAGE), FEED_URL: (200, _rss("One"))}
    requests = _controlled_http(monkeypatch, responses)
    async with _web_database() as (factory, source_id, _):
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        responses[FEED_URL] = (503, "Try later")
        for _ in range(3):
            await sync.scan_claimed_source(
                factory,
                await _claim_scan(factory, source_id, reschedule=True),
            )
            async with factory() as session:
                source = await session.get(FeedSource, source_id)
                state = await session.get(SourceSyncState, source_id)
                assert source.config["web_feed_discovery"]["status"] == "found"
                assert source.config["web_feed_discovery"]["feed_url"] == FEED_URL
                assert state.last_error_code == "web_rss_http_503"
                assert state.next_scan_at is not None
        assert requests.count(URL) == 1
        await _assert_no_jobs(factory, source_id)


@pytest.mark.postgres
async def test_successful_feed_sync_breaks_consecutive_structure_failure_sequence(monkeypatch):
    responses = {URL: (200, PAGE), FEED_URL: (200, _rss("One"))}
    requests = _controlled_http(monkeypatch, responses)
    async with _web_database() as (factory, source_id, _):
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        for response in ((404, "Not found"), (200, _rss("One")), (404, "Not found")):
            responses[FEED_URL] = response
            await sync.scan_claimed_source(
                factory,
                await _claim_scan(factory, source_id, reschedule=True),
            )
            async with factory() as session:
                source = await session.get(FeedSource, source_id)
                assert source.config["web_feed_discovery"]["status"] == "found"
        await sync.scan_claimed_source(
            factory,
            await _claim_scan(factory, source_id, reschedule=True),
        )
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            assert source.config["web_feed_discovery"]["status"] == "retry"
        assert requests.count(URL) == 1
        await _assert_no_jobs(factory, source_id)


@pytest.mark.postgres
async def test_page_403_does_not_prevent_independent_feed_sync(monkeypatch):
    requests = _controlled_http(monkeypatch, {URL: (403, "Denied"), FEED_URL: (200, _rss("One"))})
    async with _web_database() as (factory, source_id, _):
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            state = await session.get(SourceSyncState, source_id)
            assert source.config["web_feed_discovery"]["status"] == "found"
            assert source.config["web_feed_discovery"]["feed_url"] == FEED_URL
            assert state.provider_mode == "web_rss"
            assert state.initial_sync_completed and state.last_error_code is None
        assert requests[:3] == [URL, URL + "/feed", FEED_URL]
        await _assert_no_jobs(factory, source_id)


@pytest.mark.postgres
@pytest.mark.parametrize("denied_endpoint", ["page", "declared_candidate"])
async def test_denied_discovery_retries_later_without_rule_authoring(monkeypatch, denied_endpoint):
    responses = (
        {URL: (403, "Denied")}
        if denied_endpoint == "page"
        else {URL: (200, PAGE), FEED_URL: (403, "Denied")}
    )
    requests = _controlled_http(monkeypatch, responses)
    async with _web_database() as (factory, source_id, _):
        started = datetime.now(UTC)
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            state = await session.get(SourceSyncState, source_id)
            expected_code = "web_http_403" if denied_endpoint == "page" else "web_rss_http_403"
            record = source.config["web_feed_discovery"]
            assert record["status"] == "blocked"
            assert record["evidence"]["error_code"] == expected_code
            assert state.last_error_code == expected_code
            assert started + timedelta(hours=24) <= state.next_scan_at
            assert state.next_scan_at <= datetime.now(UTC) + timedelta(hours=24)
            assert state.lease_token is None
            assert state.consecutive_failures == 1
        assert len(requests) == 7
        assert await _call(factory, jobs.backfill_web_rule_jobs) == 0
        assert await _call(factory, jobs.ensure_web_rule_job, source_id=source_id) is None
        assert await _call(factory, schedule_web_feed_probes) == 0
        async with factory() as session:
            assert await sync.claim_due_sources(session) == []
        await _assert_no_jobs(factory, source_id)


@pytest.mark.postgres
@pytest.mark.parametrize("feed_available", [False, True])
async def test_legacy_page_403_negative_cache_is_woken_without_resetting_terminal_job(
    monkeypatch, feed_available
):
    responses = {URL: (403, "Denied")}
    if feed_available:
        responses[FEED_URL] = (200, _rss("One"))
    requests = _controlled_http(monkeypatch, responses)
    async with _web_database() as (factory, source_id, _):
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            source.config = {
                "web_feed_discovery": {
                    "version": 1,
                    "source_url": URL,
                    "status": "not_found",
                    "feed_url": None,
                    "evidence": {
                        "requests": 1,
                        "attempts": [{"url": URL, "origin": "source", "status_code": 403}],
                    },
                }
            }
            terminal = WebRuleJob(
                id=uuid4(),
                source_id=source_id,
                source_url=URL,
                reason="author",
                idempotency_key=f"historical:{uuid4()}",
                status="failed",
                model_calls_reserved=2,
                total_tokens=25,
                last_error_code="web_crawl_failed",
                finished_at=datetime.now(UTC),
                deadline_at=datetime.now(UTC) - timedelta(hours=1),
            )
            session.add(terminal)
            await session.commit()
            job_id, deadline = terminal.id, terminal.deadline_at
        assert await _call(factory, schedule_web_feed_probes) == 1
        await sync.scan_claimed_source(factory, await _claim_scan(factory, source_id))
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            record = source.config["web_feed_discovery"]
            assert record["status"] == ("found" if feed_available else "blocked")
            terminal = await session.get(WebRuleJob, job_id)
            assert terminal.status == "failed" and terminal.last_error_code == "web_crawl_failed"
            assert terminal.model_calls_reserved == 2 and terminal.total_tokens == 25
            assert terminal.deadline_at == deadline
            assert await session.scalar(select(func.count()).select_from(WebRuleJob)) == 1
        assert requests.count(URL) == 1
        assert len(requests) > 1
        assert await _call(factory, schedule_web_feed_probes) == 0
