from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.enums import (
    CandidateStatus,
    ContentKind,
    ExtractionStatus,
    MediaType,
    SourceKind,
    SourceStatus,
    SyncPhase,
    TranslationPriority,
    TranslationPurpose,
)
from app.ingestion.candidate_queue import CandidateUpsertResult
from app.ingestion.models import (
    DiscoveredContent,
    DiscoveredMedia,
    SourceScanError,
    SourceScanPage,
)
from app.ingestion.url_safety import PublicAsyncClient
from app.workers import candidates, sync
from app.workers.candidates import (
    _enqueue_default_title_translation,
    _get_or_create_content,
    _process_candidate,
    _resolved_extraction_status,
    _sanitize_html,
    _synchronise_media,
    claim_candidates,
)


def test_refresh_ranges_are_channel_specific() -> None:
    idle = SimpleNamespace(phase=SyncPhase.IDLE, committed_checkpoint={})
    quiet = SimpleNamespace(phase=SyncPhase.IDLE, committed_checkpoint={"unchanged_runs": 3})
    busy = SimpleNamespace(
        phase=SyncPhase.IDLE,
        committed_checkpoint={"last_change_count": 12},
    )

    assert sync._normal_interval_minutes(SimpleNamespace(kind=SourceKind.RSS), idle) == 60
    assert sync._normal_interval_minutes(SimpleNamespace(kind=SourceKind.RSS), quiet) == 120
    assert sync._normal_interval_minutes(SimpleNamespace(kind=SourceKind.YOUTUBE), quiet) == 120
    assert sync._normal_interval_minutes(SimpleNamespace(kind=SourceKind.WEB), quiet) == 240
    assert sync._normal_interval_minutes(SimpleNamespace(kind=SourceKind.X), idle) == 120
    assert sync._normal_interval_minutes(SimpleNamespace(kind=SourceKind.RSS), busy) == 30
    assert sync._normal_interval_minutes(SimpleNamespace(kind=SourceKind.WEB), busy) == 60


@pytest.mark.asyncio
async def test_only_operator_configured_scweet_uses_the_trusted_service_client() -> None:
    settings = SimpleNamespace(x_provider="scweet")
    scweet_client = sync._build_source_http_client(
        SimpleNamespace(kind=SourceKind.X),
        settings,
    )
    rss_client = sync._build_source_http_client(
        SimpleNamespace(kind=SourceKind.RSS),
        settings,
    )
    try:
        assert not isinstance(scweet_client, PublicAsyncClient)
        assert isinstance(rss_client, PublicAsyncClient)
    finally:
        await scweet_client.aclose()
        await rss_client.aclose()


def test_retry_policy_uses_short_backoff_retry_after_and_long_lived_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sync.random, "uniform", lambda *_args: 1.0)

    assert sync._retry_delay(SourceScanError("temporary", "x"), 1) == timedelta(minutes=2)
    assert sync._retry_delay(SourceScanError("temporary", "x"), 5) == timedelta(hours=6)
    assert sync._retry_delay(SourceScanError("rate", "x", retry_after_seconds=900), 1) == timedelta(
        minutes=15
    )
    assert sync._retry_delay(SourceScanError("auth", "x", long_lived=True), 1) == timedelta(
        hours=24
    )


@pytest.mark.asyncio
async def test_source_claim_query_uses_skip_locked_and_commits_before_http() -> None:
    session = AsyncMock()
    session.scalars.return_value = []

    claims = await sync.claim_due_sources(session, limit=20)

    assert claims == []
    statement = session.scalars.await_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF feed_sources SKIP LOCKED" in compiled
    assert "source_sync_states.next_scan_at" in compiled
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_due_source_claims_are_bounded_by_configured_concurrency(monkeypatch) -> None:
    session = AsyncMock()
    observed_limits: list[int] = []

    @asynccontextmanager
    async def session_factory():
        yield session

    async def claim(_session, *, limit):
        observed_limits.append(limit)
        return []

    monkeypatch.setattr(
        sync,
        "get_settings",
        lambda: SimpleNamespace(ingestion_source_concurrency=3),
    )
    monkeypatch.setattr(sync, "claim_due_sources", claim)

    assert await sync.sync_due_sources(session_factory, limit=20) == 0
    assert observed_limits == [3]


