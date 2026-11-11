"""Read one board from its upstream; ported from the old ranking adapters.

Every fetcher returns up to `limit` BoardItems in board order and raises only
ScanError (with `retry_after_seconds` on rate limits). Hosts are fixed provider
endpoints, so requests go through `bounded_request`, not the public-URL checks.
"""

from __future__ import annotations

import asyncio
import calendar
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from reader.ingestion.errors import ScanError, upstream_errors
from reader.ingestion.http import bounded_request, raise_for_provider_status

MAX_BYTES = 5_000_000
_USER_AGENT = "ReaderApp/3 (self-hosted reader)"
PRODUCT_HUNT_TIMEZONE = ZoneInfo("America/Los_Angeles")
PRODUCT_HUNT_API_URL = "https://api.producthunt.com/v2/api/graphql"


class BoardItem(BaseModel):
    """One entry; fields a board does not have stay empty."""

    rank: int
    title: str
    url: str
    description: str | None = None
    # Long text of the entry itself (a tweet), when the board has one.
    body_text: str | None = None
    author: str | None = None
    author_handle: str | None = None
    # Where the link points, e.g. "github.com" on HN or "r/python · Hot".
    source_label: str | None = None
    published_at: datetime | None = None
    image_urls: list[str] = []
    video_url: str | None = None
    video_thumbnail_url: str | None = None
    website_url: str | None = None
    language: str | None = None
    score: int | None = None
    comments: int | None = None
    stars: int | None = None
    forks: int | None = None
    stars_this_period: int | None = None
    reposts: int | None = None
    bookmarks: int | None = None


def _client(*, follow_redirects: bool = False) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=follow_redirects,
        trust_env=False,
        headers={"User-Agent": _USER_AGENT},
    )


def _check(response: httpx.Response, provider: str) -> None:
    """Errors keep their status code; any other non-2xx (a redirect from a fixed API
    endpoint) is refused too, so a token is never sent on to another host."""
    raise_for_provider_status(response, provider)
    if not response.is_success:
        raise ScanError(
            f"{provider}_http_{response.status_code}",
            f"{provider} answered HTTP {response.status_code}.",
        )


async def _get(client: httpx.AsyncClient, url: str, provider: str, **kwargs) -> httpx.Response:
    response = await bounded_request(client, "GET", url, max_bytes=MAX_BYTES, **kwargs)
    _check(response, provider)
    return response


def _invalid(provider: str) -> ScanError:
    return ScanError(f"{provider}_invalid_response", f"{provider} returned an unexpected response.")


# --- Hacker News --------------------------------------------------------------------


async def fetch_hacker_news(limit: int) -> list[BoardItem]:
    """Top Stories from the official Firebase API (job posts are skipped)."""
    base = "https://hacker-news.firebaseio.com/v0"
    with upstream_errors("hn"):
        async with _client() as client:
            ids = (await _get(client, f"{base}/topstories.json", "hn")).json()
            if not isinstance(ids, list):
                raise _invalid("hn")
            # A small buffer, so skipped job posts still leave `limit` stories.
            wanted = ids[: limit + 15]
            gate = asyncio.Semaphore(20)
            failed = 0

            async def item(item_id: Any) -> dict | None:
                nonlocal failed
                async with gate:
                    try:
                        # Timeouts, network errors and bad JSON become ScanErrors here.
                        with upstream_errors("hn"):
                            response = await _get(client, f"{base}/item/{int(item_id)}.json", "hn")
                            data = response.json()
                    except ScanError as exc:
                        if exc.code == "hn_http_429":
                            raise  # stops the board; the task group cancels the rest
                        failed += 1
                        return None  # one missing story does not spoil the board
                return data if isinstance(data, dict) else None

            try:
                async with asyncio.TaskGroup() as group:
                    tasks = [group.create_task(item(item_id)) for item_id in wanted]
            except* Exception as errors:
                # Unwrap, so upstream_errors maps it like any other failure.
                raise errors.exceptions[0] from None
            raw = [task.result() for task in tasks]
    if failed and not any(raw):
        # Every story failed: that is an outage, not an empty board.
        raise ScanError("hn_unavailable", "Hacker News stories could not be loaded.")
    items = []
    for data in raw:
        if not data or data.get("type") != "story" or not data.get("title"):
            continue
        url = _http_url(data.get("url")) or f"https://news.ycombinator.com/item?id={data.get('id')}"
        items.append(
            BoardItem(
                rank=len(items) + 1,
                title=str(data["title"]),
                url=url,
                source_label=_host(url),
                author=_text(data.get("by")),
                score=_count(data.get("score")),
                comments=_count(data.get("descendants")),
                published_at=datetime.fromtimestamp(data["time"], UTC)
                if isinstance(data.get("time"), int)
                else None,
            )
        )
        if len(items) >= limit:
            break
    return items


