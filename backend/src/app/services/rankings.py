"""Public ranking provider adapters; saved posts use a separate archive path."""

from __future__ import annotations

import asyncio
import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.core.settings import get_settings

BODY_SEGMENT_MAX_UTF16_UNITS = 8_000


@dataclass(frozen=True)
class RankingItem:
    rank: int
    title: str
    url: str
    source_label: str | None = None
    description: str | None = None
    author: str | None = None
    score: int | None = None
    comments: int | None = None
    language: str | None = None
    stars: int | None = None
    forks: int | None = None
    stars_this_period: int | None = None
    image_urls: tuple[str, ...] = ()
    native_id: str | None = None
    published_at: datetime | None = None
    body_text: str | None = None
    author_handle: str | None = None
    retweet_count: int | None = None
    bookmark_count: int | None = None
    video_url: str | None = None
    video_thumbnail_url: str | None = None
    product_id: str | None = None
    website_url: str | None = None


PRODUCT_HUNT_TIMEZONE = ZoneInfo("America/Los_Angeles")
PRODUCT_HUNT_API_URL = "https://api.producthunt.com/v2/api/graphql"
_PRODUCT_HUNT_POSTS_QUERY = """
query ProductHuntDaily(
  $first: Int!, $after: String, $postedAfter: DateTime!, $postedBefore: DateTime!
) {
  posts(
    first: $first, after: $after, order: RANKING, featured: true,
    postedAfter: $postedAfter, postedBefore: $postedBefore
  ) {
    edges {
      node { id name tagline url website votesCount commentsCount createdAt thumbnail { url } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


class ProductHuntError(RuntimeError):
    """Product Hunt returned an unusable response, not an empty board."""


def product_hunt_bounds(day: date) -> tuple[datetime, datetime]:
    """Calendar-day half-open bounds; UTC duration varies across DST transitions."""
    start = datetime.combine(day, time.min, tzinfo=PRODUCT_HUNT_TIMEZONE)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=PRODUCT_HUNT_TIMEZONE)
    return start.astimezone(UTC), end.astimezone(UTC)


async def fetch_product_hunt_daily(day: date, limit: int = 100) -> tuple[list[RankingItem], bool]:
    """Fetch featured posts in API RANKING order for an LA calendar day.

    Historical votes and positions are observed now, not closing-day records.
    """
    token = get_settings().product_hunt_token
    if token is None or not token.get_secret_value():
        raise ProductHuntError("Product Hunt is not configured.")
    after_bound, before_bound = product_hunt_bounds(day)
    target = min(max(limit, 1), 100)
    items: list[RankingItem] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        while len(items) < target:
            response = await client.post(
                PRODUCT_HUNT_API_URL,
                headers={"Authorization": f"Bearer {token.get_secret_value()}"},
                json={
                    "query": _PRODUCT_HUNT_POSTS_QUERY,
                    "variables": {
                        "first": min(50, target - len(items)),
                        "after": cursor,
                        "postedAfter": after_bound.isoformat(),
                        "postedBefore": before_bound.isoformat(),
                    },
                },
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProductHuntError("Product Hunt returned invalid JSON.") from exc
            if not isinstance(payload, dict) or payload.get("errors"):
                raise ProductHuntError("Product Hunt API returned errors.")
            connection = payload.get("data")
            connection = connection.get("posts") if isinstance(connection, dict) else None
            if not isinstance(connection, dict):
                raise ProductHuntError("Product Hunt posts response is missing.")
            edges, page_info = connection.get("edges"), connection.get("pageInfo")
            if not isinstance(edges, list) or not isinstance(page_info, dict):
                raise ProductHuntError("Product Hunt pagination response is invalid.")
            for edge in edges:
                node = edge.get("node") if isinstance(edge, dict) else None
                if not isinstance(node, dict):
                    raise ProductHuntError("Product Hunt post is invalid.")
                product_id = _optional_text(node.get("id"))
                title = _optional_text(node.get("name"))
                url = _http_url(node.get("url"))
                if product_id is None or title is None or url is None:
                    raise ProductHuntError("Product Hunt post is incomplete.")
                thumbnail = node.get("thumbnail")
                image_url = _http_url(thumbnail.get("url")) if isinstance(thumbnail, dict) else None
                items.append(
                    RankingItem(
                        rank=len(items) + 1,
                        title=title,
                        url=url,
                        source_label="Product Hunt",
                        description=_optional_text(node.get("tagline")),
                        score=_optional_count(node.get("votesCount")),
                        comments=_optional_count(node.get("commentsCount")),
                        image_urls=(image_url,) if image_url else (),
                        native_id=product_id,
                        product_id=product_id,
                        website_url=_http_url(node.get("website")),
                        published_at=_parse_provider_datetime(node.get("createdAt")),
                    )
                )
            has_next = page_info.get("hasNextPage")
            if not isinstance(has_next, bool) or len(items) > target:
                raise ProductHuntError("Product Hunt pagination response is invalid.")
            if not has_next:
                return items, False
            if not edges:
                raise ProductHuntError("Product Hunt pagination did not advance.")
            if len(items) >= target:
                return items, True
            next_cursor = page_info.get("endCursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                raise ProductHuntError("Product Hunt pagination did not advance.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
    return items, False


async def fetch_hacker_news(limit: int = 20) -> list[RankingItem]:
    """Fetch current HN Top Stories from its official Firebase API."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
        response.raise_for_status()
        ids = response.json()
        if not isinstance(ids, list):
            raise ValueError("Unexpected Hacker News ranking response.")
        target = _bounded_limit(limit)
        # Top Stories also contains job posts. Fetch a small buffer so the
        # result can still contain up to ``target`` actual stories.
        selected_ids = ids[: min(target + 20, 500)]
        semaphore = asyncio.Semaphore(20)

        async def get_item(index: int, item_id: int) -> tuple[int, httpx.Response]:
            async with semaphore:
                item_response = await client.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
                )
            if item_response.status_code == 429:
                item_response.raise_for_status()
            return index, item_response

        tasks = [
            asyncio.create_task(get_item(index, item_id))
            for index, item_id in enumerate(selected_ids)
        ]
        raw_items: list[httpx.Response | None] = [None] * len(tasks)
        try:
            for completed in asyncio.as_completed(tasks):
                index, item_response = await completed
                raw_items[index] = item_response
        except BaseException:
            # ``gather`` propagates without cancelling siblings. Explicitly
            # reclaim queued/in-flight item requests while preserving the
            # original HTTPStatusError and its Retry-After response.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    items: list[RankingItem] = []
    upstream_errors: list[httpx.Response] = []
    for rank, response in enumerate(raw_items, start=1):
        assert response is not None
        if response.is_error:
            upstream_errors.append(response)
            continue
        raw = response.json()
        if not isinstance(raw, dict) or raw.get("type") != "story" or not raw.get("title"):
            continue
        item_id = raw.get("id")
        external_url = str(raw.get("url") or f"https://news.ycombinator.com/item?id={item_id}")
        items.append(
            RankingItem(
                rank=rank,
                title=str(raw["title"]),
                url=external_url,
                source_label=_host_label(external_url),
                author=str(raw.get("by") or "") or None,
                score=_as_int(raw.get("score")),
                comments=_as_int(raw.get("descendants")),
            )
        )
    if not items and upstream_errors:
        upstream_errors[0].raise_for_status()
    return items[:target]


