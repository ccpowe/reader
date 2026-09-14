"""Cross-process PostgreSQL budgets for authenticated translation traffic."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.settings import Settings, get_settings
from app.storage.models import TranslationQuotaBucket, TranslationQuotaOverride

QUOTA_WINDOW_SECONDS = 60
TRANSLATION_QUOTA_REQUESTS_EXCEEDED = "translation_quota_requests_exceeded"
TRANSLATION_QUOTA_USER_CHARS_EXCEEDED = "translation_quota_user_miss_chars_exceeded"
TRANSLATION_QUOTA_GLOBAL_CHARS_EXCEEDED = "translation_quota_global_miss_chars_exceeded"
TRANSLATION_DISABLED_FOR_USER = "translation_disabled_for_user"
TRANSLATION_QUOTA_STORAGE_UNAVAILABLE = "translation_quota_storage_unavailable"


class TranslationQuotaError(RuntimeError):
    """A stable, safe error suitable for an API response."""

    def __init__(self, code: str, retry_after_seconds: int = 1) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class TranslationDisabledForUser(TranslationQuotaError):
    def __init__(self) -> None:
        super().__init__(TRANSLATION_DISABLED_FOR_USER, retry_after_seconds=60)


class TranslationQuotaExceeded(TranslationQuotaError):
    pass


class TranslationQuotaStorageUnavailable(RuntimeError):
    """The quota tables/transaction could not be reached safely."""

    code = TRANSLATION_QUOTA_STORAGE_UNAVAILABLE


# Short names keep integration call sites readable while the long names make
# API error provenance unambiguous.
QuotaExceeded = TranslationQuotaExceeded
QuotaStorageUnavailable = TranslationQuotaStorageUnavailable


@dataclass(frozen=True, slots=True)
class EffectiveTranslationQuota:
    requests: int
    actual_miss_chars: int
    is_disabled: bool


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    """Counters reserved by one transaction."""

    requests: int
    actual_miss_chars: int
    provider_miss_chars: int


class TranslationQuotaStore:
    """Reserve request and provider-miss budgets with PostgreSQL row locks.

    Every method opens its own short transaction. It never holds a database
    connection while a provider is called, and the user/global rows are
    locked in deterministic order so concurrent mixed reservations cannot
    partially consume either budget.
    """

    def __init__(self, session_factory, settings: Settings | None = None) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def check_allowed(self, user_id: UUID) -> None:
        """Check each subscriber without consuming request or character budgets."""
        await self.reserve(user_id)

    async def reserve_request(self, user_id: UUID) -> QuotaReservation:
        """Charge one legal authenticated request to the user's request cap."""
        return await self.reserve(user_id, requests=1)

    async def reserve_miss(
        self,
        user_id: UUID,
        *,
        actual_miss_chars: int,
        provider_miss_chars: int | None = None,
    ) -> QuotaReservation:
        """Atomically reserve a text+context cache miss for user and provider."""
        return await self.reserve(
            user_id,
            actual_miss_chars=actual_miss_chars,
            provider_miss_chars=(
                actual_miss_chars if provider_miss_chars is None else provider_miss_chars
            ),
        )

    async def reserve(
        self,
        user_id: UUID,
        *,
        requests: int = 0,
        actual_miss_chars: int = 0,
        provider_miss_chars: int = 0,
    ) -> QuotaReservation:
        """Reserve all requested dimensions or commit none of them.

        ``actual_miss_chars`` and ``provider_miss_chars`` are deliberately
        supplied by the caller after cache projection. A pure cache hit passes
        zero and therefore does not consume character budgets, even when the
        provider's character cap is exhausted.
        """
        if not isinstance(user_id, UUID):
            raise ValueError("user_id must be a UUID")
        if requests < 0 or actual_miss_chars < 0 or provider_miss_chars < 0:
            raise ValueError("quota reservation costs cannot be negative")
        if provider_miss_chars > 0 and actual_miss_chars == 0:
            raise ValueError("provider miss characters require an actual miss")

        now = datetime.now(UTC)
        window_start = _fixed_window_start(now)
        try:
            async with self.session_factory() as session:
                override = await session.scalar(
                    select(TranslationQuotaOverride)
                    .where(TranslationQuotaOverride.user_id == user_id)
                    .with_for_update()
                )
                limits = _effective_limits(self.settings or get_settings(), override)
                if limits.is_disabled:
                    await session.rollback()
                    raise TranslationDisabledForUser()

                # No character charge is a true cache hit. This still reads the
                # override, so a newly disabled user is rejected immediately.
                if requests == 0 and actual_miss_chars == 0 and provider_miss_chars == 0:
                    await session.commit()
                    return QuotaReservation(0, 0, 0)

                user_bucket = await _lock_bucket(
                    session,
                    bucket_key=_user_bucket_key(user_id),
                    user_id=user_id,
                    window_start=window_start,
                )
                global_bucket = None
                if provider_miss_chars:
                    global_bucket = await _lock_bucket(
                        session,
                        bucket_key="global",
                        user_id=None,
                        window_start=window_start,
                    )

                # Locks may have waited across a minute boundary. Choose one
                # monotonic window only after all participating rows are locked.
                now = datetime.now(UTC)
                window_start = max(
                    _fixed_window_start(now),
                    user_bucket.window_started_at,
                    global_bucket.window_started_at
                    if global_bucket is not None
                    else user_bucket.window_started_at,
                )
                _advance_bucket(user_bucket, window_start)
                if global_bucket is not None:
                    _advance_bucket(global_bucket, window_start)
                retry_after = _retry_after_seconds(window_start, now)
                if requests and user_bucket.requests_used + requests > limits.requests:
                    await session.rollback()
                    raise TranslationQuotaExceeded(
                        TRANSLATION_QUOTA_REQUESTS_EXCEEDED,
                        retry_after,
                    )
                if (
                    actual_miss_chars
                    and user_bucket.actual_miss_chars_used + actual_miss_chars
                    > limits.actual_miss_chars
                ):
                    await session.rollback()
                    raise TranslationQuotaExceeded(
                        TRANSLATION_QUOTA_USER_CHARS_EXCEEDED,
                        retry_after,
                    )
                global_limit = (
                    self.settings or get_settings()
                ).translation_quota_global_miss_chars_per_minute
                if (
                    global_bucket is not None
                    and global_bucket.actual_miss_chars_used + provider_miss_chars > global_limit
                ):
                    await session.rollback()
                    raise TranslationQuotaExceeded(
                        TRANSLATION_QUOTA_GLOBAL_CHARS_EXCEEDED,
                        retry_after,
                    )

                user_bucket.requests_used += requests
                user_bucket.actual_miss_chars_used += actual_miss_chars
                if global_bucket is not None:
                    global_bucket.actual_miss_chars_used += provider_miss_chars
                await session.commit()
                return QuotaReservation(requests, actual_miss_chars, provider_miss_chars)
        except TranslationQuotaError:
            raise
        except Exception as exc:
            raise TranslationQuotaStorageUnavailable() from exc

    async def limits_for_user(self, user_id: UUID) -> EffectiveTranslationQuota:
        """Read effective limits for operational diagnostics without counters."""
        try:
            async with self.session_factory() as session:
                override = await session.scalar(
                    select(TranslationQuotaOverride).where(
                        TranslationQuotaOverride.user_id == user_id
                    )
                )
                await session.commit()
                return _effective_limits(self.settings or get_settings(), override)
        except Exception as exc:
            raise TranslationQuotaStorageUnavailable() from exc


