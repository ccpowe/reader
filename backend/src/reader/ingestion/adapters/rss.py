"""RSS / Atom feeds: one request reads the whole feed, newest first."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup

from reader.ingestion.errors import ScanError, upstream_errors
from reader.ingestion.html_safety import plain_text, sanitize_html
from reader.ingestion.http import ensure_response_size, raise_for_provider_status
from reader.ingestion.items import ArticleKind, Item, Media, Page, bounded_native_id
from reader.ingestion.site_icons import discover_site_icon_url
from reader.ingestion.url_safety import safe_get

USER_AGENT = "Reader/3 (+https://example.invalid)"
_UNDATED = datetime.min.replace(tzinfo=UTC)


class RssAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        max_bytes: int,
        find_icon: bool = False,
        kind: ArticleKind = "article",
        youtube: bool = False,
        provider: str = "rss",
        newest_first: bool = True,
    ) -> None:
        self._client = client
        self._url = url
        self._max_bytes = max_bytes
        self._find_icon = find_icon
        self._kind = kind
        self._youtube = youtube
        self._provider = provider
        # Feeds are usually newest first but not always; popularity lists opt out.
        self._newest_first = newest_first

    async def fetch_page(self, cursor: str | None, limit: int) -> Page:
        with upstream_errors(self._provider):
            return await self._fetch(limit)

    async def _fetch(self, limit: int) -> Page:
        response = await safe_get(
            self._client,
            self._url,
            headers={"User-Agent": USER_AGENT},
            timeout=20.0,
            max_response_bytes=self._max_bytes,
        )
        raise_for_provider_status(response, self._provider)
        ensure_response_size(response, self._max_bytes)
        parsed = feedparser.parse(response.content)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise ScanError("invalid_feed", "The RSS/Atom response could not be parsed.")
        items = [item for entry in parsed.entries if (item := self._to_item(entry)) is not None]
        if self._newest_first:
            # Stable: undated items keep their feed order, after the dated ones.
            items.sort(key=lambda item: item.published_at or _UNDATED, reverse=True)
        items = items[:limit]
        feed = parsed.feed
        icon = None
        if self._find_icon:
            icon = _feed_image(feed) or await self._site_icon(str(feed.get("link") or "").strip())
        return Page(
            items=items,
            source_name=str(feed.get("title") or "").strip() or None,
            source_icon_url=icon,
        )

    def _to_item(self, entry: Any) -> Item | None:
        url = str(entry.get("link") or "").strip()
        identity = str(entry.get("id") or entry.get("guid") or url).strip()
        if self._youtube:
            identity = str(entry.get("yt_videoid") or identity.removeprefix("yt:video:")).strip()
        if not url or not identity:
            return None
        body = sanitize_html(_entry_html(entry))
        return Item(
            native_id=bounded_native_id(identity),
            kind=self._kind,
            title=str(entry.get("title") or "").strip() or "Untitled",
            url=url,
            published_at=_entry_datetime(entry),
            author=str(entry.get("author") or "").strip() or None,
            summary=plain_text(body),
            body_html=body,
            media=_entry_media(entry, body),
            extra={"tags": [str(tag["term"]) for tag in entry.get("tags", []) if tag.get("term")]},
        )

    async def _site_icon(self, site_url: str) -> str | None:
        if not site_url.startswith(("https://", "http://")):
            return None
        try:
            response = await safe_get(
                self._client,
                site_url,
                headers={"User-Agent": USER_AGENT},
                timeout=20.0,
                max_response_bytes=self._max_bytes,
            )
        except (httpx.HTTPError, ScanError, ValueError):
            return None
        if response.is_error:
            return None
        return discover_site_icon_url(response.text, str(response.url))


def _entry_html(entry: Any) -> str | None:
    content = entry.get("content")
    if isinstance(content, list) and content and content[0].get("value"):
        return str(content[0]["value"])
    return str(entry.get("summary") or entry.get("description") or "") or None


def _entry_datetime(entry: Any) -> datetime | None:
    for name in ("published", "created", "updated"):
        parsed = entry.get(f"{name}_parsed")
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
        raw = entry.get(name)
        if raw:
            try:
                value = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                continue
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return None


def _entry_media(entry: Any, html: str | None) -> tuple[Media, ...]:
    urls: list[str] = []
    for field_name in ("media_thumbnail", "media_content", "thumbnail"):
        values = entry.get(field_name, [])
        for value in values if isinstance(values, list) else [values]:
            url = str(value.get("url") if isinstance(value, dict) else "").strip()
            if url.startswith(("https://", "http://")) and url not in urls:
                urls.append(url)
    if html:
        for image in BeautifulSoup(html, "html.parser").find_all("img", src=True):
            url = str(image["src"]).strip()
            if url.startswith(("https://", "http://")) and url not in urls:
                urls.append(url)
    return tuple(Media(url) for url in urls)


def _feed_image(feed: Any) -> str | None:
    image = feed.get("image")
    candidates = (
        image.get("href") if isinstance(image, dict) else None,
        image.get("url") if isinstance(image, dict) else None,
        feed.get("icon"),
        feed.get("logo"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip().startswith(("https://", "http://")):
            return candidate.strip()
    return None