async def fetch_reddit_ranking(
    subreddit: str, sort: str = "hot", time_filter: str = "week", limit: int = 20
) -> list[RankingItem]:
    """Fetch a public subreddit ranking through Reddit's RSS surface.

    Reddit's anonymous JSON endpoint returns 403 in our real test. Its RSS
    ranking view is already the project's proven collection path, so this
    preview intentionally uses the same dependable public surface.
    """
    normalized_subreddit = subreddit.strip().removeprefix("r/").strip()
    normalized_sort = sort.strip().lower()
    normalized_time_filter = time_filter.strip().lower()
    if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", normalized_subreddit):
        raise ValueError("Invalid subreddit name.")
    if normalized_sort not in {"hot", "rising", "top"}:
        raise ValueError("Reddit ranking must be hot, rising, or top.")
    if normalized_sort == "top" and normalized_time_filter not in {"day", "week", "month", "year"}:
        raise ValueError("Reddit top ranking needs day, week, month, or year.")

    query = {"limit": str(_bounded_limit(limit))}
    if normalized_sort == "top":
        query["t"] = normalized_time_filter
    url = (
        f"https://www.reddit.com/r/{normalized_subreddit}/{normalized_sort}/.rss?{urlencode(query)}"
    )
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            url,
            headers={"User-Agent": "ReaderLearningApp/0.1 (+https://example.invalid)"},
        )
        response.raise_for_status()
    parsed = feedparser.parse(response.content)
    items: list[RankingItem] = []
    for rank, entry in enumerate(parsed.entries[: _bounded_limit(limit)], start=1):
        title = str(entry.get("title") or "").strip()
        post_url = str(entry.get("link") or "").strip()
        if not title or not post_url:
            continue
        summary = str(entry.get("summary") or entry.get("description") or "")
        soup = BeautifulSoup(summary, "html.parser")
        image_urls = tuple(
            image_url
            for image in soup.find_all("img", src=True)
            if (image_url := str(image["src"]).strip()).startswith(("https://", "http://"))
        )
        items.append(
            RankingItem(
                rank=rank,
                title=title,
                url=post_url,
                source_label=(
                    f"r/{normalized_subreddit} · {normalized_sort.title()}"
                    + (f" · {normalized_time_filter}" if normalized_sort == "top" else "")
                ),
                description=soup.get_text(" ", strip=True)[:280] or None,
                author=str(entry.get("author") or "").strip() or None,
                image_urls=image_urls[:4],
                native_id=str(entry.get("id") or "").strip() or _reddit_post_id(post_url),
                published_at=_feed_datetime(entry),
            )
        )
    return items