# --- GitHub Trending ----------------------------------------------------------------

GITHUB_PERIODS = {"daily": "today", "weekly": "this week", "monthly": "this month"}


async def fetch_github_trending(period: str, limit: int) -> list[BoardItem]:
    """GitHub's public Trending page (at most 25 repositories)."""
    with upstream_errors("github"):
        async with _client(follow_redirects=True) as client:
            response = await _get(
                client, "https://github.com/trending", "github", params={"since": period}
            )
    soup = BeautifulSoup(response.text, "html.parser")
    gained = re.compile(rf"([\d,]+)\s+stars\s+{re.escape(GITHUB_PERIODS[period])}\b", re.I)
    items = []
    for article in soup.select("article.Box-row")[:limit]:
        link = article.select_one("h2 a[href]")
        if link is None:
            continue
        description = article.select_one("p")
        language = article.select_one("[itemprop='programmingLanguage']")
        match = gained.search(article.get_text(" ", strip=True))
        items.append(
            BoardItem(
                rank=len(items) + 1,
                title=" ".join(link.stripped_strings).replace(" / ", "/"),
                url=f"https://github.com{link['href']}",
                description=description.get_text(" ", strip=True) if description else None,
                language=language.get_text(strip=True) if language else None,
                stars=_linked_count(article, "/stargazers"),
                forks=_linked_count(article, "/forks"),
                stars_this_period=_number(match.group(1)) if match else None,
                image_urls=[
                    str(image["src"])
                    for image in article.select("img.avatar-user[src]")
                    if str(image["src"]).startswith("https://")
                ][:5],
            )
        )
    if not items and soup.select_one("article") is None:
        raise _invalid("github")
    return items


# --- TechTwitter --------------------------------------------------------------------


async def fetch_techtwitter(limit: int) -> list[BoardItem]:
    """TechTwitter's trending order, one public API request."""
    with upstream_errors("techtwitter"):
        async with _client() as client:
            response = await _get(
                client,
                "https://www.techtwitter.com/api/tweets/trending",
                "techtwitter",
                params={"limit": min(limit, 50), "offset": 0},
            )
        payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("tweets"), list):
        raise _invalid("techtwitter")
    items = []
    for raw in payload["tweets"]:
        if not isinstance(raw, dict):
            continue
        url = _http_url(raw.get("tweet_url"))
        body = raw.get("tweet_text") if isinstance(raw.get("tweet_text"), str) else None
        title = _text(raw.get("seo_title")) or (body or "")[:160]
        if url is None or not title.strip():
            continue
        images = [raw.get("image_url")]
        if isinstance(raw.get("image_grid_urls"), list):
            images += raw["image_grid_urls"]
        items.append(
            BoardItem(
                rank=len(items) + 1,
                title=title,
                url=url,
                source_label="TechTwitter",
                description=_text(raw.get("summary")),
                body_text=body,
                author=_text(raw.get("author_name")),
                author_handle=_text(raw.get("author_handle")),
                score=_count(raw.get("like_count")),
                comments=_count(raw.get("comment_count")),
                reposts=_count(raw.get("retweet_count")),
                bookmarks=_count(raw.get("bookmark_count")),
                image_urls=list(dict.fromkeys(u for value in images if (u := _http_url(value)))),
                video_url=_http_url(raw.get("video_url")),
                video_thumbnail_url=_http_url(raw.get("video_thumbnail_url")),
                published_at=_datetime(raw.get("timestamp")),
            )
        )
        if len(items) >= limit:
            break
    return items


# --- Product Hunt -------------------------------------------------------------------

