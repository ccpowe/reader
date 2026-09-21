from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import rankings, sources
from app.core.settings import Settings
from app.domain.enums import SourceStatus, TranslationPurpose, TranslationStatus
from app.ingestion.source_identity import canonical_reddit_identity
from app.main import create_app
from app.services.ranking_snapshots import RankingData
from app.services.rankings import RankingItem
from app.translation.domain import TranslationEngine, TranslationProjection
from app.translation.projection import TranslationContext


def _translation_context(*, enabled: bool = False) -> TranslationContext:
    return TranslationContext(
        target_locale="zh-CN",
        enabled=enabled,
        engine=(
            TranslationEngine(
                engine_id="test-engine",
                provider_name="test-provider",
                model_name="test-model",
            )
            if enabled
            else None
        ),
    )


def test_ranking_routes_require_authentication(monkeypatch) -> None:
    token = "rankings-test-connection-token"
    settings = Settings(_env_file=None, server_access_token=token)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    with TestClient(create_app()) as client:
        missing_gate = client.get("/v1/rankings/hacker_news")
        response = client.get("/v1/rankings/hacker_news", headers={"X-Reader-Server-Token": token})

    assert missing_gate.status_code == 401
    assert missing_gate.json()["detail"] == "invalid_server_token"
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token."


async def test_ranking_refresh_bypasses_a_fresh_snapshot(monkeypatch) -> None:
    data = RankingData(
        kind="github",
        title="GitHub Trending",
        subtitle="Repositories · this week",
        items=[RankingItem(rank=1, title="fresh", url="https://github.com/example/fresh")],
        fetched_at=datetime.now(UTC),
    )
    force_values: list[bool] = []

    async def should_not_read_snapshot(*_args: object) -> None:
        raise AssertionError("A manual refresh must not reuse a fresh snapshot.")

    async def refresh_snapshot(*_args: object, force: bool = False) -> RankingData:
        force_values.append(force)
        return data

    monkeypatch.setattr(rankings, "get_snapshot", should_not_read_snapshot)
    monkeypatch.setattr(rankings, "create_or_refresh_snapshot", refresh_snapshot)
    monkeypatch.setattr(
        rankings,
        "get_translation_context",
        AsyncMock(return_value=_translation_context()),
    )

    response = await rankings.get_ranking(
        "github",
        limit=100,
        refresh=True,
        subreddit="MachineLearning",
        sort="hot",
        time_filter="week",
        _current_user=SimpleNamespace(id=uuid4()),
        session=AsyncMock(),
    )

    assert force_values == [True]
    assert response.items[0].title == "fresh"
    assert response.items[0].translation_key.startswith("ranking:github:")
    assert response.effective_engine_fingerprint is None


async def test_reddit_ranking_requires_an_active_subscription(monkeypatch) -> None:
    access = AsyncMock(return_value=False)
    read_snapshot = AsyncMock()
    monkeypatch.setattr(rankings, "user_has_active_reddit_subscription", access)
    monkeypatch.setattr(rankings, "get_snapshot", read_snapshot)
    user = SimpleNamespace(id=uuid4())
    session = AsyncMock()

    with pytest.raises(HTTPException) as raised:
        await rankings.get_ranking(
            "reddit",
            limit=100,
            refresh=True,
            subreddit="MachineLearning",
            sort="hot",
            time_filter="week",
            _current_user=user,
            session=session,
        )

    assert raised.value.status_code == 403
    access.assert_awaited_once_with(session, user.id, "machinelearning")
    read_snapshot.assert_not_awaited()


def test_reddit_ranking_accepts_prefixed_max_length_community(monkeypatch) -> None:
    subreddit_name = "a" * 21
    access = AsyncMock(return_value=False)
    monkeypatch.setattr(rankings, "user_has_active_reddit_subscription", access)

    app = FastAPI()
    app.include_router(rankings.router)

    async def current_user() -> SimpleNamespace:
        return SimpleNamespace(id=uuid4())

    async def session() -> AsyncMock:
        return AsyncMock()

    app.dependency_overrides[rankings.require_current_user] = current_user
    app.dependency_overrides[rankings.get_session] = session

    with TestClient(app) as client:
        response = client.get(
            "/rankings/reddit",
            params={"subreddit": f"r/{subreddit_name}"},
        )

    assert response.status_code == 403
    assert access.await_args.args[2] == subreddit_name


