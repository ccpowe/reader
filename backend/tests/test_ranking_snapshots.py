from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from app.core.settings import Settings
from app.services.ranking_snapshots import (
    RankingData,
    RankingRefreshInProgress,
    RankingRefreshRateLimited,
    RankingRequest,
    _enqueue_reddit_hot_admissions,
    _provider_io_timeout_seconds,
    _record_refresh_failure,
    _refresh_one_snapshot,
    _reserve_provider_refresh,
    _retry_interval,
    _snapshot_item_limit,
    create_or_refresh_snapshot,
    ensure_reddit_snapshots,
    reconcile_reddit_snapshots,
    refresh_interval,
    sync_due_ranking_snapshots,
)
from app.services.rankings import RankingItem


def test_reddit_snapshot_key_separates_community_and_sort() -> None:
    hot = RankingRequest(kind="reddit", subreddit="MachineLearning", sort="hot")
    top = RankingRequest(kind="reddit", subreddit="LocalLLaMA", sort="top", time_filter="week")

    assert hot.cache_key == "ranking:v2:reddit:machinelearning:hot:-"
    assert top.cache_key == "ranking:v2:reddit:localllama:top:week"
    assert hot.cache_key != top.cache_key


def test_snapshot_intervals_are_conservative_for_external_providers() -> None:
    assert refresh_interval(RankingRequest(kind="hacker_news")) == timedelta(minutes=5)
    assert refresh_interval(RankingRequest(kind="github")) == timedelta(minutes=30)
    assert refresh_interval(RankingRequest(kind="reddit", sort="hot")) == timedelta(minutes=30)
    assert refresh_interval(RankingRequest(kind="reddit", sort="rising")) == timedelta(hours=1)
    assert refresh_interval(RankingRequest(kind="reddit", sort="top")) == timedelta(hours=6)


def test_snapshot_key_is_shared_by_equivalent_reddit_input() -> None:
    first = RankingRequest(kind="reddit", subreddit="MachineLearning", sort="rising")
    second = RankingRequest(kind="reddit", subreddit="machinelearning", sort="rising")

    assert first.cache_key == second.cache_key


def test_snapshot_windows_match_each_provider_capability() -> None:
    assert _snapshot_item_limit(RankingRequest(kind="hacker_news")) == 100
    assert _snapshot_item_limit(RankingRequest(kind="reddit")) == 100
    assert _snapshot_item_limit(RankingRequest(kind="github")) == 25


def test_default_refresh_lease_covers_the_bounded_hacker_news_fanout() -> None:
    settings = Settings(_env_file=None)

    # Top stories can use 20 seconds, followed by six waves of 20 item
    # requests. Keep enough margin that another worker cannot reclaim it.
    assert settings.ranking_refresh_lease_seconds >= 180
    assert _provider_io_timeout_seconds(settings.ranking_refresh_lease_seconds) == (
        settings.ranking_refresh_lease_seconds - 30
    )


def test_ranking_retry_interval_honors_provider_retry_after() -> None:
    request = httpx.Request("GET", "https://provider.example/ranking")
    response = httpx.Response(429, headers={"Retry-After": "37"}, request=request)
    error = httpx.HTTPStatusError(
        "rate limited",
        request=request,
        response=response,
    )

    assert _retry_interval(error) == timedelta(seconds=37)


def test_ranking_rate_limit_backoff_doubles_without_retry_after() -> None:
    request = httpx.Request("GET", "https://provider.example/ranking")
    error = httpx.HTTPStatusError(
        "rate limited",
        request=request,
        response=httpx.Response(429, request=request),
    )

    assert _retry_interval(error) == timedelta(minutes=15)
    assert _retry_interval(error, consecutive_rate_limits=1) == timedelta(minutes=30)
    assert _retry_interval(error, consecutive_rate_limits=2) == timedelta(minutes=60)
    assert _retry_interval(error, consecutive_rate_limits=9) == timedelta(hours=2)


def test_reddit_refresh_budget_defaults_to_one_request_per_minute() -> None:
    settings = Settings(_env_file=None)

    assert settings.reddit_ranking_refreshes_per_minute == 1


