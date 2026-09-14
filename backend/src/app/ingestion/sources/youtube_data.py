"""YouTube Data API scanner with a public Atom fallback."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import httpx

from app.domain.enums import ContentKind, MediaType
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

from .base import SourceAdapter
from .rss import RSSSourceAdapter, RSSSourceConfig

_API_ROOT = "https://www.googleapis.com/youtube/v3"


@dataclass(frozen=True)
class YouTubeSourceConfig:
    channel_id: str
    feed_url: str
    api_key: str | None
    uploads_playlist_id: str | None = None
    use_data_api: bool = True


class YouTubeSourceAdapter(SourceAdapter):
    def __init__(self, config: YouTubeSourceConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    async def scan_page(self, request: SourceScanRequest) -> SourceScanPage:
        if not self._config.api_key or not self._config.use_data_api:
            return await self._atom_page(
                request,
                warning_code="youtube_data_api_unavailable",
                warning_message=(
                    "YouTube Data API is not configured or its soft quota is exhausted."
                ),
            )
        try:
            return await self._data_api_page(request)
        except SourceScanError as exc:
            if exc.code in {
                "youtube_quota_exceeded",
                "youtube_http_400",
                "youtube_http_401",
                "youtube_http_403",
                "youtube_http_429",
            } or exc.code.startswith("youtube_http_5"):
                return await self._atom_page(
                    request,
                    warning_code=exc.code,
                    warning_message=str(exc),
                )
            raise

    async def _data_api_page(self, request: SourceScanRequest) -> SourceScanPage:
        api_checkpoint = _nested_checkpoint(request.checkpoint, "data_api")
        uploads_id = self._config.uploads_playlist_id or str(
            api_checkpoint.get("uploads_playlist_id") or ""
        )
        display_name: str | None = None
        avatar_url: str | None = None
        config_updates: dict[str, Any] = {}
        if not uploads_id:
            channel = await self._channel_metadata(request.max_response_bytes)
            uploads_id = channel["uploads_playlist_id"]
            display_name = channel["display_name"]
            avatar_url = channel["avatar_url"]
            config_updates["uploads_playlist_id"] = uploads_id

        page_size = 15 if request.initial else min(request.max_raw_items, 50)
        page_token: str | None = None
        if request.continuation:
            page_token = await self._validated_next_token(
                uploads_id,
                request.continuation,
                request.max_response_bytes,
            )
        playlist = await self._playlist_page(
            uploads_id,
            page_token,
            page_size,
            request.max_response_bytes,
        )
        raw_rows = playlist["items"]
        video_ids = [row["video_id"] for row in raw_rows]
        videos = await self._video_metadata(video_ids, request.max_response_bytes)
        known_api_ids = checkpoint_ids(api_checkpoint) | {
            str(value) for value in api_checkpoint.get("item_hashes", {})
        }
        normalized = tuple(
            item
            for row in raw_rows
            if (
                item := self._to_content(
                    row,
                    videos.get(row["video_id"]),
                    retain_unavailable=(not request.initial and row["video_id"] in known_api_ids),
                )
            )
            is not None
        )
        anchors = checkpoint_ids(api_checkpoint)
        # If the source was initially baselined through Atom (for example while
        # the daily API soft budget was exhausted), the first recovered Data API
        # scan must reconcile against those same video IDs.  With no anchor it
        # would otherwise treat the channel's entire uploads history as backlog.
        if not anchors:
            anchors = checkpoint_ids(_nested_checkpoint(request.checkpoint, "atom"))
        visible, overlapped = _through_oldest_anchor(normalized, anchors)
        next_token = playlist["next_page_token"]
        completed = request.initial or overlapped or next_token is None
        gap_detected = bool(
            not request.initial and anchors and not overlapped and next_token is None
        )
        api_next = None
        if not completed and next_token:
            api_next = {
                "provider": "youtube_data_api",
                "playlist_id": uploads_id,
                "replay_page_token": page_token,
                "page_size": page_size,
                "boundary_ids": video_ids,
                "next_page_token": next_token,
            }
        new_api_checkpoint = checkpoint_for_items(
            normalized,
            # Playlist IDs remain valid coverage anchors even when videos.list
            # cannot return readable metadata (private/deleted videos).  They
            # establish the initial boundary without admitting an unreadable
            # card to the home feed.
            extra={
                "uploads_playlist_id": uploads_id,
                "head_ids": video_ids[:30],
            },
        )
        checkpoint = dict(request.checkpoint)
        checkpoint["data_api"] = new_api_checkpoint
        return SourceScanPage(
            items=visible,
            provider_mode="youtube_data_api",
            raw_items=len(raw_rows),
            completed=completed,
            next_continuation=api_next,
            checkpoint=checkpoint,
            boundary_ids=tuple(video_ids),
            gap_detected=gap_detected,
            gap_details=(
                {"reason": "uploads_playlist_exhausted_before_checkpoint"} if gap_detected else None
            ),
            source_display_name=display_name,
            source_avatar_url=avatar_url,
            source_config_updates=config_updates,
        )

    async def _atom_page(
        self,
        request: SourceScanRequest,
        *,
        warning_code: str,
        warning_message: str,
    ) -> SourceScanPage:
        atom_checkpoint = _nested_checkpoint(request.checkpoint, "atom")
        atom_continuation = None
        if request.continuation and request.continuation.get("provider") == "youtube_atom":
            atom_continuation = request.continuation.get("value")
        atom_request = replace(
            request,
            checkpoint=atom_checkpoint,
            continuation=atom_continuation if isinstance(atom_continuation, dict) else None,
        )
        page = await RSSSourceAdapter(
            RSSSourceConfig(
                url=self._config.feed_url,
                content_kind=ContentKind.VIDEO,
                profile_url=f"https://www.youtube.com/channel/{self._config.channel_id}",
                provider_mode="youtube_atom",
            ),
            self._client,
        ).scan_page(atom_request)
        checkpoint = dict(request.checkpoint)
        checkpoint["atom"] = page.checkpoint
        continuation = (
            {"provider": "youtube_atom", "value": page.next_continuation}
            if page.next_continuation
            else None
        )
        return replace(
            page,
            authoritative=False,
            checkpoint=checkpoint,
            next_continuation=continuation,
            warning_code=warning_code,
            warning_message=warning_message,
        )

    async def _channel_metadata(self, maximum_bytes: int) -> dict[str, str]:
        payload = await self._api_get(
            "channels",
            {
                "part": "snippet,contentDetails,status",
                "id": self._config.channel_id,
                "maxResults": "1",
            },
            maximum_bytes,
        )
        rows = payload.get("items")
        if not isinstance(rows, list) or not rows:
            raise SourceScanError(
                "youtube_channel_unavailable",
                "YouTube channel is private, deleted, or unavailable.",
                long_lived=True,
            )
        row = rows[0]
        try:
            uploads_id = str(row["contentDetails"]["relatedPlaylists"]["uploads"])
        except (KeyError, TypeError) as exc:
            raise SourceScanError(
                "youtube_channel_invalid", "YouTube channel has no uploads playlist."
            ) from exc
        snippet = row.get("snippet") if isinstance(row.get("snippet"), dict) else {}
        thumbnails = (
            snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        )
        avatar = _best_thumbnail(thumbnails)
        return {
            "uploads_playlist_id": uploads_id,
            "display_name": str(snippet.get("title") or self._config.channel_id),
            "avatar_url": avatar or "",
        }

    async def _playlist_page(
        self,
        playlist_id: str,
        page_token: str | None,
        page_size: int,
        maximum_bytes: int,
    ) -> dict[str, Any]:
        params = {
            "part": "snippet,contentDetails,status",
            "playlistId": playlist_id,
            "maxResults": str(page_size),
        }
        if page_token:
            params["pageToken"] = page_token
        payload = await self._api_get("playlistItems", params, maximum_bytes)
        rows: list[dict[str, Any]] = []
        for raw in payload.get("items", []):
            if not isinstance(raw, dict):
                continue
            content = (
                raw.get("contentDetails") if isinstance(raw.get("contentDetails"), dict) else {}
            )
            snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
            video_id = str(
                content.get("videoId") or snippet.get("resourceId", {}).get("videoId") or ""
            )
            if not video_id:
                continue
            rows.append(
                {
                    "video_id": video_id,
                    "playlist_published_at": snippet.get("publishedAt"),
                    "playlist_title": snippet.get("title"),
                }
            )
        return {
            "items": rows,
            "next_page_token": str(payload.get("nextPageToken"))
            if payload.get("nextPageToken")
            else None,
        }

    async def _validated_next_token(
        self,
        playlist_id: str,
        continuation: dict[str, Any],
        maximum_bytes: int,
    ) -> str:
        if continuation.get("provider") != "youtube_data_api":
            raise SourceScanError("cursor_invalid", "YouTube continuation provider changed.")
        if str(continuation.get("playlist_id")) != playlist_id:
            raise SourceScanError("cursor_invalid", "YouTube uploads playlist changed.")
        expected = {str(value) for value in continuation.get("boundary_ids", []) if value}
        replay_token = continuation.get("replay_page_token")
        try:
            replay_page_size = int(continuation["page_size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceScanError(
                "cursor_invalid", "YouTube continuation page size is invalid."
            ) from exc
        if not 1 <= replay_page_size <= 50:
            raise SourceScanError("cursor_invalid", "YouTube continuation page size is invalid.")
        replay = await self._playlist_page(
            playlist_id,
            str(replay_token) if replay_token else None,
            replay_page_size,
            maximum_bytes,
        )
        actual = {row["video_id"] for row in replay["items"]}
        if not expected or not actual.intersection(expected):
            raise SourceScanError(
                "cursor_invalid", "YouTube playlist shifted before its boundary was verified."
            )
        next_token = replay.get("next_page_token")
        expected_next = continuation.get("next_page_token")
        if not next_token or str(next_token) != str(expected_next):
            raise SourceScanError(
                "cursor_invalid", "YouTube page token no longer matches its boundary."
            )
        return str(next_token)

    async def _video_metadata(
        self, video_ids: list[str], maximum_bytes: int
    ) -> dict[str, dict[str, Any]]:
        if not video_ids:
            return {}
        payload = await self._api_get(
            "videos",
            {
                "part": "snippet,contentDetails,status,liveStreamingDetails",
                "id": ",".join(video_ids[:50]),
                "maxResults": str(min(len(video_ids), 50)),
            },
            maximum_bytes,
        )
        return {
            str(row["id"]): row
            for row in payload.get("items", [])
            if isinstance(row, dict) and row.get("id")
        }

    def _to_content(
        self,
        playlist_row: dict[str, Any],
        video: dict[str, Any] | None,
        *,
        retain_unavailable: bool = False,
    ) -> DiscoveredContent | None:
        # An unavailable item is retained by Candidate processing when it was
        # previously imported. A first import skips it because no readable
        # metadata exists.
        video_id = playlist_row["video_id"]
        if video is None:
            if not retain_unavailable:
                return None
            return DiscoveredContent(
                native_id=video_id,
                kind=ContentKind.VIDEO,
                title=str(playlist_row.get("playlist_title") or "Unavailable video"),
                external_url=f"https://www.youtube.com/watch?v={video_id}",
                published_at=_parse_datetime(playlist_row.get("playlist_published_at")),
                raw_metadata={
                    "availability": "unavailable",
                    "privacy_status": "unavailable",
                },
            )
        snippet = video.get("snippet") if isinstance(video.get("snippet"), dict) else {}
        status = video.get("status") if isinstance(video.get("status"), dict) else {}
        details = (
            video.get("contentDetails") if isinstance(video.get("contentDetails"), dict) else {}
        )
        live = (
            video.get("liveStreamingDetails")
            if isinstance(video.get("liveStreamingDetails"), dict)
            else {}
        )
        title = str(snippet.get("title") or "").strip()
        if not title:
            return None
        published = _parse_datetime(
            snippet.get("publishedAt") or playlist_row.get("playlist_published_at")
        )
        thumbnail = _best_thumbnail(
            snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        )
        media = (
            (DiscoveredMedia(original_url=thumbnail, media_type=MediaType.IMAGE),)
            if thumbnail
            else ()
        )
        return DiscoveredContent(
            native_id=video_id,
            kind=ContentKind.VIDEO,
            title=title,
            external_url=f"https://www.youtube.com/watch?v={video_id}",
            published_at=published,
            author_name=str(snippet.get("channelTitle") or "").strip() or None,
            excerpt_html=str(snippet.get("description") or "") or None,
            raw_metadata={
                "duration": details.get("duration"),
                "privacy_status": status.get("privacyStatus"),
                "upload_status": status.get("uploadStatus"),
                "live_broadcast_content": snippet.get("liveBroadcastContent"),
                "scheduled_start_time": live.get("scheduledStartTime"),
                "actual_start_time": live.get("actualStartTime"),
                "actual_end_time": live.get("actualEndTime"),
            },
            media=media,
        )

    async def _api_get(
        self, resource: str, params: dict[str, str], maximum_bytes: int
    ) -> dict[str, Any]:
        response = await self._client.get(
            f"{_API_ROOT}/{resource}",
            params={**params, "key": self._config.api_key},
            timeout=30.0,
        )
        if response.status_code == 403:
            try:
                reasons = {
                    str(error.get("reason"))
                    for error in response.json().get("error", {}).get("errors", [])
                    if isinstance(error, dict)
                }
            except (ValueError, AttributeError):
                reasons = set()
            if reasons.intersection({"quotaExceeded", "dailyLimitExceeded"}):
                raise SourceScanError(
                    "youtube_quota_exceeded",
                    "YouTube Data API daily quota is exhausted.",
                    long_lived=True,
                )
        raise_for_provider_status(response, "youtube")
        ensure_response_size(response, maximum_bytes)
        payload = response.json()
        if not isinstance(payload, dict):
            raise SourceScanError("youtube_invalid_response", "YouTube returned invalid JSON.")
        return payload


def _nested_checkpoint(checkpoint: dict[str, Any], key: str) -> dict[str, Any]:
    value = checkpoint.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _through_oldest_anchor(
    items: tuple[DiscoveredContent, ...], anchors: set[str]
) -> tuple[tuple[DiscoveredContent, ...], bool]:
    if not anchors:
        return items, False
    matches = [index for index, item in enumerate(items) if item.native_id in anchors]
    return (items[: max(matches) + 1], True) if matches else (items, False)


def _best_thumbnail(thumbnails: dict[str, Any]) -> str | None:
    for key in ("maxres", "standard", "high", "medium", "default"):
        row = thumbnails.get(key)
        if isinstance(row, dict) and isinstance(row.get("url"), str):
            return str(row["url"])
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
