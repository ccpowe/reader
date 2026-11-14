"""The one exception every adapter raises when a source cannot be read."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class ScanError(RuntimeError):
    """A structured upstream failure; the scanner records `code` on the source.

    `long_lived` failures (gone, private, forbidden) are retried after a day;
    `retry_after_seconds` overrides the normal backoff.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        long_lived: bool = False,
        evidence: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        self.long_lived = long_lived
        self.evidence = evidence or {}
        self.retryable = retryable


@contextmanager
def upstream_errors(provider: str) -> Iterator[None]:
    """Turn failures talking to an upstream into ScanError, the adapters' only exception.

    Cancellation and programming errors still propagate unchanged.
    """
    import httpx

    from reader.ingestion.url_safety import UnresolvableHost, UnsafeSourceUrl

    try:
        yield
    except ScanError:
        raise
    except UnresolvableHost as exc:
        raise ScanError(f"{provider}_network_error", str(exc)) from exc
    except UnsafeSourceUrl as exc:
        raise ScanError("unsafe_url", str(exc), long_lived=True) from exc
    except httpx.TimeoutException as exc:
        raise ScanError(f"{provider}_timeout", f"{provider} did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise ScanError(
            f"{provider}_network_error", f"Could not reach {provider}: {type(exc).__name__}."
        ) from exc
    except ValueError as exc:
        # Undecodable JSON or text (JSONDecodeError and UnicodeDecodeError are ValueErrors).
        raise ScanError(
            f"{provider}_invalid_response", f"{provider} returned an unexpected response."
        ) from exc
