"""Page-oriented adapter for the private Scweet collector service."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
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
from app.services.x_relations import normalize_x_relations

from .base import SourceAdapter


@dataclass(frozen=True)
class ScweetXSourceConfig:
    handle: str
    service_url: str
    service_token: str


class ScweetXSourceAdapter(SourceAdapter):
    def __init__(self, config: ScweetXSourceConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    async def scan_page(self, request: SourceScanRequest) -> SourceScanPage:
        continuation = request.continuation or {}
        body: dict[str, Any] = {
            "usernames": [self._config.handle],
            "limit": min(request.max_raw_items, 50),
        }
        if continuation:
            if continuation.get("provider") != "scweet":
                raise SourceScanError("cursor_invalid", "Scweet continuation provider changed.")
            if continuation.get("cursor"):
                body["cursor"] = continuation["cursor"]
            body["boundary_ids"] = continuation.get("boundary_ids", [])
        response = await self._client.post(
            f"{self._config.service_url.rstrip('/')}/v1/profiles/tweets",
            headers={"Authorization": f"Bearer {self._config.service_token}"},
            json=body,
            timeout=180.0,
        )
        if response.is_error:
            code = "scweet_http_error"
            detail = response.text[:1000]
            try:
                error = response.json().get("detail")
                if isinstance(error, dict):
                    code = str(error.get("code") or code)
                    detail = str(error.get("message") or detail)
                elif error:
                    detail = str(error)
            except (ValueError, AttributeError):
                pass
            raise SourceScanError(
                code,
                f"Scweet service request failed ({response.status_code}): {detail}",
                retry_after_seconds=900 if code == "rate_limited" else None,
                long_lived=code in {"auth_failed", "manifest_failed", "protected_account"},
            )
        ensure_response_size(response, request.max_response_bytes)
        payload = response.json()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            raise SourceScanError("scweet_invalid_response", "Unexpected Scweet response.")
        normalized = tuple(
            item
            for raw in raw_items
            if isinstance(raw, dict)
            if (item := self._to_content(raw)) is not None
        )
        anchors = checkpoint_ids(request.checkpoint)
        visible, overlapped = _through_oldest_anchor(normalized, anchors)
        service_completed = bool(payload.get("completed"))
        cursor = payload.get("continuation")
        completed = request.initial or overlapped or (service_completed and not cursor)
        gap_detected = bool(not request.initial and anchors and not overlapped and completed)
        next_continuation = None
        if not completed and cursor:
            next_continuation = {
                "provider": "scweet",
                "cursor": cursor,
                "boundary_ids": list(
                    payload.get("boundary_ids") or [item.native_id for item in normalized]
                ),
            }
        avatar_url = next(
            (
                candidate
                for raw in raw_items
                if isinstance(raw, dict)
                if (candidate := self._profile_avatar_url(raw)) is not None
            ),
            None,
        )
        if avatar_url is None and request.continuation is None:
            avatar_url = await self._lookup_profile_avatar()
        return SourceScanPage(
            items=visible,
            provider_mode="scweet",
            raw_items=len(raw_items),
            completed=completed,
            next_continuation=next_continuation,
            checkpoint=checkpoint_for_items(visible),
            boundary_ids=tuple(item.native_id for item in normalized),
            # Provider page-size is not a coverage limit once a rolling anchor
            # has already proved overlap on this page.
            limit_reached=(
                str(payload.get("limit_reached"))
                if payload.get("limit_reached") and not overlapped
                else None
            ),
            gap_detected=gap_detected,
            gap_details=(
                {"reason": "scweet_timeline_exhausted_before_checkpoint"} if gap_detected else None
            ),
            source_avatar_url=avatar_url,
        )

    def _to_content(self, raw: dict[str, Any]) -> DiscoveredContent | None:
        native_id = str(raw.get("tweet_id") or raw.get("id") or "").strip()
        external_url = str(raw.get("tweet_url") or raw.get("url") or "").strip()
        text = str(raw.get("text") or raw.get("full_text") or "").strip()
        if not native_id or not external_url or not text:
            return None
        published_at = self._parse_datetime(raw.get("timestamp") or raw.get("date"))
        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        handle = str(user.get("screen_name") or raw.get("username") or self._config.handle).strip()
        relations = normalize_x_relations(raw)
        return DiscoveredContent(
            native_id=native_id,
            kind=ContentKind.POST,
            title=text.replace("\n", " ")[:180],
            external_url=external_url,
            published_at=published_at,
            source_updated_at=self._parse_datetime(raw.get("edited_at")),
            author_name=f"@{handle.removeprefix('@')}",
            excerpt_html=html.escape(text).replace("\n", "<br>"),
            raw_metadata={
                "timeline_event": str(raw.get("timeline_event") or "tweet"),
                "is_reply": bool(relations["reply_to"] or raw.get("is_reply")),
                "is_retweet": relations["is_repost"],
                "x": relations,
                "is_pinned": bool(raw.get("is_pinned")),
                "quoted_tweet": self._stable_reference(raw.get("quoted_tweet") or raw.get("quote")),
                "referenced_tweet": self._stable_reference(raw.get("referenced_tweet")),
                "stats": {
                    key: raw.get(key)
                    for key in ("likes", "retweets", "comments", "views", "bookmarks")
                },
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
        if isinstance(raw_media, dict):
            image_links = raw_media.get("image_links", [])
            video_links = raw_media.get("video_links", [])
            values = [
                (value, MediaType.IMAGE) for value in image_links if isinstance(value, str)
            ] + [(value, MediaType.VIDEO) for value in video_links if isinstance(value, str)]
        elif isinstance(raw_media, list):
            values = [
                (
                    str(value.get("url") or ""),
                    MediaType.VIDEO if value.get("type") == "video" else MediaType.IMAGE,
                )
                for value in raw_media
                if isinstance(value, dict)
            ]
        else:
            values = []
        return tuple(
            DiscoveredMedia(original_url=url.strip(), media_type=media_type, sort_order=index)
            for index, (url, media_type) in enumerate(values)
            if url.strip().startswith(("https://", "http://"))
        )

    @staticmethod
    def _profile_avatar_url(raw: dict[str, Any]) -> str | None:
        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        candidate = user.get("profile_image_url")
        if not isinstance(candidate, str):
            return None
        candidate = candidate.strip()
        return candidate if candidate.startswith(("https://", "http://")) else None

    async def _lookup_profile_avatar(self) -> str | None:
        response = await self._client.post(
            f"{self._config.service_url.rstrip('/')}/v1/profiles/info",
            headers={"Authorization": f"Bearer {self._config.service_token}"},
            json={"usernames": [self._config.handle]},
            timeout=60.0,
        )
        if response.is_error:
            return None
        payload = response.json()
        rows = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate = row.get("profile_image_url")
            if isinstance(candidate, str) and candidate.strip().startswith(("https://", "http://")):
                return candidate.strip()
        return None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _through_oldest_anchor(
    items: tuple[DiscoveredContent, ...], anchors: set[str]
) -> tuple[tuple[DiscoveredContent, ...], bool]:
    if not anchors:
        return items, False
    # Scweet does not always supply is_pinned. Conservatively treat the first
    # row as a possible pin even when the flag is missing; it cannot prove
    # overlap with the chronological timeline by itself.
    matches = [
        index
        for index, item in enumerate(items)
        if index > 0
        and not item.raw_metadata.get("is_pinned")
        and item.native_id in anchors
    ]
    return (items[: max(matches) + 1], True) if matches else (items, False)
