"""Source icons, relayed through the server so the app never contacts their hosts.

Icons are small public images; a short in-process cache avoids refetching them
for every list render.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from uuid import UUID

import httpx

from reader.core.errors import AppError
from reader.ingestion.errors import ScanError
from reader.ingestion.url_safety import PublicAsyncClient, UnsafeSourceUrl, safe_get

ICON_MAX_BYTES = 2 * 1024 * 1024
_CACHE_SECONDS = 24 * 60 * 60
_CACHE_ENTRIES = 256
_IMAGE_TYPES = frozenset(
    {
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/vnd.microsoft.icon",
        "image/webp",
        "image/x-icon",
    }
)


@dataclass(frozen=True)
class Icon:
    content: bytes
    content_type: str


@dataclass(frozen=True)
class _Cached:
    url: str
    icon: Icon
    expires_at: float


_cache: OrderedDict[UUID, _Cached] = OrderedDict()


def _unavailable() -> AppError:
    return AppError(502, "icon_unavailable", "The source icon could not be loaded.")


async def fetch_icon(source_id: UUID, url: str) -> Icon:
    cached = _cache.get(source_id)
    if cached is not None and cached.url == url and cached.expires_at > monotonic():
        _cache.move_to_end(source_id)
        return cached.icon
    try:
        async with PublicAsyncClient(timeout=15.0) as client:
            response = await safe_get(client, url, timeout=15.0, max_response_bytes=ICON_MAX_BYTES)
    except (httpx.HTTPError, UnsafeSourceUrl, ScanError) as exc:
        raise _unavailable() from exc
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if response.status_code != 200 or content_type not in _IMAGE_TYPES or not response.content:
        raise _unavailable()
    icon = Icon(response.content, content_type)
    _cache[source_id] = _Cached(url, icon, monotonic() + _CACHE_SECONDS)
    _cache.move_to_end(source_id)
    while len(_cache) > _CACHE_ENTRIES:
        _cache.popitem(last=False)
    return icon
