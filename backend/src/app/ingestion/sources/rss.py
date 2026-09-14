"""Checkpoint-aware RSS and Atom scanner."""

from __future__ import annotations

import calendar
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.domain.enums import ContentKind, MediaType
from app.ingestion.feed_discovery import parse_usable_web_feed
from app.ingestion.html_safety import sanitize_html
from app.ingestion.http import ensure_response_size, raise_for_provider_status
from app.ingestion.models import (
    DiscoveredContent,
    DiscoveredMedia,
    SourceScanError,
    SourceScanPage,
    SourceScanRequest,
    checkpoint_for_items,
    checkpoint_ids,
)
from app.ingestion.site_icons import discover_site_icon_url
from app.ingestion.source_identity import normalize_web_article_url, web_article_native_id
from app.ingestion.url_safety import safe_get, validate_http_url

from .base import SourceAdapter

_USER_AGENT = "ReaderAggregator/0.2 (+https://example.invalid)"


@dataclass(frozen=True)
class RSSSourceConfig:
    url: str
    content_kind: ContentKind = ContentKind.ARTICLE
    profile_url: str | None = None
    site_url: str | None = None
    resolve_site_avatar: bool = False
    provider_mode: str = "rss"


class RSSSourceAdapter(SourceAdapter):
    def __init__(self, config: RSSSourceConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    async def scan_page(self, request: SourceScanRequest) -> SourceScanPage:
        page_url = self._config.url
        if request.continuation:
            page_url = await self._validated_continuation_url(
                request.continuation, request.max_response_bytes
            )

        headers = {"User-Agent": _USER_AGENT}
        validators = request.checkpoint.get("validators")
        if (
            request.conditional
            and not request.initial
            and request.continuation is None
            and isinstance(validators, dict)
        ):
            if validators.get("etag"):
                headers["If-None-Match"] = str(validators["etag"])
            if validators.get("last_modified"):
                headers["If-Modified-Since"] = str(validators["last_modified"])

        response = await safe_get(
            self._client,
            page_url,
            headers=headers,
            timeout=20.0,
            max_response_bytes=request.max_response_bytes,
        )
        if response.status_code == httpx.codes.NOT_MODIFIED:
            return SourceScanPage(
                items=(),
                provider_mode=self._config.provider_mode,
                raw_items=0,
                completed=True,
                checkpoint=request.checkpoint,
            )
        try:
            raise_for_provider_status(response, self._config.provider_mode)
            ensure_response_size(response, request.max_response_bytes)
            parsed = self._parse_feed(response)
            if getattr(parsed, "bozo", False) and not parsed.entries:
                raise SourceScanError("invalid_feed", "The RSS/Atom response could not be parsed.")
        except SourceScanError as exc:
            if self._config.provider_mode == "web_rss":
                exc.evidence["feed_head"] = request.continuation is None
            raise
        raw_entries = list(parsed.entries[: request.max_raw_items])
        raw_truncated = len(parsed.entries) > request.max_raw_items
        normalized = tuple(
            item for entry in raw_entries if (item := self._entry_to_content(entry)) is not None
        )
        anchors = checkpoint_ids(request.checkpoint)
        visible, overlapped = _through_oldest_anchor(normalized, anchors)
        next_url = self._next_archive_url(parsed.feed, str(response.url))
        # An archive link cannot safely skip the unexamined tail of an oversized
        # current document.  Treat that as an explicit bounded-window gap rather
        # than jumping to the archive and silently losing entries.
        completed = request.initial or overlapped or next_url is None or raw_truncated
        gap_detected = bool(
            not request.initial
            and anchors
            and not overlapped
            and (next_url is None or raw_truncated)
        )
        validators_out = {
            key: value
            for key, value in {
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
            }.items()
            if value
        }
        checkpoint = checkpoint_for_items(
            normalized,
            validators=validators_out,
            extra={"feed_url": self._config.url},
        )
        continuation = None
        if not completed and next_url:
            continuation = {
                "replay_url": str(response.url),
                "boundary_ids": [item.native_id for item in normalized],
                "next_url": next_url,
            }
        avatar = self._feed_avatar_url(parsed.feed)
        if avatar is None and self._config.profile_url:
            avatar = await self._profile_avatar_url(
                self._config.profile_url, request.max_response_bytes
            )
        if avatar is None and self._config.resolve_site_avatar and request.continuation is None:
            site_url = str(parsed.feed.get("link") or self._config.site_url or "").strip()
            if site_url:
                avatar = await self._site_avatar_url(site_url, request.max_response_bytes)
        return SourceScanPage(
            items=visible,
            provider_mode=self._config.provider_mode,
            raw_items=len(raw_entries),
            completed=completed,
            next_continuation=continuation,
            checkpoint=checkpoint,
            boundary_ids=tuple(item.native_id for item in normalized),
            limit_reached=(
                "initial_history_truncated"
                if request.initial and raw_truncated
                else "raw_items"
                if raw_truncated and not overlapped
                else None
            ),
            gap_detected=gap_detected,
            gap_details=(
                {
                    "reason": (
                        "feed_page_exceeds_raw_window"
                        if raw_truncated
                        else "archive_unavailable_before_checkpoint"
                    ),
                    "oldest_visible_id": normalized[-1].native_id if normalized else None,
                }
                if gap_detected
                else None
            ),
            source_display_name=str(parsed.feed.get("title") or "").strip() or None,
            source_avatar_url=avatar,
        )

    async def _validated_continuation_url(
        self, continuation: dict[str, Any], maximum_bytes: int
    ) -> str:
        replay_url = str(continuation.get("replay_url") or "")
        next_url = str(continuation.get("next_url") or "")
        expected = {str(item) for item in continuation.get("boundary_ids", []) if item}
        if not replay_url or not next_url or not expected:
            raise SourceScanError("cursor_invalid", "RSS continuation is incomplete.")
        replay = await safe_get(
            self._client,
            replay_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=20.0,
            max_response_bytes=maximum_bytes,
        )
        raise_for_provider_status(replay, self._config.provider_mode)
        ensure_response_size(replay, maximum_bytes)
        parsed = self._parse_feed(replay)
        actual = {
            item.native_id
            for entry in parsed.entries
            if (item := self._entry_to_content(entry)) is not None
        }
        if not actual.intersection(expected):
            raise SourceScanError(
                "cursor_invalid", "RSS archive shifted before its boundary could be verified."
            )
        current_next = self._next_archive_url(parsed.feed, str(replay.url))
        if current_next != next_url:
            raise SourceScanError(
                "cursor_invalid", "RSS archive continuation changed after it was saved."
            )
        return next_url

    def _parse_feed(self, response: httpx.Response) -> Any:
        if self._config.provider_mode != "web_rss":
            return feedparser.parse(response.content)
        parsed = parse_usable_web_feed(response.content, str(response.url))
        if parsed is None:
            raise SourceScanError(
                "invalid_feed", "The selected RSS/Atom URL no longer returns a feed."
            )
        return parsed

    def _entry_to_content(self, entry: Any) -> DiscoveredContent | None:
        external_url = str(entry.get("link") or "").strip()
        identity = str(entry.get("id") or entry.get("guid") or external_url).strip()
        if self._config.provider_mode == "web_rss":
            try:
                validate_http_url(external_url)
            except ValueError:
                return None
            if not str(entry.get("title") or "").strip():
                return None
            external_url = normalize_web_article_url(external_url, keep_query=True)
            identity = web_article_native_id(external_url)
        if self._config.provider_mode == "youtube_atom":
            identity = str(entry.get("yt_videoid") or identity.removeprefix("yt:video:")).strip()
        if not external_url or not identity:
            return None
        excerpt_html = sanitize_html(self._extract_html(entry))
        return DiscoveredContent(
            native_id=_bounded_native_id(identity),
            kind=self._config.content_kind,
            title=str(entry.get("title") or "Untitled").strip() or "Untitled",
            external_url=external_url,
            published_at=self._parse_datetime(entry, ("published", "created", "updated")),
            source_updated_at=self._parse_datetime(entry, ("updated",)),
            author_name=str(entry.get("author") or "").strip() or None,
            excerpt_html=excerpt_html,
            raw_metadata={
                "tags": [str(tag.get("term")) for tag in entry.get("tags", []) if tag.get("term")],
            },
            media=self._discover_media(entry, excerpt_html),
        )

    @staticmethod
    def _extract_html(entry: Any) -> str | None:
        content = entry.get("content")
        if content:
            value = content[0].get("value") if isinstance(content, list) else None
            if value:
                return str(value)
        return str(entry.get("summary") or entry.get("description") or "") or None

    @classmethod
    def _parse_datetime(cls, entry: Any, fields: tuple[str, ...]) -> datetime | None:
        for field_name in fields:
            parsed = entry.get(f"{field_name}_parsed")
            if parsed:
                return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
            raw_value = entry.get(field_name)
            if raw_value:
                try:
                    return cls._to_utc(parsedate_to_datetime(raw_value))
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _next_archive_url(feed: Any, current_url: str) -> str | None:
        links = feed.get("links", [])
        # RFC 5005 subscription/archive documents walk backwards through
        # ``prev-archive``.  Ordinary paged feeds commonly expose the older
        # page as ``next``.  ``next-archive`` points towards newer archives and
        # must not be followed from the current boundary.
        for relation in ("prev-archive", "next"):
            for link in links:
                if str(link.get("rel") or "").lower() == relation and link.get("href"):
                    return urljoin(current_url, str(link["href"]))
        return None

    @staticmethod
    def _discover_media(entry: Any, html: str | None) -> tuple[DiscoveredMedia, ...]:
        urls: list[str] = []
        for media_field in ("media_thumbnail", "media_content", "thumbnail"):
            values = entry.get(media_field, [])
            if not isinstance(values, list):
                values = [values]
            for value in values:
                source = str(value.get("url") if isinstance(value, dict) else "").strip()
                if source.startswith(("https://", "http://")) and source not in urls:
                    urls.append(source)
        if html:
            for image in BeautifulSoup(html, "html.parser").find_all("img", src=True):
                source = str(image["src"]).strip()
                if source.startswith(("https://", "http://")) and source not in urls:
                    urls.append(source)
        return tuple(
            DiscoveredMedia(original_url=url, media_type=MediaType.IMAGE, sort_order=index)
            for index, url in enumerate(urls)
        )

    @staticmethod
    def _feed_avatar_url(feed: Any) -> str | None:
        image = feed.get("image")
        candidates = (
            image.get("href") if isinstance(image, dict) else None,
            image.get("url") if isinstance(image, dict) else None,
            feed.get("icon"),
            feed.get("logo"),
        )
        return next(
            (
                str(candidate).strip()
                for candidate in candidates
                if isinstance(candidate, str)
                and candidate.strip().startswith(("https://", "http://"))
            ),
            None,
        )

    async def _profile_avatar_url(self, profile_url: str, maximum_bytes: int) -> str | None:
        try:
            response = await safe_get(
                self._client,
                profile_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=20.0,
                max_response_bytes=maximum_bytes,
            )
        except (httpx.HTTPError, SourceScanError, ValueError):
            return None
        if response.is_error:
            return None
        if response.text.lstrip().lower().startswith(("<?xml", "<feed", "<rss")):
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        image = soup.find("meta", attrs={"property": "og:image"})
        candidate = image.get("content") if image is not None else None
        if isinstance(candidate, str) and candidate.strip().startswith(("https://", "http://")):
            return candidate.strip()
        return None

    async def _site_avatar_url(self, site_url: str, maximum_bytes: int) -> str | None:
        try:
            response = await safe_get(
                self._client,
                site_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=20.0,
                max_response_bytes=maximum_bytes,
            )
            if response.is_error:
                return None
            return discover_site_icon_url(response.text, str(response.url))
        except (httpx.HTTPError, SourceScanError, ValueError):
            return None

    @staticmethod
    def _to_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _through_oldest_anchor(
    items: tuple[DiscoveredContent, ...], anchors: set[str]
) -> tuple[tuple[DiscoveredContent, ...], bool]:
    if not anchors:
        return items, False
    matches = [index for index, item in enumerate(items) if item.native_id in anchors]
    return (items[: max(matches) + 1], True) if matches else (items, False)


def _bounded_native_id(identity: str) -> str:
    if len(identity) <= 1024:
        return identity
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
