"""Stable, local-process pages over authenticated Reader ranking previews."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date as calendar_date
from datetime import datetime
from time import monotonic
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.ingestion.source_identity import canonical_reddit_identity

_PAGE_SIZE = 10
_ITEM_FIELDS = (
    "rank",
    "title",
    "url",
    "source_label",
    "description",
    "author",
    "author_handle",
    "score",
    "comments",
    "language",
    "stars",
    "forks",
    "stars_this_period",
    "image_urls",
    "body_text",
    "published_at",
    "retweet_count",
    "bookmark_count",
    "video_url",
    "video_thumbnail_url",
)
_PRODUCT_HUNT_ITEM_FIELDS = ("product_id", "website_url")
_PRODUCT_HUNT_METADATA_FIELDS = ("date", "timezone", "order", "truncated")
_PRODUCT_HUNT_TIMEZONE = ZoneInfo("America/Los_Angeles")


class ReaderClient(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any: ...


class RankingCursorExpired(ValueError):
    """A valid cursor whose in-process frozen snapshot has expired or been evicted."""


@dataclass(frozen=True)
class _Snapshot:
    board: tuple[str, ...]
    kind: str
    title: str
    subtitle: str
    fetched_at: str
    metadata: dict[str, Any]
    items: tuple[dict[str, Any], ...]
    expires_at: float
    size: int


class RankingPages:
    """One user's bounded, short-lived ranking snapshots in a local stdio process."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 900,
        max_snapshots: int = 16,
        max_cached_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if ttl_seconds <= 0 or max_snapshots < 1 or max_cached_bytes < 1:
            raise ValueError("Ranking cache limits must be positive.")
        self._ttl = ttl_seconds
        self._max_snapshots = max_snapshots
        self._max_cached_bytes = max_cached_bytes
        self._bytes = 0
        self._snapshots: OrderedDict[str, _Snapshot] = OrderedDict()
        self._secret = secrets.token_bytes(32)

    async def list_ranking_snapshot(
        self,
        client: ReaderClient,
        kind: str,
        cursor: str | None = None,
        date: str | None = None,
        period: str | None = None,
    ) -> dict[str, Any]:
        """Freeze up to 100 originals and serve ten at a time without refreshing.

        A cursor never shifts to a newer board. Invalid cursors raise ValueError;
        expired or evicted snapshots raise RankingCursorExpired.
        """
        if kind not in {"hacker_news", "github", "techtwitter", "product_hunt"}:
            raise ValueError(
                "Ranking kind must be hacker_news, github, techtwitter, or product_hunt."
            )
        if kind != "product_hunt" and date is not None:
            raise ValueError("date is only supported for product_hunt rankings.")
        if kind != "github" and period is not None:
            raise ValueError("period is only supported for github rankings.")
        if kind == "github":
            if period is not None and (
                not isinstance(period, str) or period not in {"daily", "weekly", "monthly"}
            ):
                raise ValueError("GitHub period must be daily, weekly, or monthly.")
            return await self._page(client, (kind, period or "weekly"), cursor)
        if kind != "product_hunt":
            return await self._page(client, (kind,), cursor)
        today = datetime.now(_PRODUCT_HUNT_TIMEZONE).date()
        if date is not None:
            if not isinstance(date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
                raise ValueError("date must be YYYY-MM-DD.")
            try:
                selected = calendar_date.fromisoformat(date)
            except ValueError:
                raise ValueError("date must be YYYY-MM-DD.") from None
            if selected > today:
                raise ValueError(
                    "Product Hunt date cannot be in the future in America/Los_Angeles."
                )
        elif cursor is not None:
            snapshot_id, _ = self._decode(cursor)
            existing = self._snapshots.get(snapshot_id)
            date = (
                existing.board[1]
                if existing is not None and existing.kind == kind
                else today.isoformat()
            )
        else:
            date = today.isoformat()
        return await self._page(client, (kind, date), cursor)

    async def list_reddit_ranking_snapshot(
        self,
        client: ReaderClient,
        subreddit: str,
        sort: str = "hot",
        time_filter: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Read a subscribed board; authorize later pages without replacing their data.

        ``top`` defaults to ``week``. Explicit time filters on hot/rising are
        rejected. The follow-up authorization GET uses Reader's normal ranking
        cache behavior and may surface its API errors.
        """
        if not isinstance(subreddit, str) or len(subreddit) > 64:
            raise ValueError("Invalid subreddit name.")
        name = canonical_reddit_identity(subreddit).canonical_key.removeprefix("reddit:")
        if sort not in {"hot", "rising", "top"}:
            raise ValueError("Reddit sort must be hot, rising, or top.")
        if sort == "top":
            if time_filter is None:
                time_filter = "week"
            if time_filter not in {"day", "week", "month", "year"}:
                raise ValueError("Reddit top time_filter must be day, week, month, or year.")
        elif time_filter is not None:
            raise ValueError("time_filter is only supported for Reddit top rankings.")
        return await self._page(client, ("reddit", name, sort, time_filter or "-"), cursor)

    async def _page(
        self, client: ReaderClient, board: tuple[str, ...], cursor: str | None
    ) -> dict[str, Any]:
        self._prune()
        params: dict[str, Any] = {"limit": 100 if cursor is None else 1}
        if board[0] == "reddit":
            params.update(subreddit=board[1], sort=board[2])
            if board[2] == "top":
                params["time_filter"] = board[3]
        elif board[0] == "product_hunt":
            params["date"] = board[1]
        elif board[0] == "github":
            params["period"] = board[1]
        path = f"/v1/rankings/{board[0]}"
        if cursor is None:
            payload = await client.request("GET", path, params=params)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("Invalid ranking response.")
            if any(
                not isinstance(item, dict) or not {"rank", "title", "url"} <= item.keys()
                for item in payload["items"]
            ):
                raise ValueError("Invalid ranking response.")
            fields = _ITEM_FIELDS + (
                _PRODUCT_HUNT_ITEM_FIELDS if board[0] == "product_hunt" else ()
            )
            items = tuple(
                {
                    field: deepcopy(item[field])
                    for field in fields
                    if field in item
                    and (
                        item[field] is not None
                        or (board[0] == "product_hunt" and field in _PRODUCT_HUNT_ITEM_FIELDS)
                    )
                }
                | ({"discussion_url": item["url"]} if board[0] == "reddit" else {})
                for item in payload["items"]
            )
            if len(items) > 100:
                raise ValueError("Ranking response exceeds the requested limit.")
            metadata: dict[str, Any] = {}
            if board[0] == "product_hunt":
                if payload.get("date") != board[1] or any(
                    field not in payload for field in _PRODUCT_HUNT_METADATA_FIELDS
                ):
                    raise ValueError("Invalid Product Hunt ranking response date or metadata.")
                metadata = {
                    field: deepcopy(payload[field]) for field in _PRODUCT_HUNT_METADATA_FIELDS
                }
            elif board[0] == "github" and "period" in payload:
                if payload["period"] != board[1]:
                    raise ValueError("Invalid GitHub ranking response period.")
                metadata["period"] = deepcopy(payload["period"])
            size = len(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode())
            if metadata:
                size += len(
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode()
                )
            if size > self._max_cached_bytes:
                raise ValueError("Ranking snapshot exceeds the local cache size limit.")
            snapshot = _Snapshot(
                board=board,
                kind=board[0],
                title=payload["title"],
                subtitle=payload["subtitle"],
                fetched_at=payload["fetched_at"],
                metadata=metadata,
                items=items,
                expires_at=monotonic() + self._ttl,
                size=size,
            )
            snapshot_id = secrets.token_urlsafe(24)
            self._store(snapshot_id, snapshot)
            offset = 0
        else:
            snapshot_id, offset = self._decode(cursor)
            snapshot = self._snapshots.get(snapshot_id)
            if snapshot is None:
                raise RankingCursorExpired("Ranking cursor expired; start a new snapshot.")
            if snapshot.board != board or offset >= len(snapshot.items):
                raise ValueError("Invalid ranking cursor for this board.")
            if board[0] == "reddit":
                # The ranking endpoint enforces the user's active subscription. Do not
                # use its current snapshot for the frozen page, even if it has changed.
                await client.request("GET", path, params=params)
            else:
                await client.request("GET", "/v1/auth/user")
            if monotonic() >= snapshot.expires_at or snapshot_id not in self._snapshots:
                raise RankingCursorExpired("Ranking cursor expired; start a new snapshot.")
        end = min(offset + _PAGE_SIZE, len(snapshot.items))
        return {
            "kind": snapshot.kind,
            "title": snapshot.title,
            "subtitle": snapshot.subtitle,
            "snapshot_id": snapshot_id,
            "fetched_at": snapshot.fetched_at,
            "items": list(deepcopy(snapshot.items[offset:end])),
            "next_cursor": self._encode(snapshot_id, end) if end < len(snapshot.items) else None,
            **deepcopy(snapshot.metadata),
        }

    def _store(self, snapshot_id: str, snapshot: _Snapshot) -> None:
        while self._snapshots and (
            len(self._snapshots) >= self._max_snapshots
            or self._bytes + snapshot.size > self._max_cached_bytes
        ):
            _, oldest = self._snapshots.popitem(last=False)
            self._bytes -= oldest.size
        self._snapshots[snapshot_id] = snapshot
        self._bytes += snapshot.size

    def _prune(self) -> None:
        now = monotonic()
        for snapshot_id, snapshot in list(self._snapshots.items()):
            if now >= snapshot.expires_at:
                del self._snapshots[snapshot_id]
                self._bytes -= snapshot.size

    def _encode(self, snapshot_id: str, offset: int) -> str:
        raw = f"{snapshot_id}:{offset}".encode("ascii")
        data = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        signature = hmac.new(self._secret, data.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{data}.{signature}"

    def _decode(self, cursor: str) -> tuple[str, int]:
        if not isinstance(cursor, str) or len(cursor) > 256:
            raise ValueError("Invalid ranking cursor.")
        try:
            data, signature = cursor.split(".")
            expected = hmac.new(self._secret, data.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            snapshot_id, raw_offset = (
                base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("ascii").split(":")
            )
            offset = int(raw_offset)
            if offset < _PAGE_SIZE or offset % _PAGE_SIZE or not snapshot_id:
                raise ValueError
            return snapshot_id, offset
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid ranking cursor.") from exc