@pytest.mark.asyncio
async def test_candidate_claim_query_locks_only_the_candidate_table() -> None:
    session = AsyncMock()
    session.scalars.return_value = []

    claims = await claim_candidates(session, limit=50)

    assert claims == []
    statement = session.scalars.await_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "row_number() OVER" in compiled
    assert "FOR UPDATE OF ingestion_candidates SKIP LOCKED" in compiled
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_degraded_source_still_resumes_its_durable_continuation(monkeypatch) -> None:
    source = SimpleNamespace(kind=SourceKind.RSS)
    state = SimpleNamespace(
        initial_sync_completed=True,
        phase=SyncPhase.DEGRADED,
        continuation={"tasks": [{"cursor": {"next": "page-2"}}]},
    )
    claim = sync.SourceClaim(uuid4(), uuid4())
    catch_up = AsyncMock()
    head_scan = AsyncMock()
    monkeypatch.setattr(
        sync,
        "get_settings",
        lambda: SimpleNamespace(ingestion_source_timeout_seconds=120),
    )
    monkeypatch.setattr(sync, "_start_run", AsyncMock(return_value=(source, state, uuid4())))
    monkeypatch.setattr(sync, "_build_adapter", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(sync, "_run_catch_up", catch_up)
    monkeypatch.setattr(sync, "_run_head_scan", head_scan)

    await sync.scan_claimed_source(object(), claim)

    catch_up.assert_awaited_once()
    head_scan.assert_not_awaited()


@pytest.mark.asyncio
async def test_youtube_soft_quota_is_reserved_atomically(monkeypatch) -> None:
    session = AsyncMock()
    session.scalar.return_value = 4

    @asynccontextmanager
    async def session_factory():
        yield session

    monkeypatch.setattr(
        sync,
        "get_settings",
        lambda: SimpleNamespace(
            youtube_data_api_key=object(), youtube_daily_quota_soft_limit=8_000
        ),
    )

    assert await sync._reserve_youtube_quota(session_factory) is True
    statement = session.scalar.await_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (provider, usage_date) DO UPDATE" in compiled
    assert "provider_quota_usage.units +" in compiled
    session.commit.assert_awaited_once()


def test_candidate_cap_replays_the_same_page_instead_of_advancing_cursor() -> None:
    page = SourceScanPage(
        items=(),
        provider_mode="rss",
        raw_items=50,
        completed=False,
        next_continuation={"next": "page-2"},
    )
    input_cursor = {"current": "page-1"}
    outcome = CandidateUpsertResult(changed=40, duplicates=0, processed_items=40, cap_reached=True)

    assert sync._cursor_for_incomplete_page(input_cursor, page, outcome) == input_cursor


def test_extraction_status_never_claims_a_nonexistent_future_worker() -> None:
    assert (
        _resolved_extraction_status(ContentKind.ARTICLE, "<p>Extracted article.</p>")
        == ExtractionStatus.SUCCESS
    )
    assert _resolved_extraction_status(ContentKind.ARTICLE, None) == ExtractionStatus.FAILED
    assert _resolved_extraction_status(ContentKind.POST, None) == ExtractionStatus.NOT_NEEDED


def test_nested_fallback_checkpoint_hashes_are_available_for_deduplication() -> None:
    checkpoint = {
        "data_api": {"item_hashes": {"video": "hash-a"}},
        "atom": {"item_hashes": {"other": "hash-b"}},
    }

    assert sync._checkpoint_hashes(checkpoint) == {
        "video": "hash-a",
        "other": "hash-b",
    }


def test_authoritative_checkpoint_commit_preserves_newer_atom_progress() -> None:
    authoritative = {
        "data_api": {"head_ids": ["new-api-head"]},
        "atom": {"head_ids": ["old-atom-head"]},
    }
    durable_fallback = {
        "data_api": {"head_ids": ["old-api-head"]},
        "atom": {"head_ids": ["new-atom-head"]},
    }

    assert sync._merge_fallback_checkpoint(authoritative, durable_fallback) == {
        "data_api": {"head_ids": ["new-api-head"]},
        "atom": {"head_ids": ["new-atom-head"]},
    }


@pytest.mark.asyncio
async def test_content_persistence_uses_url_hash_upsert() -> None:
    session = AsyncMock()
    stored_content = SimpleNamespace(id=uuid4())
    session.scalar.return_value = stored_content
    item = DiscoveredContent(
        native_id="entry-1",
        kind=ContentKind.ARTICLE,
        title="Same URL",
        external_url="https://example.com/article",
        published_at=None,
    )

    source_id = uuid4()
    result = await _get_or_create_content(session, item, source_id=source_id)

    assert result is stored_content
    statement = session.execute.await_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert (
        "ON CONFLICT (authority_source_id, url_hash) WHERE url_hash IS NOT NULL DO NOTHING"
    ) in compiled
    assert "authority_source_id" in compiled
    parameters = statement.compile(dialect=postgresql.dialect()).params
    assert parameters["authority_source_id"] == source_id


@pytest.mark.asyncio
async def test_non_authoritative_source_cannot_overwrite_shared_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_source_id = uuid4()
    attacker_source_id = uuid4()
    content_id = uuid4()
    entry = SimpleNamespace(
        content_id=content_id,
        external_url="https://example.com/original",
        title="Original entry title",
        author_name=None,
        published_at=None,
        raw_metadata={},
    )
    content = SimpleNamespace(
        id=content_id,
        authority_source_id=authority_source_id,
        kind=ContentKind.ARTICLE,
        canonical_url="https://example.com/original",
        url_hash="trusted-hash",
        title="Trusted title",
        author_name="Trusted author",
        published_at=None,
        excerpt="Trusted excerpt",
        body_html="<p>Trusted body</p>",
        body_text="Trusted body",
        content_hash="trusted-content-hash",
        extraction_status=ExtractionStatus.PENDING,
    )
    source = SimpleNamespace(status=SourceStatus.ACTIVE)
    session = AsyncMock()
    session.scalar.side_effect = [entry, content]
    session.get.return_value = source
    sync_media = AsyncMock()
    enqueue_translation = AsyncMock()
    monkeypatch.setattr(candidates, "_synchronise_media", sync_media)
    monkeypatch.setattr(candidates, "_enqueue_default_title_translation", enqueue_translation)

    item = DiscoveredContent(
        native_id="attacker-entry",
        kind=ContentKind.POST,
        title="Poisoned title",
        external_url="https://example.com/original",
        published_at=None,
        author_name="Attacker",
        excerpt_html="<p>Poisoned body</p>",
    )
    candidate = SimpleNamespace(
        payload=item.to_payload(),
        payload_hash="poisoned-content-hash",
        source_id=attacker_source_id,
        native_id=item.native_id,
        suggested_feed_sort_at=datetime.now(UTC),
        content_id=None,
        status=CandidateStatus.PROCESSING,
        completed_at=None,
        lease_token=uuid4(),
        lease_expires_at=None,
        last_error_code=None,
        last_error_message=None,
    )

    await _process_candidate(session, candidate)

    assert entry.title == "Poisoned title"
    assert content.title == "Trusted title"
    assert content.body_text == "Trusted body"
    assert content.content_hash == "trusted-content-hash"
    sync_media.assert_not_awaited()
    enqueue_translation.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_sync_deactivates_removed_assets_and_upserts_current_set() -> None:
    session = AsyncMock()
    content = SimpleNamespace(id=uuid4())
    item = DiscoveredContent(
        native_id="entry-1",
        kind=ContentKind.ARTICLE,
        title="With media",
        external_url="https://example.com/article",
        published_at=None,
        media=(
            DiscoveredMedia(
                original_url="https://cdn.example.com/image.jpg",
                media_type=MediaType.IMAGE,
            ),
        ),
    )

    await _synchronise_media(session, content, item)

    assert session.execute.await_count == 2
    upsert = session.execute.await_args_list[1].args[0]
    compiled = str(upsert.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (content_id, original_url) DO UPDATE" in compiled
    assert "is_active" in compiled


@pytest.mark.asyncio
async def test_candidate_title_creates_ingestion_demand_without_cache_invalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    content = SimpleNamespace(id=uuid4(), title="Edited title")
    ensure = AsyncMock(return_value=1)
    engine = SimpleNamespace(provider_name="deepseek", model_name="deepseek-v4-flash")
    monkeypatch.setattr(candidates, "default_translation_engine", lambda: engine)
    monkeypatch.setattr(candidates, "ensure_translation_work", ensure)
    monkeypatch.setattr(
        candidates,
        "get_settings",
        lambda: SimpleNamespace(translation_default_target_locale="zh-CN"),
    )

    await _enqueue_default_title_translation(session, content)

    ensure.assert_awaited_once()
    demand = ensure.await_args.args[1][0]
    assert demand.item_id == str(content.id)
    assert demand.text == "Edited title"
    assert demand.purpose == TranslationPurpose.TITLE
    assert demand.priority == int(TranslationPriority.INGESTION)
    assert ensure.await_args.kwargs["engine"] is engine


@pytest.mark.asyncio
async def test_unavailable_baseline_video_does_not_create_a_home_entry() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    item = DiscoveredContent(
        native_id="private-video",
        kind=ContentKind.VIDEO,
        title="Unavailable video",
        external_url="https://www.youtube.com/watch?v=private-video",
        published_at=None,
        raw_metadata={"availability": "unavailable"},
    )
    candidate = SimpleNamespace(
        payload=item.to_payload(),
        source_id=uuid4(),
        native_id=item.native_id,
        content_id=None,
        status=CandidateStatus.PROCESSING,
        completed_at=None,
        lease_token=uuid4(),
        lease_expires_at=None,
        last_error_code=None,
        last_error_message=None,
    )

    await _process_candidate(session, candidate)

    assert candidate.status == CandidateStatus.COMPLETED
    assert candidate.content_id is None
    session.add.assert_not_called()


def test_candidate_html_sanitizer_removes_active_content() -> None:
    unsafe = (
        '<p onclick="steal()">Safe text</p><script>steal()</script>'
        '<a href="javascript:steal()">bad link</a>'
        '<img src="https://cdn.example/image.jpg" onerror="steal()">'
    )

    cleaned = _sanitize_html(unsafe)

    assert cleaned is not None
    assert "Safe text" in cleaned
    assert "script" not in cleaned
    assert "onclick" not in cleaned
    assert "javascript:" not in cleaned
    assert "onerror" not in cleaned
    assert 'src="https://cdn.example/image.jpg"' in cleaned
