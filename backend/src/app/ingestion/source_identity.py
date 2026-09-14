"""Canonical identities for public, shareable information sources."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain.enums import SourceKind
from app.ingestion.url_safety import validate_http_url


@dataclass(frozen=True)
class SourceIdentity:
    """Stable source identity used to deduplicate public source subscriptions."""

    kind: SourceKind
    canonical_url: str
    canonical_key: str


def canonical_rss_identity(url: str) -> SourceIdentity:
    """Normalise an RSS URL without changing meaningful query parameters.

    The source key deliberately retains the query string: some legitimate feeds
    use it to select a public language or category. Credential-bearing URLs are
    rejected separately by `validate_http_url`.
    """
    validate_http_url(url)
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port
    authority = host
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        authority = f"{host}:{port}"
    path = parsed.path or "/"
    canonical_url = urlunsplit((scheme, authority, path, parsed.query, ""))
    return SourceIdentity(
        kind=SourceKind.RSS,
        canonical_url=canonical_url,
        canonical_key=f"rss:{canonical_url}",
    )


def canonical_web_identity(url: str) -> SourceIdentity:
    """Normalise a public blog index URL for shared web subscriptions."""
    validate_http_url(url)
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port
    authority = host
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        authority = f"{host}:{port}"
    path = parsed.path or "/"
    canonical_url = urlunsplit((scheme, authority, path, parsed.query, ""))
    return SourceIdentity(
        kind=SourceKind.WEB,
        canonical_url=canonical_url,
        canonical_key=f"web:{canonical_url}",
    )


_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")
MAX_RAW_SUBREDDIT_LENGTH = 64
_X_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_YOUTUBE_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def canonical_reddit_identity(subreddit: str) -> SourceIdentity:
    """Build one stable source identity per subreddit, independent of ranking view."""
    name = subreddit.strip().removeprefix("r/").strip()
    if not _SUBREDDIT_RE.fullmatch(name):
        raise ValueError("Invalid subreddit name.")
    return SourceIdentity(
        kind=SourceKind.REDDIT,
        canonical_url=f"https://www.reddit.com/r/{name}/",
        canonical_key=f"reddit:{name.lower()}",
    )


def canonical_youtube_identity(channel_id: str) -> SourceIdentity:
    """Build an identity for YouTube's official public channel Atom Feed."""
    normalized = channel_id.strip()
    if not _YOUTUBE_CHANNEL_ID_RE.fullmatch(normalized):
        raise ValueError("A YouTube channel ID beginning with UC is required.")
    return SourceIdentity(
        kind=SourceKind.YOUTUBE,
        canonical_url=f"https://www.youtube.com/feeds/videos.xml?channel_id={normalized}",
        canonical_key=f"youtube:{normalized}",
    )


def canonical_x_identity(handle: str) -> SourceIdentity:
    """Build an identity for an explicitly selected public X account."""
    normalized = handle.strip().removeprefix("@").strip()
    if not _X_HANDLE_RE.fullmatch(normalized):
        raise ValueError("Invalid X handle.")
    return SourceIdentity(
        kind=SourceKind.X,
        canonical_url=f"https://x.com/{normalized}",
        canonical_key=f"x:{normalized.lower()}",
    )


_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def normalize_web_article_url(url: str, *, keep_query: bool = False) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port
    authority = f"[{host}]" if ":" in host else host
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        authority = f"{authority}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = ""
    if keep_query:
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key.lower() not in _TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
            ]
        )
    return urlunsplit((scheme, authority, path, query, ""))


def web_article_native_id(url: str) -> str:
    return hashlib.sha256(
        normalize_web_article_url(url, keep_query=True).encode("utf-8")
    ).hexdigest()
