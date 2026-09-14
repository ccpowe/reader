import asyncio

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
