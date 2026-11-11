"""The one exception every adapter raises when a source cannot be read."""

from __future__ import annotations

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