@pytest.mark.asyncio
async def test_reddit_budget_reserves_the_next_global_slot(monkeypatch) -> None:
    now = datetime.now(UTC)
    budget = SimpleNamespace(
        provider="reddit",
        window_started_at=now,
        refresh_count=0,
        blocked_until=None,
        updated_at=now,
    )
    session = AsyncMock()
    session.scalar.return_value = budget
    monkeypatch.setattr(
        "app.services.ranking_snapshots.get_settings",
        lambda: SimpleNamespace(
            reddit_ranking_refreshes_per_minute=1,
            ranking_provider_refreshes_per_minute=30,
        ),
    )

    assert await _reserve_provider_refresh(session, "reddit", now) is None
    assert budget.blocked_until == now + timedelta(minutes=1)
    assert await _reserve_provider_refresh(session, "reddit", now) == 61


@pytest.mark.asyncio
async def test_worker_marks_shared_budget_wait_as_skipped(monkeypatch) -> None:
    session = AsyncMock()

    async def deferred(*_args, **_kwargs):
        raise RankingRefreshRateLimited(60)

    monkeypatch.setattr(
        "app.services.ranking_snapshots.create_or_refresh_snapshot",
        deferred,
    )

    result = await _refresh_one_snapshot(
        session,
        "ranking:v2:reddit:codex:hot:-",
        RankingRequest(kind="reddit", subreddit="codex"),
    )

    assert result["status"] == "skipped"
    assert result["error_code"] == "RankingRefreshRateLimited"


@pytest.mark.asyncio
async def test_late_rate_limit_blocks_provider_after_snapshot_lease_was_replaced() -> None:
    now = datetime.now(UTC)
    current_token = uuid4()
    stale_token = uuid4()
    snapshot = SimpleNamespace(
        cache_key="ranking:v2:hacker_news",
        refresh_token=current_token,
    )
    budget = SimpleNamespace(
        provider="hacker_news",
        blocked_until=None,
        updated_at=now,
    )
    session = AsyncMock()
    session.scalar.side_effect = [snapshot, budget]
    request = httpx.Request("GET", "https://provider.example/ranking")
    response = httpx.Response(429, headers={"Retry-After": "37"}, request=request)
    error = httpx.HTTPStatusError(
        "rate limited",
        request=request,
        response=response,
    )

    await _record_refresh_failure(
        session,
        RankingRequest(kind="hacker_news"),
        stale_token,
        error,
    )

    assert snapshot.refresh_token == current_token
    assert budget.blocked_until is not None
    assert budget.blocked_until >= now + timedelta(seconds=36)
    session.commit.assert_awaited_once()
    failure_lookup = session.scalar.await_args_list[0].args[0]
    selected_columns = str(failure_lookup.compile(dialect=postgresql.dialect())).partition("FROM")[
        0
    ]
    assert "ranking_snapshots.payload" not in selected_columns


