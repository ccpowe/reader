"""Resolve public YouTube channel references to the Atom-feed channel ID."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from app.ingestion.url_safety import UnsafeSourceUrl, safe_get

_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


async def resolve_youtube_channel_id(reference: str, client: httpx.AsyncClient) -> str:
    """Accept a channel ID or a public ``/@handle`` URL and return its ID."""
    value = reference.strip()
    if _CHANNEL_ID_RE.fullmatch(value):
        return value

    if value.startswith("@"):
        value = f"https://www.youtube.com/{value}"
    elif "://" not in value:
        value = f"https://www.youtube.com/@{value}"
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _YOUTUBE_HOSTS:
        raise ValueError("请输入 YouTube 频道 ID（UC…）或 https://www.youtube.com/@频道名。")

    direct_match = re.fullmatch(r"/channel/(UC[A-Za-z0-9_-]{22})/?", parsed.path)
    if direct_match:
        return direct_match.group(1)
    if not re.fullmatch(r"/@[A-Za-z0-9._-]+/?", parsed.path):
        raise ValueError(
            "请输入 YouTube 频道主页 URL，例如 https://www.youtube.com/@aiDotEngineer。"
        )

    try:
        response = await safe_get(
            client,
            value,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ReaderAggregator/0.1)"},
            timeout=30.0,
        )
        response.raise_for_status()
    except (httpx.HTTPError, UnsafeSourceUrl) as exc:
        raise ValueError("无法解析这个 YouTube 频道，请稍后重试。") from exc

    handle = parsed.path.removeprefix("/").rstrip("/").lower()
    match = re.search(
        rf'"browseId":"(UC[A-Za-z0-9_-]{{22}})".{{0,600}}?"canonicalBaseUrl":"/{re.escape(handle)}"',
        response.text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        match = re.search(r'"externalId":"(UC[A-Za-z0-9_-]{22})"', response.text)
    if match is None:
        raise ValueError("无法从该页面识别 YouTube 频道 ID。")
    return match.group(1)
