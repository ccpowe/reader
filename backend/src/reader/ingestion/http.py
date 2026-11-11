"""Small HTTP safety helpers shared by ingestion adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from reader.ingestion.errors import ScanError


def ensure_response_size(response: httpx.Response, maximum_bytes: int) -> None:
    declared = response.headers.get("content-length")
    if declared:
        try:
            if int(declared) > maximum_bytes:
                raise ScanError("response_too_large", "Upstream response exceeded size limit.")
        except ValueError:
            pass
    if len(response.content) > maximum_bytes:
        raise ScanError("response_too_large", "Upstream response exceeded size limit.")


def provider_http_error_code(response: httpx.Response, provider: str) -> str:
    """Identify a challenge from upstream evidence, never from the status alone."""
    if response.headers.get("cf-mitigated", "").strip().lower() == "challenge":
        return f"{provider}_challenge_required"
    return f"{provider}_http_{response.status_code}"


def raise_for_provider_status(response: httpx.Response, provider: str) -> None:
    code = provider_http_error_code(response, provider)
    challenged = code == f"{provider}_challenge_required"
    if not response.is_error and not challenged:
        return
    retry_after = _retry_after_seconds(response.headers.get("retry-after"))
    if challenged or response.status_code == 403:
        # Challenge HTML can contain transient verification tokens. Retain only
        # the status/category needed for source health and operational recovery.
        message = (
            "The website requires browser verification before the server can fetch it."
            if challenged
            else "The website refused the server's request (HTTP 403)."
        )
        raise ScanError(
            code,
            message,
            retry_after_seconds=retry_after,
            long_lived=True,
            evidence={"http_status": response.status_code, "browser_verification": challenged},
        )
    long_lived = response.status_code in {401, 403, 404, 410}
    detail = response.text[:1000].strip() or response.reason_phrase
    raise ScanError(
        code,
        f"{provider} request failed ({response.status_code}): {detail}",
        retry_after_seconds=retry_after,
        long_lived=long_lived,
    )


def _retry_after_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(int(value), 0)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        retry_at = (
            retry_at.replace(tzinfo=UTC) if retry_at.tzinfo is None else retry_at.astimezone(UTC)
        )
        return max(int((retry_at - datetime.now(UTC)).total_seconds()), 0)


async def bounded_request(
    client: httpx.AsyncClient, method: str, url: str, *, max_bytes: int, **kwargs: Any
) -> httpx.Response:
    """Send a request and read at most `max_bytes` of body, stopping as soon as it is exceeded.

    For fixed provider endpoints (APIs, the internal Scweet service). User-supplied
    URLs go through `url_safety.safe_get`, which also validates the destination.
    """
    from reader.ingestion.url_safety import read_bounded_body

    headers = dict(kwargs.pop("headers", None) or {})
    # A compressed body could expand past the limit before we could check it.
    headers.setdefault("Accept-Encoding", "identity")
    async with client.stream(method, url, headers=headers, **kwargs) as response:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ScanError("response_too_large", "Upstream response exceeded size limit.")
        content = await read_bounded_body(response, max_bytes)
        clean = response.headers.copy()
        for header in ("content-encoding", "content-length", "transfer-encoding"):
            clean.pop(header, None)
        return httpx.Response(
            response.status_code, headers=clean, content=content, request=response.request
        )
