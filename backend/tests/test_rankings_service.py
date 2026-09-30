import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from app.services import rankings


@pytest.mark.asyncio
async def test_hacker_news_item_429_is_not_published_as_an_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[1, 2], request=request)
        return httpx.Response(
            429,
            headers={"Retry-After": "37"},
            json={"error": "rate limited"},
            request=request,
        )

    monkeypatch.setattr(
        rankings.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await rankings.fetch_hacker_news(limit=2)

    assert raised.value.response.status_code == 429
    assert raised.value.response.headers["Retry-After"] == "37"


@pytest.mark.asyncio
async def test_hacker_news_item_429_cancels_slow_sibling_requests_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient
    all_items_started = asyncio.Event()
    slow_item_cancelled = asyncio.Event()
    never = asyncio.Event()
    started_items = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal started_items
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[1, 2], request=request)
        started_items += 1
        if started_items == 2:
            all_items_started.set()
        await all_items_started.wait()
        if request.url.path.endswith("/1.json"):
            return httpx.Response(
                429,
                headers={"Retry-After": "37"},
                request=request,
            )
        try:
            await never.wait()
        finally:
            slow_item_cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        rankings.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    async with asyncio.timeout(0.5):
        with pytest.raises(httpx.HTTPStatusError) as raised:
            await rankings.fetch_hacker_news(limit=2)

    assert raised.value.response.headers["Retry-After"] == "37"
    assert slow_item_cancelled.is_set()


@pytest.mark.asyncio
async def test_hacker_news_all_failed_items_do_not_replace_the_snapshot_with_empty_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[1, 2], request=request)
        return httpx.Response(503, json={"error": "unavailable"}, request=request)

    monkeypatch.setattr(
        rankings.httpx,
        "AsyncClient",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await rankings.fetch_hacker_news(limit=2)
    assert raised.value.response.status_code == 503


@pytest.mark.asyncio
async def test_techtwitter_fetches_one_top50_page_in_upstream_order_with_originals(
    monkeypatch,
) -> None:
    real_client = httpx.AsyncClient
    requests: list[httpx.Request] = []
    original = "First line 😀\n\n  Second line, intact. "

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        tweets = [
            {
                "id": f"tweet-{index}",
                "tweet_url": f"https://x.com/author/status/{index}",
                "tweet_text": original if index == 0 else f"Post {index}",
                "seo_title": "SEO headline" if index == 0 else None,
                "summary": "An optional summary" if index == 0 else None,
                "author_name": "Author" if index == 0 else None,
                "author_handle": "@author" if index == 0 else None,
                "timestamp": "2026-09-30T12:00:00Z" if index == 0 else None,
                "like_count": None if index == 0 else index,
                "comment_count": 0 if index == 0 else None,
                "retweet_count": None,
                "bookmark_count": 7 if index == 0 else None,
                "image_url": "https://cdn.example/a.jpg" if index == 0 else None,
                "image_grid_urls": [
                    "https://cdn.example/a.jpg",
                    "javascript:alert(1)",
                    "https://cdn.example/b.jpg",
                ]
                if index == 0
                else None,
                "video_url": "https://cdn.example/clip.mp4" if index == 0 else None,
                "video_thumbnail_url": ("https://cdn.example/cover.jpg" if index == 0 else None),
            }
            for index in range(50)
        ]
        return httpx.Response(
            200, json={"tweets": tweets, "total": 50, "hasMore": False}, request=request
        )

    monkeypatch.setattr(
        rankings.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    items = await rankings.fetch_techtwitter_trending(50)

    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://www.techtwitter.com/api/tweets/trending?limit=50&offset=0"
    )
    assert len(items) == 50
    assert [(item.rank, item.native_id) for item in items] == [
        (index + 1, f"tweet-{index}") for index in range(50)
    ]
    first = items[0]
    assert (first.title, first.description, first.body_text) == (
        "SEO headline",
        "An optional summary",
        original,
    )
    assert (first.score, first.comments, first.retweet_count, first.bookmark_count) == (
        None,
        0,
        None,
        7,
    )
    assert first.published_at == datetime(2026, 9, 30, 12, tzinfo=UTC)
    assert first.image_urls == ("https://cdn.example/a.jpg", "https://cdn.example/b.jpg")
    assert first.video_url == "https://cdn.example/clip.mp4"
    assert first.video_thumbnail_url == "https://cdn.example/cover.jpg"
    assert items[1].title == "Post 1"
    assert items[1].published_at is None
    assert items[1].comments is None


@pytest.mark.asyncio
async def test_techtwitter_rejects_failed_http_and_skips_invalid_post_urls(monkeypatch) -> None:
    real_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["limit"] == "1":
            return httpx.Response(429, request=request)
        return httpx.Response(
            200,
            json={
                "tweets": [
                    {"id": "broken", "tweet_url": "javascript:alert(1)", "tweet_text": "Wrong"},
                    {"id": "valid", "tweet_url": "https://x.com/a/status/2", "tweet_text": "Right"},
                    {
                        "id": None,
                        "tweet_url": "https://x.com/a/status/3",
                        "tweet_text": None,
                        "seo_title": "Media post",
                    },
                ]
            },
        )

    monkeypatch.setattr(
        rankings.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await rankings.fetch_techtwitter_trending(1)
    assert raised.value.response.status_code == 429
    assert [
        (item.rank, item.native_id, item.body_text)
        for item in await rankings.fetch_techtwitter_trending(3)
    ] == [
        (2, "valid", "Right"),
        (3, "https://x.com/a/status/3", None),
    ]
