"""Small HTTP safety helpers shared by ingestion adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.ingestion.models import SourceScanError


def ensure_response_size(response: httpx.Response, maximum_bytes: int) -> None:
    declared = response.headers.get("content-length")
    if declared:
        try:
            if int(declared) > maximum_bytes:
                raise SourceScanError(
                    "response_too_large", "Upstream response exceeded size limit."
                )
        except ValueError:
            pass
    if len(response.content) > maximum_bytes:
        raise SourceScanError("response_too_large", "Upstream response exceeded size limit.")


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
        raise SourceScanError(
            code,
            message,
            retry_after_seconds=retry_after,
            long_lived=True,
            evidence={"http_status": response.status_code, "browser_verification": challenged},
        )
    long_lived = response.status_code in {401, 403, 404, 410}
    detail = response.text[:1000].strip() or response.reason_phrase
    raise SourceScanError(
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
