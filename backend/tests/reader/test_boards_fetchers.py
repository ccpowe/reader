"""Board fetchers and board resolution, with mocked upstreams."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from reader.boards import fetchers
from reader.boards.service import resolve
from reader.core.errors import AppError
from reader.core.settings import Settings
from reader.ingestion.errors import ScanError


@pytest.fixture
def upstream(monkeypatch):
    """Route every fetcher request to `handler`; returns a setter."""
    state = {}
    real = httpx.AsyncClient

    def client(**kwargs):
        return real(transport=httpx.MockTransport(lambda r: state["handler"](r)), **kwargs)

    monkeypatch.setattr(
        fetchers,
        "_client",
        lambda **kw: client(timeout=5, follow_redirects=kw.get("follow_redirects", False)),
    )

    def set_handler(handler):
        state["handler"] = handler

    return set_handler


# --- Hacker News --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hn_returns_stories_in_order_and_skips_jobs(upstream) -> None:
    stories = {
        1: {
            "id": 1,
            "type": "story",
            "title": "One",
            "url": "https://www.a.com/x",
            "score": 5,
            "descendants": 2,
            "by": "u",
            "time": 1_700_000_000,
        },
        2: {"id": 2, "type": "job", "title": "Hiring"},
        3: {"id": 3, "type": "story", "title": "Ask HN"},
    }

    def handler(request):
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[1, 2, 3])
        return httpx.Response(200, json=stories[int(request.url.path.rsplit("/", 1)[1][:-5])])

    upstream(handler)
    items = await fetchers.fetch_hacker_news(10)

    assert [(item.rank, item.title) for item in items] == [(1, "One"), (2, "Ask HN")]
    assert items[0].source_label == "a.com" and items[0].score == 5
    assert items[1].url == "https://news.ycombinator.com/item?id=3"


@pytest.mark.asyncio
async def test_hn_rate_limit_stops_the_board_and_cancels_the_rest(upstream) -> None:
    cancelled = asyncio.Event()

    async def handler(request):
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[1, 2])
        if request.url.path.endswith("/1.json"):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return httpx.Response(429, headers={"Retry-After": "37"})

    upstream(handler)
    with pytest.raises(ScanError) as error:
        await asyncio.wait_for(fetchers.fetch_hacker_news(2), 5)

    assert error.value.code == "hn_http_429" and error.value.retry_after_seconds == 37
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_hn_skips_a_story_that_times_out_or_is_garbled(upstream) -> None:
    def handler(request):
        path = request.url.path
        if path.endswith("topstories.json"):
            return httpx.Response(200, json=[1, 2, 3])
        if path.endswith("/1.json"):
            raise httpx.ReadTimeout("slow", request=request)
        if path.endswith("/2.json"):
            return httpx.Response(200, text="{not json")
        return httpx.Response(200, json={"id": 3, "type": "story", "title": "Fine"})

    upstream(handler)
    items = await fetchers.fetch_hacker_news(10)
    assert [item.title for item in items] == ["Fine"]


@pytest.mark.asyncio
async def test_hn_with_every_story_failing_is_an_outage(upstream) -> None:
    def handler(request):
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[1, 2])
        return httpx.Response(500)

    upstream(handler)
    with pytest.raises(ScanError) as error:
        await fetchers.fetch_hacker_news(2)
    assert error.value.code == "hn_unavailable"


# --- GitHub, TechTwitter, Reddit ---------------------------------------------------------

_GITHUB = """
<article class="Box-row">
  <h2><a href="/owner/repo">owner / repo</a></h2>
  <p>A tool</p><span itemprop="programmingLanguage">Python</span>
  <a href="/owner/repo/stargazers">1,234</a><a href="/owner/repo/forks">56</a>
  <span>789 stars this week</span>
</article>"""


@pytest.mark.asyncio
async def test_github_trending(upstream) -> None:
    seen = {}

    def handler(request):
        seen["since"] = request.url.params["since"]
        return httpx.Response(200, text=_GITHUB)

    upstream(handler)
    (item,) = await fetchers.fetch_github_trending("weekly", 10)

    assert seen["since"] == "weekly"
    assert (item.title, item.url, item.language) == (
        "owner/repo",
        "https://github.com/owner/repo",
        "Python",
    )
    assert (item.stars, item.forks, item.stars_this_period) == (1234, 56, 789)


@pytest.mark.asyncio
async def test_techtwitter_keeps_order_and_skips_bad_urls(upstream) -> None:
    tweets = [
        {
            "id": 1,
            "tweet_url": "https://x.com/a/status/1",
            "tweet_text": "Long body",
            "seo_title": "Title",
            "like_count": 3,
            "retweet_count": 1,
            "image_url": "https://i/1.png",
            "image_grid_urls": ["https://i/1.png", "https://i/2.png"],
        },
        {"id": 2, "tweet_url": "javascript:alert(1)", "tweet_text": "bad"},
        {"id": 3, "tweet_url": "https://x.com/b/status/3", "tweet_text": "Only body"},
    ]
    upstream(lambda request: httpx.Response(200, json={"tweets": tweets}))
    items = await fetchers.fetch_techtwitter(50)

    assert [(item.rank, item.title) for item in items] == [(1, "Title"), (2, "Only body")]
    assert items[0].image_urls == ["https://i/1.png", "https://i/2.png"]
    assert items[0].body_text == "Long body" and items[0].reposts == 1


_REDDIT_RSS = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry><title>Post</title><link href="https://www.reddit.com/r/python/comments/abc/post/"/>
  <author><name>/u/me</name></author><updated>2026-11-10T10:00:00+00:00</updated>
  <content type="html">&lt;p&gt;Hello&lt;/p&gt;&lt;img src="https://i.redd.it/x.png"/&gt;</content>
 </entry>
</feed>"""