@pytest.mark.asyncio
async def test_reddit_snapshot_scheduling_is_safe_under_concurrent_subscriptions() -> None:
    session = AsyncMock()

    await ensure_reddit_snapshots(session, "MachineLearning")

    assert session.execute.await_count == 3
    for call in session.execute.await_args_list:
        statement = str(call.args[0].compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT (cache_key) DO UPDATE" in statement
        assert "last_accessed_at" in statement
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_reconciles_missing_reddit_snapshots_from_durable_subscriptions() -> None:
    session = AsyncMock()

    await reconcile_reddit_snapshots(session)

    statement = str(session.execute.await_args.args[0])
    assert "reddit_snapshot_reconciliation" in statement
    assert "CROSS JOIN (VALUES" in statement
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_only_scans_current_snapshot_keys_without_loading_payloads() -> None:
    session = AsyncMock()
    session.scalars.return_value = []

    await sync_due_ranking_snapshots(session)

    statement = session.scalars.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    rendered = str(compiled)
    selected_columns = rendered.partition("FROM")[0]

    assert "ranking_snapshots.cache_key LIKE" in rendered
    assert "ranking:v2:%" in compiled.params.values()
    assert "ranking_snapshots.cache_key" in selected_columns
    assert "ranking_snapshots.kind" in selected_columns
    assert "ranking_snapshots.parameters" in selected_columns
    assert "ranking_snapshots.payload" not in selected_columns
    assert "ranking_snapshots.last_accessed_at >=" in rendered
    assert "ranking_snapshots.parameters ->>" in rendered


@pytest.mark.asyncio
async def test_worker_retires_mismatched_current_snapshot_without_refresh(monkeypatch) -> None:
    snapshot = SimpleNamespace(
        cache_key="ranking:v2:reddit:codex:hot:-",
        kind="reddit",
        parameters={"subreddit": "machinelearning", "sort": "hot", "time_filter": "week"},
    )
    session = AsyncMock()
    session.scalars.return_value = [snapshot]
    refresh = AsyncMock()
    monkeypatch.setattr("app.services.ranking_snapshots.create_or_refresh_snapshot", refresh)

    handled = await sync_due_ranking_snapshots(session)

    assert handled == 1
    refresh.assert_not_awaited()
    retirement = session.execute.await_args_list[-1].args[0]
    compiled = retirement.compile(dialect=postgresql.dialect())
    assert "DELETE FROM ranking_snapshots" in str(compiled)
    assert snapshot.cache_key in compiled.params.values()


@pytest.mark.asyncio
async def test_worker_does_not_wait_on_an_empty_valid_snapshot_set(monkeypatch) -> None:
    snapshot = SimpleNamespace(
        cache_key="ranking:v2:reddit:codex:hot:-",
        kind="reddit",
        parameters={"subreddit": "machinelearning", "sort": "hot", "time_filter": "week"},
    )
    session = AsyncMock()
    session.scalars.return_value = [snapshot]

    class SessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *_args):
            return False

    wait = AsyncMock()
    monkeypatch.setattr("app.services.ranking_snapshots.asyncio.wait", wait)

    handled = await sync_due_ranking_snapshots(session, session_factory=SessionFactory())

    assert handled == 1
    wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_commits_the_database_lease_before_provider_io(monkeypatch) -> None:
    now = datetime.now(UTC)
    snapshot = SimpleNamespace(
        cache_key="ranking:v2:hacker_news",
        kind="hacker_news",
        parameters={},
        payload={},
        fetched_at=now,
        next_refresh_at=now - timedelta(minutes=1),
        last_accessed_at=now,
        is_ready=False,
        refresh_token=None,
        refresh_expires_at=None,
        manual_refresh_after=None,
        last_error=None,
    )
    session = AsyncMock()
    provider_budget = SimpleNamespace(
        provider="hacker_news",
        window_started_at=now,
        refresh_count=0,
        blocked_until=None,
        updated_at=now,
    )
    session.scalar.side_effect = [snapshot, provider_budget, snapshot]

    async def fetch_after_commit(*_args, **_kwargs) -> RankingData:
        assert session.commit.await_count == 2
        assert snapshot.refresh_token is not None
        return RankingData(
            kind="hacker_news",
            title="Hacker News",
            subtitle="Top Stories",
            items=[],
            fetched_at=now,
        )

    monkeypatch.setattr("app.services.ranking_snapshots.fetch_live_ranking", fetch_after_commit)

    result = await create_or_refresh_snapshot(session, RankingRequest(kind="hacker_news"))

    assert result.title == "Hacker News"
    assert snapshot.is_ready is True
    assert snapshot.refresh_token is None
    assert session.commit.await_count == 3
    for call_index in (0, 2):
        statement = session.scalar.await_args_list[call_index].args[0]
        selected_columns = str(statement.compile(dialect=postgresql.dialect())).partition("FROM")[0]
        assert "ranking_snapshots.payload" not in selected_columns


