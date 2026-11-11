"""YouTube channels: the Data API when a key and quota allow, else the public Atom feed."""

from __future__ import annotations

import html
from typing import Any

import httpx
from bs4 import BeautifulSoup

from reader.ingestion.adapters.rss import USER_AGENT, RssAdapter
from reader.ingestion.errors import ScanError, upstream_errors
from reader.ingestion.http import bounded_request, raise_for_provider_status
from reader.ingestion.items import Item, Media, Page, parse_iso_datetime
from reader.ingestion.url_safety import safe_get

API_ROOT = "https://www.googleapis.com/youtube/v3"
# Any API failure (quota, 4xx, 5xx, timeout, bad response) falls back to the public Atom feed.
_API_FAILURE_PREFIXES = ("youtube_quota", "youtube_http_", "youtube_timeout", "youtube_network")
_API_FAILURE_CODES = frozenset({"youtube_invalid_response"})


class YouTubeAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        channel_id: str,
        feed_url: str,
        api_key: str | None,
        uploads_playlist_id: str | None,
        max_bytes: int,
        find_icon: bool = False,
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._feed_url = feed_url
        self._api_key = api_key
        self._uploads = uploads_playlist_id
        self._max_bytes = max_bytes
        self._find_icon = find_icon
        # Set once the scan has to fall back; later pages stay on Atom.
        self.used_fallback = False

    async def fetch_page(self, cursor: str | None, limit: int) -> Page:
        if not self._api_key or self.used_fallback:
            return await self._atom_page(limit)
        try:
            with upstream_errors("youtube"):
                return await self._api_page(cursor, min(limit, 50))
        except ScanError as exc:
            if exc.code.startswith(_API_FAILURE_PREFIXES) or exc.code in _API_FAILURE_CODES:
                return await self._atom_page(limit)
            raise

    async def _atom_page(self, limit: int) -> Page:
        self.used_fallback = True
        page = await RssAdapter(
            self._client,
            self._feed_url,
            max_bytes=self._max_bytes,
            kind="video",
            youtube=True,
            provider="youtube_atom",
        ).fetch_page(None, limit)
        icon = await self._channel_page_icon() if self._find_icon else None
        return Page(items=page.items, source_name=page.source_name, source_icon_url=icon)

    async def _api_page(self, cursor: str | None, limit: int) -> Page:
        name = icon = None
        config: dict[str, Any] = {}
        if not self._uploads:
            channel = await self._channel()
            self._uploads = channel["uploads_playlist_id"]
            name, icon = channel["name"], channel["icon"]
            config["uploads_playlist_id"] = self._uploads
        params = {"part": "contentDetails", "playlistId": self._uploads, "maxResults": str(limit)}
        if cursor:
            params["pageToken"] = cursor
        playlist = await self._get("playlistItems", params)
        video_ids = [
            str(row["contentDetails"]["videoId"])
            for row in playlist.get("items", [])
            if isinstance(row, dict)
            and isinstance(row.get("contentDetails"), dict)
            and row["contentDetails"].get("videoId")
        ]
        videos = await self._videos(video_ids)
        # Private or deleted videos have no metadata and are skipped.
        items = [item for video_id in video_ids if (item := _video_item(videos.get(video_id)))]
        next_token = playlist.get("nextPageToken")
        return Page(
            items=items,
            next_cursor=str(next_token) if next_token else None,
            source_name=name,
            source_icon_url=icon,
            source_config=config,
        )

    async def _channel(self) -> dict[str, str | None]:
        payload = await self._get(
            "channels", {"part": "snippet,contentDetails", "id": self._channel_id}
        )
        rows = payload.get("items")
        if not isinstance(rows, list) or not rows:
            raise ScanError(
                "youtube_channel_unavailable",
                "The YouTube channel is private, deleted or unavailable.",
                long_lived=True,
            )
        row = rows[0]
        try:
            uploads = str(row["contentDetails"]["relatedPlaylists"]["uploads"])
        except (KeyError, TypeError) as exc:
            raise ScanError("youtube_channel_invalid", "The channel has no uploads.") from exc
        snippet = row.get("snippet") if isinstance(row.get("snippet"), dict) else {}
        return {
            "uploads_playlist_id": uploads,
            "name": str(snippet.get("title") or "") or None,
            "icon": _best_thumbnail(snippet.get("thumbnails")),
        }

    async def _videos(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not video_ids:
            return {}
        payload = await self._get(
            "videos", {"part": "snippet,contentDetails,status", "id": ",".join(video_ids[:50])}
        )
        return {
            str(row["id"]): row
            for row in payload.get("items", [])
            if isinstance(row, dict) and row.get("id")
        }

    async def _get(self, resource: str, params: dict[str, str]) -> dict[str, Any]:
        response = await bounded_request(
            self._client,
            "GET",
            f"{API_ROOT}/{resource}",
            max_bytes=self._max_bytes,
            params={**params, "key": self._api_key},
            timeout=30.0,
        )
        if response.status_code == 403 and _quota_exhausted(response):
            raise ScanError(
                "youtube_quota_exceeded", "The YouTube Data API daily quota is used up."
            )
        raise_for_provider_status(response, "youtube")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ScanError("youtube_invalid_response", "YouTube returned invalid JSON.")
        return payload

    async def _channel_page_icon(self) -> str | None:
        try:
            response = await safe_get(
                self._client,
                f"https://www.youtube.com/channel/{self._channel_id}",
                headers={"User-Agent": USER_AGENT},
                timeout=20.0,
                max_response_bytes=self._max_bytes,
            )
        except (httpx.HTTPError, ScanError, ValueError):
            return None
        if response.is_error:
            return None
        tag = BeautifulSoup(response.text, "html.parser").find(
            "meta", attrs={"property": "og:image"}
        )
        value = tag.get("content") if tag is not None else None
        return value.strip() if isinstance(value, str) and value.startswith("https://") else None


def _video_item(video: dict[str, Any] | None) -> Item | None:
    if video is None:
        return None
    snippet = video.get("snippet") if isinstance(video.get("snippet"), dict) else {}
    details = video.get("contentDetails") if isinstance(video.get("contentDetails"), dict) else {}
    title = str(snippet.get("title") or "").strip()
    if not title:
        return None
    video_id = str(video["id"])
    thumbnail = _best_thumbnail(snippet.get("thumbnails"))
    description = str(snippet.get("description") or "").strip()
    return Item(
        native_id=video_id,
        kind="video",
        title=title,
        url=f"https://www.youtube.com/watch?v={video_id}",
        published_at=parse_iso_datetime(snippet.get("publishedAt")),
        author=str(snippet.get("channelTitle") or "").strip() or None,
        summary=_cut(" ".join(description.split())) if description else None,
        # The full description (chapters, links) is the body; it is plain text.
        body_html=html.escape(description).replace("\n", "<br>") if description else None,
        media=(Media(thumbnail),) if thumbnail else (),
        extra={
            "duration": details.get("duration"),
            "live_broadcast_content": snippet.get("liveBroadcastContent"),
        },
    )


def _cut(text: str, limit: int = 1000) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _best_thumbnail(thumbnails: Any) -> str | None:
    if not isinstance(thumbnails, dict):
        return None
    for key in ("maxres", "standard", "high", "medium", "default"):
        row = thumbnails.get(key)
        if isinstance(row, dict) and isinstance(row.get("url"), str):
            return str(row["url"])
    return None


def _quota_exhausted(response: httpx.Response) -> bool:
    try:
        errors = response.json().get("error", {}).get("errors", [])
        reasons = {str(error.get("reason")) for error in errors if isinstance(error, dict)}
    except (ValueError, AttributeError):
        return False
    return bool(reasons & {"quotaExceeded", "dailyLimitExceeded"})