@pytest.mark.asyncio
async def test_reddit_top_week(upstream) -> None:
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, text=_REDDIT_RSS)

    upstream(handler)
    (item,) = await fetchers.fetch_reddit("python", "top-week", 25)

    assert seen["url"] == "https://www.reddit.com/r/python/top/.rss?limit=25&t=week"
    assert item.source_label == "r/python · Top · week"
    assert item.description == "Hello" and item.image_urls == ["https://i.redd.it/x.png"]


@pytest.mark.asyncio
async def test_a_bot_check_is_not_mistaken_for_a_rate_limit(upstream) -> None:
    upstream(
        lambda request: httpx.Response(
            429, headers={"x-vercel-mitigated": "challenge"}, text="<title>Checkpoint</title>"
        )
    )
    with pytest.raises(ScanError) as error:
        await fetchers.fetch_techtwitter(10)
    assert error.value.code == "techtwitter_challenge_required"


@pytest.mark.asyncio
async def test_upstream_http_errors_become_codes(upstream) -> None:
    upstream(lambda request: httpx.Response(503))
    with pytest.raises(ScanError) as error:
        await fetchers.fetch_techtwitter(10)
    assert error.value.code == "techtwitter_http_503"


# --- Product Hunt -------------------------------------------------------------------


def test_product_hunt_days_follow_los_angeles() -> None:
    # 07:30 UTC on Nov 10 is still Nov 9 in Los Angeles.
    now = datetime(2026, 11, 10, 7, 30, tzinfo=UTC)
    assert fetchers.product_hunt_day(now, "today") == date(2026, 11, 9)
    assert fetchers.product_hunt_day(now, "yesterday") == date(2026, 11, 8)
    start, end = fetchers.product_hunt_bounds(date(2026, 11, 1))  # DST ends: 25 hours
    assert end - start == timedelta(hours=25)


@pytest.mark.asyncio
async def test_product_hunt_paginates_in_ranking_order(upstream) -> None:
    pages = {
        None: ([{"id": "1", "name": "A", "url": "https://ph/a"}], True, "c1"),
        "c1": ([{"id": "2", "name": "B", "url": "https://ph/b", "votesCount": 9}], False, None),
    }
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.headers["authorization"], body["variables"]["after"]))
        nodes, more, cursor = pages[body["variables"]["after"]]
        return httpx.Response(
            200,
            json={
                "data": {
                    "posts": {
                        "edges": [{"node": node} for node in nodes],
                        "pageInfo": {"hasNextPage": more, "endCursor": cursor},
                    }
                }
            },
        )

    upstream(handler)
    items = await fetchers.fetch_product_hunt(date(2026, 11, 9), 10, "token")

    assert [(item.rank, item.title, item.score) for item in items] == [(1, "A", None), (2, "B", 9)]
    assert seen == [("Bearer token", None), ("Bearer token", "c1")]


@pytest.mark.asyncio
async def test_product_hunt_errors(upstream) -> None:
    with pytest.raises(ScanError) as missing:
        await fetchers.fetch_product_hunt(date(2026, 11, 9), 10, None)
    upstream(lambda request: httpx.Response(200, json={"errors": [{"message": "no"}]}))
    with pytest.raises(ScanError) as graphql:
        await fetchers.fetch_product_hunt(date(2026, 11, 9), 10, "token")
    upstream(lambda request: httpx.Response(302, headers={"location": "https://evil/"}))
    with pytest.raises(ScanError) as redirect:
        await fetchers.fetch_product_hunt(date(2026, 11, 9), 10, "token")

    assert missing.value.code == "product_hunt_not_configured"
    assert graphql.value.code == "product_hunt_invalid_response"
    assert redirect.value.code == "product_hunt_http_302"  # the token is never sent on


# --- Resolution ---------------------------------------------------------------------


def _code(call) -> str:
    with pytest.raises(AppError) as error:
        call()
    return error.value.code


def test_board_requests_are_validated() -> None:
    settings = Settings(_env_file=None)
    assert resolve(settings, "hn", None, None).key == "hn"
    assert resolve(settings, "github", "monthly", None).key == "github:monthly"
    reddit = resolve(settings, "reddit", "top-year", "r/Python")
    assert (reddit.key, reddit.subreddit) == ("reddit:python:top-year", "python")
    assert _code(lambda: resolve(settings, "github", "yearly", None)) == "invalid_variant"
    assert _code(lambda: resolve(settings, "reddit", None, None)) == "invalid_subreddit"
    assert _code(lambda: resolve(settings, "reddit", None, "a b")) == "invalid_subreddit"
    assert _code(lambda: resolve(settings, "hn", None, "python")) == "invalid_subreddit"


def test_a_finished_product_hunt_day_never_expires() -> None:
    settings = Settings(_env_file=None)
    now = datetime(2026, 11, 10, 20, tzinfo=UTC)  # Nov 10 in Los Angeles
    yesterday = resolve(settings, "product_hunt", "yesterday", None, now)
    today = resolve(settings, "product_hunt", "today", None, now)

    assert (yesterday.key, today.key) == ("product_hunt:2026-11-09", "product_hunt:2026-11-10")
    after_day_end = yesterday.final_after + timedelta(minutes=1)
    assert yesterday.is_fresh(after_day_end, now + timedelta(days=3))
    during_day = yesterday.final_after - timedelta(hours=1)
    assert not yesterday.is_fresh(during_day, now)
    assert not today.is_fresh(now - timedelta(minutes=31), now)