async def _lock_bucket(session, *, bucket_key: str, user_id: UUID | None, window_start: datetime):
    await session.execute(
        pg_insert(TranslationQuotaBucket)
        .values(
            bucket_key=bucket_key,
            user_id=user_id,
            window_started_at=window_start,
            requests_used=0,
            actual_miss_chars_used=0,
            updated_at=window_start,
        )
        .on_conflict_do_nothing(index_elements=[TranslationQuotaBucket.bucket_key])
    )
    bucket = await session.scalar(
        select(TranslationQuotaBucket)
        .where(TranslationQuotaBucket.bucket_key == bucket_key)
        .with_for_update()
    )
    if bucket is None:
        raise RuntimeError("translation quota bucket disappeared")
    return bucket


def _advance_bucket(bucket, window_start: datetime) -> None:
    if bucket.window_started_at < window_start:
        bucket.window_started_at = window_start
        bucket.requests_used = 0
        bucket.actual_miss_chars_used = 0


def _effective_limits(
    settings: Settings,
    override: TranslationQuotaOverride | None,
) -> EffectiveTranslationQuota:
    if override is not None and override.is_disabled:
        return EffectiveTranslationQuota(0, 0, True)
    return EffectiveTranslationQuota(
        requests=(
            override.requests
            if override is not None and override.requests is not None
            else settings.translation_quota_requests_per_minute
        ),
        actual_miss_chars=(
            override.actual_miss_chars
            if override is not None and override.actual_miss_chars is not None
            else settings.translation_quota_user_miss_chars_per_minute
        ),
        is_disabled=False,
    )


def _fixed_window_start(now: datetime) -> datetime:
    timestamp = math.floor(now.timestamp() / QUOTA_WINDOW_SECONDS) * QUOTA_WINDOW_SECONDS
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _retry_after_seconds(window_start: datetime, now: datetime) -> int:
    remaining = (window_start + timedelta(seconds=QUOTA_WINDOW_SECONDS) - now).total_seconds()
    return max(1, math.ceil(remaining))


def _user_bucket_key(user_id: UUID) -> str:
    return f"user:{user_id}"


__all__ = [
    "EffectiveTranslationQuota",
    "QuotaReservation",
    "QUOTA_WINDOW_SECONDS",
    "TRANSLATION_DISABLED_FOR_USER",
    "TRANSLATION_QUOTA_GLOBAL_CHARS_EXCEEDED",
    "TRANSLATION_QUOTA_REQUESTS_EXCEEDED",
    "TRANSLATION_QUOTA_STORAGE_UNAVAILABLE",
    "TRANSLATION_QUOTA_USER_CHARS_EXCEEDED",
    "TranslationDisabledForUser",
    "TranslationQuotaExceeded",
    "TranslationQuotaStorageUnavailable",
    "TranslationQuotaStore",
    "QuotaExceeded",
    "QuotaStorageUnavailable",
]