@pytest.mark.asyncio
async def test_manual_refresh_is_rate_limited_before_provider_io(monkeypatch) -> None:
    now = datetime.now(UTC)
    snapshot = SimpleNamespace(
        cache_key="ranking:v2:github",
        kind="github",
        parameters={},
        payload={"title": "Cached", "subtitle": "", "items": []},
        fetched_at=now,
        next_refresh_at=now + timedelta(minutes=20),
        last_accessed_at=now,
        is_ready=True,
        refresh_token=None,
        refresh_expires_at=None,
        manual_refresh_after=now + timedelta(seconds=30),
        last_error=None,
    )
    session = AsyncMock()
    session.scalar.return_value = snapshot
    fetch = AsyncMock()
    monkeypatch.setattr("app.services.ranking_snapshots.fetch_live_ranking", fetch)

    with pytest.raises(RankingRefreshRateLimited):
        await create_or_refresh_snapshot(
            session,
            RankingRequest(kind="github"),
            force=True,
        )

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_initial_snapshot_honors_retry_window_before_provider_io(
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    snapshot = SimpleNamespace(
        cache_key="ranking:v2:hacker_news",
        kind="hacker_news",
        parameters={},
        payload={"title": "Ranking", "subtitle": "", "items": []},
        fetched_at=now,
        next_refresh_at=now + timedelta(minutes=5),
        last_accessed_at=now,
        is_ready=False,
        refresh_token=None,
        refresh_expires_at=None,
        manual_refresh_after=None,
        last_error="upstream unavailable",
    )
    session = AsyncMock()
    session.scalar.return_value = snapshot
    fetch = AsyncMock()
    monkeypatch.setattr("app.services.ranking_snapshots.fetch_live_ranking", fetch)

    with pytest.raises(RankingRefreshInProgress) as raised:
        await create_or_refresh_snapshot(session, RankingRequest(kind="hacker_news"))

    assert raised.value.retry_after_seconds > 0
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_refresh_cannot_bypass_provider_failure_backoff(monkeypatch) -> None:
    now = datetime.now(UTC)
    snapshot = SimpleNamespace(
        cache_key="ranking:v2:reddit:test:hot:-",
        kind="reddit",
        parameters={"subreddit": "test", "sort": "hot", "time_filter": "week"},
        payload={"title": "Cached", "subtitle": "", "items": []},
        fetched_at=now,
        next_refresh_at=now + timedelta(minutes=15),
        last_accessed_at=now,
        is_ready=True,
        refresh_token=None,
        refresh_expires_at=None,
        manual_refresh_after=now - timedelta(seconds=1),
        last_error="provider rate limited",
    )
    session = AsyncMock()
    session.scalar.return_value = snapshot
    fetch = AsyncMock()
    monkeypatch.setattr("app.services.ranking_snapshots.fetch_live_ranking", fetch)

    with pytest.raises(RankingRefreshRateLimited) as raised:
        await create_or_refresh_snapshot(
            session,
            RankingRequest(kind="reddit", subreddit="test"),
            force=True,
        )

    assert raised.value.retry_after_seconds >= 60
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_budget_is_shared_across_snapshot_keys(monkeypatch) -> None:
    now = datetime.now(UTC)
    snapshot = SimpleNamespace(
        cache_key="ranking:v2:reddit:another:hot:-",
        kind="reddit",
        parameters={"subreddit": "another", "sort": "hot", "time_filter": "week"},
        payload={"title": "Cached", "subtitle": "", "items": []},
        fetched_at=now - timedelta(minutes=20),
        next_refresh_at=now - timedelta(minutes=1),
        last_accessed_at=now,
        is_ready=True,
        refresh_token=None,
        refresh_expires_at=None,
        manual_refresh_after=None,
        last_error=None,
    )
    provider_budget = SimpleNamespace(
        provider="reddit",
        window_started_at=now,
        refresh_count=1,
        blocked_until=now + timedelta(seconds=30),
    )
    session = AsyncMock()
    session.scalar.side_effect = [snapshot, provider_budget]
    fetch = AsyncMock()
    monkeypatch.setattr("app.services.ranking_snapshots.fetch_live_ranking", fetch)

    with pytest.raises(RankingRefreshRateLimited):
        await create_or_refresh_snapshot(
            session,
            RankingRequest(kind="reddit", subreddit="another"),
            force=True,
        )

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_hot_top_ten_are_admitted_to_the_home_queue(monkeypatch) -> None:
    source = SimpleNamespace(id="source", status="pending")
    state = SimpleNamespace(committed_checkpoint={})
    session = AsyncMock()
    session.scalar.return_value = source
    session.get.side_effect = [source, state]
    captured: dict[str, object] = {}

    async def capture_candidates(*_args, **kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("app.services.ranking_snapshots.upsert_candidates", capture_candidates)
    items = [
        RankingItem(
            rank=index,
            title=f"Post {index}",
            url=f"https://reddit.com/r/test/comments/{index}/post/",
            native_id=f"t3_{index}",
        )
        for index in range(1, 13)
    ]
    observed_at = datetime.now(UTC)

    await _enqueue_reddit_hot_admissions(
        session,
        RankingRequest(kind="reddit", subreddit="test", sort="hot"),
        RankingData(
            kind="reddit",
            title="r/test",
            subtitle="Hot",
            items=items,
            fetched_at=observed_at,
        ),
        observed_at,
    )

    queued = captured["items"]
    assert len(queued) == 10
    assert [item.native_id for item in queued] == [f"t3_{index}" for index in range(1, 11)]
    assert captured["max_changes"] is None
    assert captured["force_feed_sort_at"] == observed_at
    assert captured["counts_as_update"] is False
    assert state.committed_checkpoint == {"reddit_hot_baseline_received": True}