async def fetch_github_trending(
    limit: int = 20, *, period: Literal["daily", "weekly", "monthly"] = "weekly"
) -> list[RankingItem]:
    """Parse GitHub's public Trending page into repository cards."""
    if period not in {"daily", "weekly", "monthly"}:
        raise ValueError(f"unsupported GitHub period: {period}")
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(
            "https://github.com/trending",
            params={"since": period},
            headers={"User-Agent": "ReaderLearningApp/0.1 (+https://example.invalid)"},
        )
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    items: list[RankingItem] = []
    # GitHub Trending intentionally exposes a single Top-25 page.
    articles = soup.select("article.Box-row")[: min(_bounded_limit(limit), 25)]
    period_label = {"daily": "today", "weekly": "this week", "monthly": "this month"}[period]
    period_pattern = re.compile(rf"([\d,]+)\s+stars\s+{re.escape(period_label)}\b", re.IGNORECASE)
    for rank, article in enumerate(articles, start=1):
        link = article.select_one("h2 a[href]")
        if link is None:
            continue
        path = str(link["href"])
        title = " ".join(link.stripped_strings).replace(" / ", "/")
        description_node = article.select_one("p")
        language_node = article.select_one("[itemprop='programmingLanguage']")
        stars = _linked_stat(article, "/stargazers")
        forks = _linked_stat(article, "/forks")
        period_text = article.get_text(" ", strip=True)
        period_match = period_pattern.search(period_text)
        avatars = tuple(
            str(image["src"])
            for image in article.select("img.avatar-user[src]")
            if str(image["src"]).startswith("https://")
        )
        items.append(
            RankingItem(
                rank=rank,
                title=title,
                url=f"https://github.com{path}",
                description=(
                    description_node.get_text(" ", strip=True) if description_node else None
                ),
                language=language_node.get_text(strip=True) if language_node else None,
                stars=stars,
                forks=forks,
                stars_this_period=_as_int(period_match.group(1)) if period_match else None,
                image_urls=avatars[:5],
            )
        )
    return items


