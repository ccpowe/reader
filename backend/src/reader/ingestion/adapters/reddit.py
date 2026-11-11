"""A subreddit's current hot posts, read from Reddit's public RSS view.

Reddit's anonymous JSON endpoint refuses servers; its RSS view works. The hot
list is ordered by popularity, not time, so the page is marked
non-chronological: every post on it is checked, none ends the scan early.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

import httpx

from reader.ingestion.adapters.rss import RssAdapter
from reader.ingestion.items import Item, Page

HOT_POSTS = 10
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")


def hot_feed_url(subreddit: str, limit: int = HOT_POSTS) -> str:
    if not _SUBREDDIT.fullmatch(subreddit):
        raise ValueError("Invalid subreddit name.")
    return f"https://www.reddit.com/r/{subreddit}/hot/.rss?{urlencode({'limit': limit})}"


class RedditHotAdapter:
    def __init__(self, client: httpx.AsyncClient, *, subreddit: str, max_bytes: int) -> None:
        self._subreddit = subreddit
        self._feed = RssAdapter(
            client,
            hot_feed_url(subreddit),
            max_bytes=max_bytes,
            kind="post",
            provider="reddit",
            newest_first=False,
        )

    async def fetch_page(self, cursor: str | None, limit: int) -> Page:
        page = await self._feed.fetch_page(None, min(limit, HOT_POSTS))
        items = [
            Item(
                native_id=item.native_id,
                kind="post",
                title=item.title,
                url=item.url,
                published_at=item.published_at,
                author=item.author,
                summary=item.summary,
                body_html=item.body_html,
                media=item.media,
                extra={"subreddit": self._subreddit},
            )
            for item in page.items
        ]
        return Page(items=items, chronological=False, source_name=f"r/{self._subreddit}")