async def test_reddit_subscription_and_snapshots_commit_atomically(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4())
    session = AsyncMock()
    source = SimpleNamespace(
        id=uuid4(),
        canonical_url="https://www.reddit.com/r/test",
        status=SourceStatus.PENDING,
    )
    subscription = SimpleNamespace(id=uuid4())
    sync_state = SimpleNamespace(next_scan_at=None)
    subscribe = AsyncMock(return_value=(source, subscription, sync_state, True))
    ensure = AsyncMock()
    monkeypatch.setattr(sources, "enforce_reddit_subscription_limit", AsyncMock())
    monkeypatch.setattr(sources, "subscribe_to_shared_source", subscribe)
    monkeypatch.setattr(sources, "ensure_reddit_snapshots", ensure)
    monkeypatch.setattr(
        sources,
        "canonical_reddit_identity",
        lambda value: canonical_reddit_identity(value),
    )

    await sources.add_reddit_source(
        sources.AddRedditSourceRequest(subreddit=" r/Test "),
        current_user=user,
        session=session,
    )

    assert subscribe.await_args.kwargs["commit"] is False
    assert subscribe.await_args.kwargs["display_name"] == "r/test"
    assert subscribe.await_args.kwargs["source_config"] == {"subreddit": "test"}
    ensure.assert_awaited_once_with(session, "test", commit=False)
    session.commit.assert_awaited_once()


def test_ranking_response_returns_original_before_visible_translation_demand() -> None:
    item = RankingItem(
        rank=1,
        title="Original title",
        description="Original description",
        native_id="stable-id",
        url="https://example.com/story",
    )
    response = rankings._to_response("hacker_news", item)

    assert response.translation_key == rankings._translation_key("hacker_news", item)
    assert response.title == "Original title"
    assert response.translated_title is None
    assert response.title_translation_status is None
    assert response.translated_description is None
    assert response.description_translation_status is None
    assert response.translation_locale is None


async def test_ranking_does_not_wait_for_translation_before_returning(monkeypatch) -> None:
    data = RankingData(
        kind="hacker_news",
        title="Hacker News",
        subtitle="Top stories",
        items=[
            RankingItem(
                rank=index,
                title=f"Story {index}",
                url=f"https://example.com/{index}",
            )
            for index in range(1, 51)
        ],
        fetched_at=datetime.now(UTC),
    )

    async def snapshot(*_args, **_kwargs):
        return data

    monkeypatch.setattr(rankings, "get_snapshot", snapshot)
    monkeypatch.setattr(
        rankings,
        "get_translation_context",
        AsyncMock(return_value=_translation_context()),
    )

    response = await rankings.get_ranking(
        "hacker_news",
        limit=50,
        refresh=False,
        subreddit="MachineLearning",
        sort="hot",
        time_filter="week",
        _current_user=SimpleNamespace(id=uuid4()),
        session=AsyncMock(),
    )

    assert len(response.items) == 50
    assert all(item.title_translation_status is None for item in response.items)


async def test_ranking_returns_only_existing_successful_translation_artifacts(monkeypatch) -> None:
    items = [
        RankingItem(
            rank=1,
            title="Cached title",
            description="Pending description",
            native_id="cached",
            url="https://example.com/cached",
        ),
        RankingItem(
            rank=2,
            title="Missing title",
            native_id="missing",
            url="https://example.com/missing",
        ),
    ]
    data = RankingData(
        kind="hacker_news",
        title="Hacker News",
        subtitle="Top stories",
        items=items,
        fetched_at=datetime.now(UTC),
    )
    context = _translation_context(enabled=True)
    captured = []

    async def snapshot(*_args, **_kwargs):
        return data

    async def cache_only(_session, translation_inputs, *, context, ensure_missing):
        assert ensure_missing is False
        captured.extend(translation_inputs)
        cached_key = rankings._translation_key("hacker_news", items[0])
        return {
            f"{cached_key}:title": TranslationProjection(
                item_id=f"{cached_key}:title",
                source_hash="title-hash",
                target_locale="zh-CN",
                status=TranslationStatus.SUCCEEDED,
                translated_text="缓存标题",
            ),
            f"{cached_key}:description": TranslationProjection(
                item_id=f"{cached_key}:description",
                source_hash="description-hash",
                target_locale="zh-CN",
                status=TranslationStatus.PENDING,
            ),
        }

    monkeypatch.setattr(rankings, "get_snapshot", snapshot)
    monkeypatch.setattr(rankings, "get_translation_context", AsyncMock(return_value=context))
    monkeypatch.setattr(rankings, "project_texts", cache_only)

    response = await rankings.get_ranking(
        "hacker_news",
        limit=100,
        refresh=False,
        subreddit="MachineLearning",
        sort="hot",
        time_filter="week",
        _current_user=SimpleNamespace(id=uuid4()),
        session=AsyncMock(),
    )

    assert [item.purpose for item in captured] == [
        TranslationPurpose.RANKING_TITLE,
        TranslationPurpose.RANKING_DESCRIPTION,
        TranslationPurpose.RANKING_TITLE,
    ]
    assert response.items[0].translated_title == "缓存标题"
    assert response.items[0].title_translation_status == "succeeded"
    assert response.items[0].translated_description is None
    assert response.items[0].description_translation_status is None
    assert response.items[0].translation_locale == "zh-CN"
    assert response.items[1].translated_title is None
    assert response.items[1].title_translation_status is None
    assert response.items[1].translation_locale is None
    assert response.effective_engine_fingerprint == context.engine.fingerprint