def split_ranking_body(text: str) -> list[str]:
    """Keep line breaks verbatim between translated chunks; bound UTF-16 size."""
    segments: list[str] = []
    for part in re.split(r"((?:\r\n|\r|\n)+)", text):
        start = 0
        while start < len(part):
            end = start
            units = 0
            while end < len(part):
                width = 2 if ord(part[end]) > 0xFFFF else 1
                if units + width > BODY_SEGMENT_MAX_UTF16_UNITS:
                    break
                units += width
                end += 1
            segments.append(part[start:end])
            start = end
    return segments


async def fetch_techtwitter_trending(limit: int = 50) -> list[RankingItem]:
    """Fetch TechTwitter's unfiltered TRENDING order in one public API request."""
    target = min(max(int(limit), 1), 50)
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            "https://www.techtwitter.com/api/tweets/trending",
            params={"limit": target, "offset": 0},
        )
        response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("tweets"), list):
        raise ValueError("Unexpected TechTwitter trending response.")

    items: list[RankingItem] = []
    for rank, raw in enumerate(payload["tweets"][:target], start=1):
        if not isinstance(raw, dict):
            continue
        url = _http_url(raw.get("tweet_url"))
        body = raw.get("tweet_text")
        if url is None or (body is not None and not isinstance(body, str)):
            continue
        title = _optional_text(raw.get("seo_title")) or (body or "")[:160]
        if not title.strip():
            continue
        images = raw.get("image_grid_urls")
        image_candidates = [raw.get("image_url")]
        if isinstance(images, list):
            image_candidates.extend(images)
        image_urls = tuple(
            dict.fromkeys(
                image for value in image_candidates if (image := _http_url(value)) is not None
            )
        )
        native_id = raw.get("id")
        items.append(
            RankingItem(
                rank=rank,
                title=title,
                url=url,
                source_label="TechTwitter",
                description=_optional_text(raw.get("summary")),
                body_text=body,
                author=_optional_text(raw.get("author_name")),
                author_handle=_optional_text(raw.get("author_handle")),
                score=_optional_count(raw.get("like_count")),
                comments=_optional_count(raw.get("comment_count")),
                retweet_count=_optional_count(raw.get("retweet_count")),
                bookmark_count=_optional_count(raw.get("bookmark_count")),
                image_urls=image_urls,
                video_url=_http_url(raw.get("video_url")),
                video_thumbnail_url=_http_url(raw.get("video_thumbnail_url")),
                native_id=str(native_id) if native_id is not None else url,
                published_at=_parse_provider_datetime(raw.get("timestamp")),
            )
        )
    return items


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _optional_count(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        count = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return count if count >= 0 and str(count) == str(value).strip() else None


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.hostname else None
    except ValueError:
        return None


def _parse_provider_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _linked_stat(article: Any, suffix: str) -> int | None:
    node = article.select_one(f"a[href$='{suffix}']")
    return _as_int(node.get_text(" ", strip=True)) if node else None


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _host_label(url: str) -> str | None:
    match = re.match(r"https?://([^/]+)", url)
    return match.group(1).removeprefix("www.") if match else None


def _bounded_limit(limit: int) -> int:
    return min(max(int(limit), 1), 100)


def _feed_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    return None


def _reddit_post_id(url: str) -> str | None:
    match = re.search(r"/comments/([A-Za-z0-9]+)/", url)
    return f"t3_{match.group(1)}" if match else None
