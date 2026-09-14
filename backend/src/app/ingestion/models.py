"""Persistence-independent contracts shared by all source scanners."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.domain.enums import ContentKind, MediaType


@dataclass(frozen=True)
class DiscoveredMedia:
    original_url: str
    media_type: MediaType = MediaType.IMAGE
    sort_order: int = 0
    mime_type: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "original_url": self.original_url,
            "media_type": self.media_type.value,
            "sort_order": self.sort_order,
            "mime_type": self.mime_type,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DiscoveredMedia:
        return cls(
            original_url=str(payload["original_url"]),
            media_type=MediaType(str(payload.get("media_type") or MediaType.IMAGE)),
            sort_order=int(payload.get("sort_order") or 0),
            mime_type=str(payload["mime_type"]) if payload.get("mime_type") else None,
        )


@dataclass(frozen=True)
class DiscoveredContent:
    """A stable upstream item before it enters the Candidate Queue."""

    native_id: str
    kind: ContentKind
    title: str
    external_url: str
    published_at: datetime | None
    author_name: str | None = None
    excerpt_html: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    media: tuple[DiscoveredMedia, ...] = ()
    source_updated_at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "native_id": self.native_id,
            "kind": self.kind.value,
            "title": self.title,
            "external_url": self.external_url,
            "published_at": _datetime_text(self.published_at),
            "author_name": self.author_name,
            "excerpt_html": self.excerpt_html,
            "raw_metadata": self.raw_metadata,
            "media": [item.to_payload() for item in self.media],
            "source_updated_at": _datetime_text(self.source_updated_at),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DiscoveredContent:
        return cls(
            native_id=str(payload["native_id"]),
            kind=ContentKind(str(payload["kind"])),
            title=str(payload["title"]),
            external_url=str(payload["external_url"]),
            published_at=_parse_datetime(payload.get("published_at")),
            author_name=str(payload["author_name"]) if payload.get("author_name") else None,
            excerpt_html=str(payload["excerpt_html"]) if payload.get("excerpt_html") else None,
            raw_metadata=(
                dict(payload["raw_metadata"])
                if isinstance(payload.get("raw_metadata"), dict)
                else {}
            ),
            media=tuple(
                DiscoveredMedia.from_payload(item)
                for item in payload.get("media", [])
                if isinstance(item, dict)
            ),
            source_updated_at=_parse_datetime(payload.get("source_updated_at")),
        )

    def material_hash(self) -> str:
        """Hash reader-visible fields, excluding volatile rank/engagement counters."""
        stable_metadata = _stable_metadata(self.raw_metadata)
        material = {
            "kind": self.kind.value,
            "title": self.title,
            "external_url": self.external_url,
            "author_name": self.author_name,
            "excerpt_html": self.excerpt_html,
            "source_updated_at": _datetime_text(self.source_updated_at),
            "metadata": stable_metadata,
            "media": [
                {
                    "url": media.original_url,
                    "type": media.media_type.value,
                    "mime_type": media.mime_type,
                    "sort_order": media.sort_order,
                }
                for media in self.media
            ],
        }
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DiscoveredWebLink:
    normalized_url: str
    confidence: float
    accepted: bool
    discovered_from: str
    evidence: dict[str, Any] = field(default_factory=dict)
    original_url: str | None = None
    fetched: bool = False
    canonical_url: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class SourceScanRequest:
    """One upstream page request with an opaque provider continuation."""

    checkpoint: dict[str, Any] = field(default_factory=dict)
    continuation: dict[str, Any] | None = None
    initial: bool = False
    conditional: bool = True
    max_raw_items: int = 120
    max_response_bytes: int = 5 * 1024 * 1024


@dataclass(frozen=True)
class SourceScanPage:
    """One replayable page returned by a scanner adapter."""

    items: tuple[DiscoveredContent, ...]
    provider_mode: str
    raw_items: int
    completed: bool
    authoritative: bool = True
    next_continuation: dict[str, Any] | None = None
    checkpoint: dict[str, Any] = field(default_factory=dict)
    boundary_ids: tuple[str, ...] = ()
    limit_reached: str | None = None
    gap_detected: bool = False
    gap_details: dict[str, Any] | None = None
    warning_code: str | None = None
    warning_message: str | None = None
    source_display_name: str | None = None
    source_avatar_url: str | None = None
    source_config_updates: dict[str, Any] = field(default_factory=dict)
    web_links: tuple[DiscoveredWebLink, ...] = ()
    web_listing_evidence: dict[str, Any] = field(default_factory=dict)


class SourceScanError(RuntimeError):
    """Structured provider failure used by scheduler retry policy."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        long_lived: bool = False,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.long_lived = long_lived
        self.evidence = evidence or {}


ScanLane = Literal["head", "head_backlog", "backlog"]


def checkpoint_for_items(
    items: tuple[DiscoveredContent, ...],
    *,
    validators: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    anchor_limit: int = 30,
) -> dict[str, Any]:
    """Build a bounded rolling-ID checkpoint including baseline material hashes."""
    selected = items[:anchor_limit]
    checkpoint: dict[str, Any] = {
        "head_ids": [item.native_id for item in selected],
        # Keep every item from the bounded provider page as the initial/current
        # baseline.  Only ``head_ids`` are rolling overlap anchors; limiting the
        # hashes to the same 30 rows would let a reordered item from positions
        # 31-120 look new on a later scan.
        "item_hashes": {item.native_id: item.material_hash() for item in items},
    }
    if validators:
        checkpoint["validators"] = validators
    if extra:
        checkpoint.update(extra)
    return checkpoint


def checkpoint_ids(checkpoint: dict[str, Any] | None) -> set[str]:
    raw = (checkpoint or {}).get("head_ids", [])
    return {str(value) for value in raw if value}


def _datetime_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _stable_metadata(value: Any) -> Any:
    volatile = {
        "stats",
        "metrics",
        "rank",
        "score",
        "likes",
        "like_count",
        "favorites",
        "favourites",
        "favorite_count",
        "favourite_count",
        "retweets",
        "retweet_count",
        "reposts",
        "repost_count",
        "quote_count",
        "comments",
        "reply_count",
        "views",
        "view_count",
        "impressions",
        "impression_count",
        "bookmarks",
        "bookmark_count",
        "followers",
        "followers_count",
        "following_count",
    }
    if isinstance(value, dict):
        return {
            str(key): _stable_metadata(item)
            for key, item in value.items()
            if str(key).lower() not in volatile
        }
    if isinstance(value, list):
        return [_stable_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_stable_metadata(item) for item in value]
    return value
