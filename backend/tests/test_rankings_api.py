from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import rankings
from app.core.settings import Settings
from app.domain.enums import TranslationPurpose, TranslationStatus
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

    async def cache_only(_session, translation_inputs, *, context):
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
    monkeypatch.setattr(rankings, "read_text_translations", cache_only)

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


async def test_techtwitter_body_is_losslessly_chunked_and_only_body_cache_is_projected(
    monkeypatch,
) -> None:
    original = "😀" * 4_001 + "\n  Next paragraph " + "字" * 8_005
    item = RankingItem(
        rank=1,
        title="SEO title, not tweet body",
        description="Summary, not tweet body",
        url="https://x.com/dev/status/42",
        native_id="uuid-42",
        body_text=original,
    )
    context = _translation_context(enabled=True)
    captured = []

    async def cache_only(_session, inputs, *, context):
        captured.extend(inputs)
        first = inputs[0]
        return {
            first.item_id: TranslationProjection(
                item_id=first.item_id,
                source_hash="body-cache",
                target_locale=context.target_locale,
                status=TranslationStatus.SUCCEEDED,
                translated_text="第一段",
            ),
            inputs[1].item_id: TranslationProjection(
                item_id=inputs[1].item_id,
                source_hash="pending",
                target_locale=context.target_locale,
                status=TranslationStatus.PENDING,
            ),
        }

    monkeypatch.setattr(rankings, "get_translation_context", AsyncMock(return_value=context))
    monkeypatch.setattr(rankings, "read_text_translations", cache_only)
    result = await rankings._ranking_response(
        AsyncMock(),
        uuid4(),
        RankingData(
            kind="techtwitter",
            title="X 技术热门",
            subtitle="Trending",
            items=[item],
            fetched_at=datetime.now(UTC),
        ),
        limit=50,
    )

    response_item = result.items[0]
    segments = response_item.body_segments
    assert result.effective_engine_fingerprint == context.engine.fingerprint
    assert response_item.body_text == original
    assert "".join(segment.text for segment in segments) == original
    assert [segment.segment_id for segment in segments] == [
        f"{response_item.translation_key}:body:{index}" for index in range(len(segments))
    ]
    assert all(len(segment.text.encode("utf-16-le")) // 2 <= 8_000 for segment in segments)
    assert len(segments) == 5
    assert segments[2].text == "\n"
    assert segments[2].translated_text is None
    assert [source.purpose for source in captured] == [
        TranslationPurpose.PARAGRAPH for _ in range(4)
    ]
    assert [source.text for source in captured] == [
        segment.text for segment in segments if segment.text.strip()
    ]
    assert segments[0].translated_text == "第一段"
    assert segments[0].translation_status == "succeeded"
    assert segments[1].translated_text is None
    assert segments[1].translation_status is None
    assert response_item.translated_title is None
    assert response_item.translated_description is None
    assert response_item.translation_locale == "zh-CN"


def test_techtwitter_saved_preview_without_context_cannot_leak_translations() -> None:
    item = RankingItem(
        rank=1,
        title="Different SEO title",
        url="https://x.com/author/status/99",
        body_text="原文 😀\n",
        description="Separate summary",
    )
    response = rankings._to_response("techtwitter", item)

    assert response.translation_locale is None
    assert response.body_text == "原文 😀\n"
    assert [(segment.text, segment.translated_text) for segment in response.body_segments] == [
        ("原文 😀", None),
        ("\n", None),
    ]
    assert response.body_segments[0].segment_id.endswith(":body:0")


def _isolated_ranking_app() -> FastAPI:
    app = FastAPI()
    app.include_router(rankings.router)

    async def current_user() -> SimpleNamespace:
        return SimpleNamespace(id=uuid4())

    async def session() -> AsyncMock:
        return AsyncMock()

    app.dependency_overrides[rankings.require_current_user] = current_user
    app.dependency_overrides[rankings.get_session] = session
    return app


def test_product_hunt_date_and_github_period_reject_wrong_scopes_and_future(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings,
        "get_settings",
        lambda: SimpleNamespace(product_hunt_token=SecretStr("secret")),
    )
    app = _isolated_ranking_app()
    future = date.today().replace(year=date.today().year + 1).isoformat()
    with TestClient(app) as client:
        assert client.get("/rankings/github", params={"date": "2026-10-20"}).status_code == 422
        assert client.get("/rankings/hacker_news", params={"period": "daily"}).status_code == 422
        assert client.get("/rankings/product_hunt", params={"period": "weekly"}).status_code == 422
        assert client.get("/rankings/product_hunt", params={"date": "2026-2-3"}).status_code == 422
        assert client.get("/rankings/product_hunt", params={"date": future}).status_code == 422
        assert client.get("/rankings/github", params={"period": "yearly"}).status_code == 422


@pytest.mark.asyncio
async def test_product_hunt_disabled_returns_clear_503_before_reading_cache(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings,
        "get_settings",
        lambda: SimpleNamespace(product_hunt_token=None),
    )
    read_snapshot = AsyncMock()
    monkeypatch.setattr(rankings, "get_snapshot", read_snapshot)
    with pytest.raises(HTTPException) as raised:
        await rankings.get_ranking(
            "product_hunt",
            limit=100,
            refresh=False,
            subreddit="MachineLearning",
            sort="hot",
            time_filter="week",
            date="2026-10-20",
            _current_user=SimpleNamespace(id=uuid4()),
            session=AsyncMock(),
        )
    assert raised.value.status_code == 503
    assert raised.value.detail == "Product Hunt ranking is not configured."
    read_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_product_hunt_historical_ranking_cache_metadata_and_github_period(
    monkeypatch,
) -> None:
    historical_day = date(2026, 10, 20)
    item = RankingItem(
        rank=1,
        title="Launch",
        url="https://www.producthunt.com/posts/launch",
        product_id="post-1",
        website_url="https://example.org/",
        description="A tagline",
    )
    data = RankingData(
        kind="product_hunt",
        title="Product Hunt",
        subtitle="Featured launches",
        items=[item],
        fetched_at=datetime.now(UTC),
        truncated=True,
    )
    requests = []

    async def snapshot(_session, request):
        requests.append(request)
        return data

    monkeypatch.setattr(
        rankings,
        "get_settings",
        lambda: Settings(_env_file=None, product_hunt_token="secret"),
    )
    monkeypatch.setattr(rankings, "get_snapshot", snapshot)
    monkeypatch.setattr(
        rankings,
        "get_translation_context",
        AsyncMock(return_value=_translation_context()),
    )
    result = await rankings.get_ranking(
        "product_hunt",
        limit=100,
        refresh=False,
        subreddit="MachineLearning",
        sort="hot",
        time_filter="week",
        date=historical_day.isoformat(),
        _current_user=SimpleNamespace(id=uuid4()),
        session=AsyncMock(),
    )
    assert requests[0].day == historical_day
    assert result.date == historical_day.isoformat()
    assert (result.timezone, result.order, result.truncated) == (
        "America/Los_Angeles",
        "RANKING",
        True,
    )
    assert (result.items[0].product_id, result.items[0].website_url) == (
        "post-1",
        "https://example.org/",
    )

    async def github_snapshot(_session, request):
        requests.append(request)
        return RankingData(
            kind="github",
            title="GitHub Trending",
            subtitle="Repositories · today",
            items=[],
            fetched_at=datetime.now(UTC),
        )

    monkeypatch.setattr(rankings, "get_snapshot", github_snapshot)
    github = await rankings.get_ranking(
        "github",
        limit=100,
        refresh=False,
        subreddit="MachineLearning",
        sort="hot",
        time_filter="week",
        period="daily",
        _current_user=SimpleNamespace(id=uuid4()),
        session=AsyncMock(),
    )
    assert requests[-1].period == "daily"
    assert github.period == "daily"


@pytest.mark.asyncio
async def test_product_hunt_omitted_date_uses_los_angeles_calendar_day(monkeypatch) -> None:
    monkeypatch.setattr(
        rankings,
        "get_settings",
        lambda: Settings(_env_file=None, product_hunt_token="secret"),
    )
    requests = []

    async def snapshot(_session, request):
        requests.append(request)
        return RankingData(
            kind="product_hunt",
            title="Product Hunt",
            subtitle="Featured launches",
            items=[],
            fetched_at=datetime.now(UTC),
        )

    monkeypatch.setattr(rankings, "get_snapshot", snapshot)
    monkeypatch.setattr(
        rankings,
        "get_translation_context",
        AsyncMock(return_value=_translation_context()),
    )
    result = await rankings.get_ranking(
        "product_hunt",
        limit=100,
        refresh=False,
        subreddit="MachineLearning",
        sort="hot",
        time_filter="week",
        _current_user=SimpleNamespace(id=uuid4()),
        session=AsyncMock(),
    )
    assert requests[0].day == datetime.now(rankings.PRODUCT_HUNT_TIMEZONE).date()
    assert result.date == requests[0].day.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selected_day", "age_seconds", "expected_votes"),
    [
        ("2026-10-22", 20, 10),
        ("2026-10-22", 61, 331),
        ("2026-10-21", 20, 10),
        ("2026-10-21", 61, 331),
    ],
)
async def test_product_hunt_date_query_revalidates_official_counts(
    monkeypatch, selected_day, age_seconds, expected_votes
) -> None:
    now = datetime(2026, 10, 22, 12, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    def data(votes, at):
        return RankingData(
            kind="product_hunt",
            title="Product Hunt",
            subtitle="Featured",
            items=[
                RankingItem(
                    rank=1,
                    title="Launch",
                    score=votes,
                    url="https://www.producthunt.com/posts/launch",
                )
            ],
            fetched_at=at,
        )

    monkeypatch.setattr(rankings, "datetime", Clock)
    monkeypatch.setattr(
        rankings, "get_settings", lambda: Settings(_env_file=None, product_hunt_token="secret")
    )
    monkeypatch.setattr(
        rankings,
        "get_snapshot",
        AsyncMock(return_value=data(10, now - timedelta(seconds=age_seconds))),
    )
    monkeypatch.setattr(
        rankings, "create_or_refresh_snapshot", AsyncMock(return_value=data(331, now))
    )
    monkeypatch.setattr(
        rankings, "get_translation_context", AsyncMock(return_value=_translation_context())
    )
    response = await rankings.get_ranking(
        "product_hunt",
        limit=100,
        refresh=False,
        subreddit="MachineLearning",
        sort="hot",
        time_filter="week",
        date=selected_day,
        _current_user=SimpleNamespace(id=uuid4()),
        session=AsyncMock(),
    )
    assert response.items[0].score == expected_votes


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_completed", [True, False])
async def test_product_hunt_previous_day_does_not_reuse_recent_pre_close_capture(
    monkeypatch, refresh_completed
):
    now = datetime(2026, 10, 22, 7, 0, 10, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    captured = RankingData(
        kind="product_hunt",
        title="Product Hunt",
        subtitle="Featured",
        items=[
            RankingItem(
                rank=1, title="Old", score=0, url="https://www.producthunt.com/posts/launch"
            )
        ],
        fetched_at=now - timedelta(seconds=20),
    )
    official = RankingData(
        kind="product_hunt",
        title="Product Hunt",
        subtitle="Featured",
        items=[
            RankingItem(
                rank=1, title="Launch", score=331, url="https://www.producthunt.com/posts/launch"
            )
        ],
        fetched_at=now,
    )
    monkeypatch.setattr(rankings, "datetime", Clock)
    monkeypatch.setattr(
        rankings, "get_settings", lambda: Settings(_env_file=None, product_hunt_token="secret")
    )
    monkeypatch.setattr(rankings, "get_snapshot", AsyncMock(return_value=captured))
    monkeypatch.setattr(
        rankings,
        "create_or_refresh_snapshot",
        AsyncMock(return_value=official if refresh_completed else captured),
    )
    monkeypatch.setattr(
        rankings, "get_translation_context", AsyncMock(return_value=_translation_context())
    )
    operation = rankings.get_ranking(
        "product_hunt",
        limit=100,
        refresh=False,
        subreddit="MachineLearning",
        sort="hot",
        time_filter="week",
        date="2026-10-21",
        _current_user=SimpleNamespace(id=uuid4()),
        session=AsyncMock(),
    )
    if refresh_completed:
        response = await operation
        assert response.items[0].score == 331
        assert response.fetched_at == now
    else:
        with pytest.raises(HTTPException) as raised:
            await operation
        assert raised.value.status_code == 503
        assert raised.value.headers["Retry-After"]
