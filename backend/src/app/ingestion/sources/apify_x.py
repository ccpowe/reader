"""Explicit compatibility adapter for X through Apify.

This path is selected only with ``APP_X_PROVIDER=apify``. It is never an
automatic fallback from Scweet because the two providers cannot share a
provable pagination checkpoint.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.domain.enums import ContentKind, MediaType
from app.ingestion.http import ensure_response_size
from app.ingestion.models import (
    DiscoveredContent,
    DiscoveredMedia,
    SourceScanError,
    SourceScanPage,
    SourceScanRequest,
    checkpoint_for_items,
    checkpoint_ids,
)

from .base import SourceAdapter


@dataclass(frozen=True)
class ApifyXSourceConfig:
    handle: str
    token: str


class ApifyXSourceAdapter(SourceAdapter):
    _run_url = (
        "https://api.apify.com/v2/acts/"
        "mikolabs~twitter-x-profile-scraper/run-sync-get-dataset-items"
    )

    def __init__(self, config: ApifyXSourceConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    async def scan_page(self, request: SourceScanRequest) -> SourceScanPage:
        if request.continuation:
            raise SourceScanError(
                "apify_cursor_unavailable",
                "The configured Apify actor does not expose a verifiable continuation.",
            )
        requested = min(request.max_raw_items, 50)
        response = await self._client.post(
            self._run_url,
            headers={"Authorization": f"Bearer {self._config.token}"},
            json={
                "twitterHandles": [self._config.handle],
                "includeReplies": True,
                "mediaOnly": False,
                "maxItems": requested,
                "maxRetries": 1,
            },
            timeout=300.0,
        )
        if response.is_error:
            raise SourceScanError(
                f"apify_http_{response.status_code}",
                f"Apify X request failed ({response.status_code}): {response.text[:1000]}",
                long_lived=response.status_code in {401, 403},
            )
        ensure_response_size(response, request.max_response_bytes)
        payload = response.json()
        if not isinstance(payload, list):
            raise SourceScanError("apify_invalid_response", "Unexpected Apify X response.")
        normalized = tuple(
            item
            for raw in payload[:requested]
            if isinstance(raw, dict)
            if (item := self._to_content(raw)) is not None
        )
        anchors = checkpoint_ids(request.checkpoint)
        visible, overlapped = _through_oldest_anchor(normalized, anchors)
        exhausted = len(payload) < requested
        gap = bool(not request.initial and anchors and not overlapped and not exhausted)
        return SourceScanPage(
            items=visible,
            provider_mode="apify",
            raw_items=min(len(payload), requested),
            completed=True,
            checkpoint=checkpoint_for_items(normalized),
            boundary_ids=tuple(item.native_id for item in normalized),
            limit_reached="provider_window" if gap else None,
            gap_detected=gap,
            gap_details={"reason": "apify_has_no_verifiable_cursor"} if gap else None,
        )

    def _to_content(self, raw: dict[str, Any]) -> DiscoveredContent | None:
        if raw.get("type") not in {None, "tweet"}:
            return None
        native_id = str(raw.get("tweet_id") or raw.get("id") or "").strip()
        external_url = str(raw.get("tweet_url") or raw.get("url") or "").strip()
        text = str(raw.get("text") or raw.get("full_text") or "").strip()
        if not native_id or not external_url or not text:
            return None
        handle = str(raw.get("username") or self._config.handle).strip()
        return DiscoveredContent(
            native_id=native_id,
            kind=ContentKind.POST,
            title=text.replace("\n", " ")[:180],
            external_url=external_url,
            published_at=self._parse_datetime(raw.get("date")),
            source_updated_at=self._parse_datetime(raw.get("edited_at")),
            author_name=f"@{handle.removeprefix('@')}",
            excerpt_html=html.escape(text).replace("\n", "<br>"),
            raw_metadata={
                "is_reply": bool(raw.get("is_reply")),
                "is_retweet": bool(raw.get("is_retweet")),
                "is_pinned": bool(raw.get("is_pinned")),
                "quoted_tweet": self._stable_reference(raw.get("quoted_tweet")),
                "stats": raw.get("stats"),
            },
            media=self._media(raw.get("media")),
        )

    @staticmethod
    def _stable_reference(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {
            key: value.get(key)
            for key in (
                "id",
                "tweet_id",
                "text",
                "full_text",
                "url",
                "tweet_url",
                "username",
                "edited_at",
                "media",
            )
            if value.get(key) is not None
        }

    @staticmethod
    def _media(raw_media: Any) -> tuple[DiscoveredMedia, ...]:
        if not isinstance(raw_media, list):
            return ()
        return tuple(
            DiscoveredMedia(
                original_url=str(media["url"]),
                media_type=MediaType.VIDEO if media.get("type") == "video" else MediaType.IMAGE,
                sort_order=index,
            )
            for index, media in enumerate(raw_media)
            if isinstance(media, dict) and isinstance(media.get("url"), str)
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(str(value))
            except (TypeError, ValueError):
                try:
                    parsed = datetime.strptime(str(value), "%b %d, %Y · %I:%M %p UTC").replace(
                        tzinfo=UTC
                    )
                except ValueError:
                    return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _through_oldest_anchor(
    items: tuple[DiscoveredContent, ...], anchors: set[str]
) -> tuple[tuple[DiscoveredContent, ...], bool]:
    if not anchors:
        return items, False
    matches = [index for index, item in enumerate(items) if item.native_id in anchors]
    return (items[: max(matches) + 1], True) if matches else (items, False)