_PRODUCT_HUNT_QUERY = """
query ProductHuntDaily(
  $first: Int!, $after: String, $postedAfter: DateTime!, $postedBefore: DateTime!
) {
  posts(
    first: $first, after: $after, order: RANKING, featured: true,
    postedAfter: $postedAfter, postedBefore: $postedBefore
  ) {
    edges { node { id name tagline url website votesCount commentsCount createdAt
                   thumbnail { url } } }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def product_hunt_day(now: datetime, variant: str) -> date:
    """The Los Angeles calendar day that "today" / "yesterday" means right now."""
    today = now.astimezone(PRODUCT_HUNT_TIMEZONE).date()
    return today if variant == "today" else today - timedelta(days=1)


def product_hunt_bounds(day: date) -> tuple[datetime, datetime]:
    """Half-open UTC bounds of a Los Angeles day (23–25 hours around DST)."""
    start = datetime.combine(day, time.min, tzinfo=PRODUCT_HUNT_TIMEZONE)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=PRODUCT_HUNT_TIMEZONE)
    return start.astimezone(UTC), end.astimezone(UTC)


async def fetch_product_hunt(day: date, limit: int, token: str | None) -> list[BoardItem]:
    """Featured posts of one Los Angeles day, in Product Hunt's ranking order."""
    if not token:
        raise ScanError(
            "product_hunt_not_configured", "APP_PRODUCT_HUNT_TOKEN is required.", retryable=False
        )
    after_bound, before_bound = product_hunt_bounds(day)
    items: list[BoardItem] = []
    cursor: str | None = None
    seen: set[str] = set()
    with upstream_errors("product_hunt"):
        async with _client() as client:
            while len(items) < limit:
                response = await bounded_request(
                    client,
                    "POST",
                    PRODUCT_HUNT_API_URL,
                    max_bytes=MAX_BYTES,
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "query": _PRODUCT_HUNT_QUERY,
                        "variables": {
                            "first": min(50, limit - len(items)),
                            "after": cursor,
                            "postedAfter": after_bound.isoformat(),
                            "postedBefore": before_bound.isoformat(),
                        },
                    },
                )
                _check(response, "product_hunt")
                payload = response.json()
                try:
                    if payload.get("errors"):
                        raise ValueError
                    posts = payload["data"]["posts"]
                    edges, page = posts["edges"], posts["pageInfo"]
                    for edge in edges:
                        node = edge["node"]
                        thumbnail = node.get("thumbnail") or {}
                        image = _http_url(thumbnail.get("url"))
                        items.append(
                            BoardItem(
                                rank=len(items) + 1,
                                title=_required(node.get("name")),
                                url=_required(_http_url(node.get("url"))),
                                source_label="Product Hunt",
                                description=_text(node.get("tagline")),
                                score=_count(node.get("votesCount")),
                                comments=_count(node.get("commentsCount")),
                                image_urls=[image] if image else [],
                                website_url=_http_url(node.get("website")),
                                published_at=_datetime(node.get("createdAt")),
                            )
                        )
                    following = page.get("endCursor")
                    if not page.get("hasNextPage") or not edges:
                        break
                    if not isinstance(following, str) or following in seen:
                        raise ValueError
                except (AttributeError, KeyError, TypeError, ValueError):
                    raise _invalid("product_hunt") from None
                seen.add(following)
                cursor = following
    return items[:limit]


# --- Reddit -------------------------------------------------------------------------

# variant -> (sort, time filter)
REDDIT_VARIANTS = {
    "hot": ("hot", None),
    "rising": ("rising", None),
    "top-day": ("top", "day"),
    "top-week": ("top", "week"),
    "top-month": ("top", "month"),
    "top-year": ("top", "year"),
}
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")


def normalize_subreddit(value: str) -> str:
    name = value.strip().removeprefix("/").removeprefix("r/").strip("/").strip()
    if not _SUBREDDIT.fullmatch(name):
        raise ValueError("Invalid subreddit name.")
    return name.lower()


async def fetch_reddit(subreddit: str, variant: str, limit: int) -> list[BoardItem]:
    """A subreddit ranking through Reddit's public RSS view (its JSON refuses servers)."""
    sort, period = REDDIT_VARIANTS[variant]
    query = {"limit": str(limit)} | ({"t": period} if period else {})
    url = f"https://www.reddit.com/r/{subreddit}/{sort}/.rss?{urlencode(query)}"
    with upstream_errors("reddit"):
        async with _client() as client:
            response = await _get(client, url, "reddit")
    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise _invalid("reddit")
    label = f"r/{subreddit} · " + ("Top · " + period if period else sort.title())
    items = []
    for entry in feed.entries:
        title = str(entry.get("title") or "").strip()
        link = _http_url(str(entry.get("link") or "").strip())
        if not title or not link:
            continue
        soup = BeautifulSoup(str(entry.get("summary") or ""), "html.parser")
        items.append(
            BoardItem(
                rank=len(items) + 1,
                title=title,
                url=link,
                source_label=label,
                description=soup.get_text(" ", strip=True)[:280] or None,
                author=str(entry.get("author") or "").strip() or None,
                image_urls=[
                    src
                    for image in soup.find_all("img", src=True)
                    if (src := _http_url(str(image["src"]).strip()))
                ][:4],
                published_at=_feed_datetime(entry),
            )
        )
        if len(items) >= limit:
            break
    return items


# --- Small parsers ------------------------------------------------------------------


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError
    return value


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    try:
        count = int(value)
    except ValueError:
        return None
    return count if count >= 0 else None


def _number(text: str) -> int | None:
    try:
        return int(text.replace(",", "").strip())
    except ValueError:
        return None


def _linked_count(article: Any, suffix: str) -> int | None:
    node = article.select_one(f"a[href$='{suffix}']")
    return _number(node.get_text(" ", strip=True)) if node else None


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    return value if parsed.scheme in {"http", "https"} and parsed.hostname else None


def _host(url: str) -> str | None:
    host = urlsplit(url).hostname
    return host.removeprefix("www.") if host else None


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _feed_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        if parsed := entry.get(key):
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    return None
