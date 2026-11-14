"""What an adapter hands the scanner: items from one upstream page.

Adapters only read upstream; they never touch the database. The scanner
decides what is new, writes articles and stops at known items.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

ArticleKind = Literal["article", "post", "video"]

# Engagement counters change on every fetch; they must not make an item look edited.
_VOLATILE_KEYS = frozenset(
    {
        "stats",
        "metrics",
        "rank",
        "score",
        "likes",
        "like_count",
        "retweets",
        "retweet_count",
        "reposts",
        "quote_count",
        "comments",
        "reply_count",
        "views",
        "view_count",
        "bookmarks",
        "bookmark_count",
    }
)


@dataclass(frozen=True)
class Media:
    url: str
    type: Literal["image", "video"] = "image"

    def to_json(self) -> dict[str, str]:
        return {"url": self.url, "type": self.type}


@dataclass(frozen=True)
class Item:
    """One upstream item, already normalized and sanitized."""

    native_id: str
    kind: ArticleKind
    title: str
    url: str
    published_at: datetime | None = None
    author: str | None = None
    summary: str | None = None
    body_html: str | None = None
    media: tuple[Media, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)
    # A pinned post can be old; it never counts as "reached known items".
    pinned: bool = False

    def content_hash(self) -> str:
        """Hash of what a reader sees; unchanged hash means skip the rewrite."""
        material = {
            "kind": self.kind,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at.astimezone(UTC).isoformat()
            if self.published_at
            else None,
            "author": self.author,
            "summary": self.summary,
            "body_html": self.body_html,
            "media": [media.to_json() for media in self.media],
            "extra": _stable(self.extra),
        }
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Page:
    """Items newest first, plus what the source told us about itself."""

    items: list[Item]
    next_cursor: str | None = None
    # False for popularity-ordered lists (Reddit hot): there, a known item says
    # nothing about older ones, so the scanner neither stops at it nor flags gaps.
    chronological: bool = True
    source_name: str | None = None
    source_icon_url: str | None = None
    # Values to merge into sources.config, e.g. a resolved uploads playlist.
    source_config: dict[str, Any] = field(default_factory=dict)


class Adapter(Protocol):
    async def fetch_page(self, cursor: str | None, limit: int) -> Page:
        """One upstream page of at most `limit` items, starting at `cursor`."""
        ...


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable(item)
            for key, item in value.items()
            if str(key).lower() not in _VOLATILE_KEYS
        }
    if isinstance(value, list | tuple):
        return [_stable(item) for item in value]
    return value


def bounded_native_id(identity: str) -> str:
    """Native IDs fit in 1024 characters; longer ones are hashed."""
    if len(identity) <= 1024:
        return identity
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
