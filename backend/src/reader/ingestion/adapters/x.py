"""X accounts through the private Scweet collector service."""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from reader.ingestion.errors import ScanError
from reader.ingestion.http import ensure_response_size
from reader.ingestion.items import Item, Media, Page
from reader.ingestion.x_relations import normalize_x_relations

_LONG_LIVED = frozenset({"auth_failed", "manifest_failed", "protected_account"})


class XAdapter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        handle: str,
        service_url: str,
        service_token: str,
        max_bytes: int,
        find_icon: bool = False,
    ) -> None:
        self._client = client
        self._handle = handle
        self._base = service_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {service_token}"}
        self._max_bytes = max_bytes
        self._find_icon = find_icon

    async def fetch_page(self, cursor: str | None, limit: int) -> Page:
        body: dict[str, Any] = {"usernames": [self._handle], "limit": min(limit, 50)}
        if cursor:
            # Scweet replays the previous page boundary before continuing.
            try:
                saved = json.loads(cursor)
                body["cursor"] = saved["cursor"]
                body["boundary_ids"] = list(saved["boundary_ids"])
            except (ValueError, KeyError, TypeError) as exc:
                raise ScanError("cursor_invalid", "The X page cursor is invalid.") from exc
        response = await self._client.post(
            f"{self._base}/v1/profiles/tweets", headers=self._headers, json=body, timeout=180.0
        )
        if response.is_error:
            raise _service_error(response)
        ensure_response_size(response, self._max_bytes)
        payload = response.json()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            raise ScanError("scweet_invalid_response", "Scweet returned an unexpected response.")
        rows = [raw for raw in raw_items if isinstance(raw, dict)]
        items = [item for index, raw in enumerate(rows) if (item := self._to_item(raw, index))]
        next_cursor = None
        if payload.get("continuation") and not payload.get("completed"):
            next_cursor = json.dumps(
                {
                    "cursor": payload["continuation"],
                    "boundary_ids": list(
                        payload.get("boundary_ids") or [item.native_id for item in items]
                    ),
                }
            )
        icon = None
        if self._find_icon:
            icon = next((url for raw in rows if (url := _profile_image(raw))), None)
            if icon is None and cursor is None:
                icon = await self._lookup_icon()
        return Page(items=items, next_cursor=next_cursor, source_icon_url=icon)

    def _to_item(self, raw: dict[str, Any], index: int) -> Item | None:
        native_id = str(raw.get("tweet_id") or raw.get("id") or "").strip()
        url = str(raw.get("tweet_url") or raw.get("url") or "").strip()
        text = str(raw.get("text") or raw.get("full_text") or "").strip()
        if not native_id or not url or not text:
            return None
        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        handle = str(user.get("screen_name") or raw.get("username") or self._handle).strip()
        relations = normalize_x_relations(raw)
        return Item(
            native_id=native_id,
            kind="post",
            title=" ".join(text.split())[:180],
            url=url,
            published_at=_parse_datetime(raw.get("timestamp") or raw.get("date")),
            author=f"@{handle.removeprefix('@')}",
            summary=text,
            body_html=html.escape(text).replace("\n", "<br>"),
            media=_media(raw.get("media")),
            extra={"x": relations},
            # Scweet does not always flag the pinned post; treat an unflagged first
            # row as possibly pinned so an old pin never ends a scan early.
            pinned=bool(raw.get("is_pinned")) or (index == 0 and "is_pinned" not in raw),
        )

    async def _lookup_icon(self) -> str | None:
        try:
            response = await self._client.post(
                f"{self._base}/v1/profiles/info",
                headers=self._headers,
                json={"usernames": [self._handle]},
                timeout=60.0,
            )
            rows = response.json().get("items") if not response.is_error else None
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        if not isinstance(rows, list):
            return None
        return next(
            (
                url
                for row in rows
                if isinstance(row, dict) and (url := _https(row.get("profile_image_url")))
            ),
            None,
        )


def _service_error(response: httpx.Response) -> ScanError:
    code, detail = "scweet_http_error", response.text[:500]
    try:
        error = response.json().get("detail")
        if isinstance(error, dict):
            code = str(error.get("code") or code)
            detail = str(error.get("message") or detail)
        elif error:
            detail = str(error)
    except (ValueError, AttributeError):
        pass
    return ScanError(
        code,
        f"Scweet request failed ({response.status_code}): {detail}",
        retry_after_seconds=900 if code == "rate_limited" else None,
        long_lived=code in _LONG_LIVED,
    )


def _media(raw: Any) -> tuple[Media, ...]:
    values: list[tuple[Any, str]] = []
    if isinstance(raw, dict):
        values += [(url, "image") for url in raw.get("image_links", [])]
        values += [(url, "video") for url in raw.get("video_links", [])]
    elif isinstance(raw, list):
        values += [
            (row.get("url"), "video" if row.get("type") == "video" else "image")
            for row in raw
            if isinstance(row, dict)
        ]
    return tuple(Media(url, kind) for value, kind in values if (url := _https(value)))  # type: ignore[arg-type]


def _profile_image(raw: dict[str, Any]) -> str | None:
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    return _https(user.get("profile_image_url"))


def _https(value: Any) -> str | None:
    if isinstance(value, str) and value.strip().startswith(("https://", "http://")):
        return value.strip()
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, OverflowError):
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
