"""Live, read-only preview adapters for public ranking pages.

These are deliberately not persisted as subscription content yet. The mobile
Explore preview lets us validate each provider's real fields before choosing a
stable board data model and final card design.
"""

from __future__ import annotations

import asyncio
import calendar
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import feedparser
import httpx
from bs4 import BeautifulSoup


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


async def fetch_github_trending(limit: int = 20) -> list[RankingItem]:
    """Parse GitHub's public weekly Trending page into repository cards."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(
            "https://github.com/trending?since=weekly",
            headers={"User-Agent": "ReaderLearningApp/0.1 (+https://example.invalid)"},
        )
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    items: list[RankingItem] = []
    # GitHub Trending intentionally exposes a single Top-25 page.
    articles = soup.select("article.Box-row")[: min(_bounded_limit(limit), 25)]
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
        period_match = re.search(r"([\d,]+)\s+stars\s+this\s+week", period_text, re.IGNORECASE)
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
