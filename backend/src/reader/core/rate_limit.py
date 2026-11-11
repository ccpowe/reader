"""In-process token buckets for failed authentication attempts."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass
from time import monotonic


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """`requests` attempts per `window_seconds` per client key."""

    def __init__(self, *, requests: int, window_seconds: float, max_clients: int = 10_000) -> None:
        if requests < 1 or window_seconds <= 0 or max_clients < 1:
            raise ValueError("Rate-limit values must be positive.")
        self._capacity = float(requests)
        self._refill_per_second = requests / window_seconds
        self._max_clients = max_clients
        self._buckets: OrderedDict[Hashable, _Bucket] = OrderedDict()

    def retry_after(self, key: Hashable, *, now: float | None = None) -> float | None:
        """Take one token; return seconds to wait if none is left."""
        current = monotonic() if now is None else now
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self._max_clients:
                self._buckets.popitem(last=False)
            bucket = self._buckets[key] = _Bucket(self._capacity, current)
        else:
            self._buckets.move_to_end(key)
            self._refill(bucket, current)
        if bucket.tokens < 1:
            return max((1 - bucket.tokens) / self._refill_per_second, 0.001)
        bucket.tokens -= 1
        return None

    def refund(self, key: Hashable, *, now: float | None = None) -> None:
        """Give the token back after a successful attempt."""
        bucket = self._buckets.get(key)
        if bucket is not None:
            self._refill(bucket, monotonic() if now is None else now)
            bucket.tokens = min(self._capacity, bucket.tokens + 1)

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = max(now - bucket.updated_at, 0.0)
        bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)
        bucket.updated_at = now
