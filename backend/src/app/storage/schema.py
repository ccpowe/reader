"""Runtime database schema contract used by deployment readiness."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.configuration import runtime_configuration_missing
from app.core.settings import Settings

logger = logging.getLogger(__name__)

REQUIRED_SCHEMA_REVISION = "20260922_29"
REQUIRED_SCHEMA_CONTRACT = "reader-runtime-v25"


class RuntimeReadinessProbe:
    """Bound and coalesce readiness checks so probes cannot drain the DB pool."""

    def __init__(
        self,
        *,
        cache_seconds: float,
        timeout_seconds: float,
        refresh_timeout_seconds: float = 30.0,
    ) -> None:
        self._cache_seconds = cache_seconds
        self._timeout_seconds = timeout_seconds
        self._refresh_timeout_seconds = refresh_timeout_seconds
        self._lock = asyncio.Lock()
        self._cached_until = 0.0
        self._cached_failures: tuple[str, ...] = ()
        self._inflight: asyncio.Task[list[str]] | None = None

    async def prime(
        self,
        settings: Settings,
        engine: AsyncEngine,
        *,
        timeout_seconds: float,
    ) -> list[str]:
        """Warm a cold database connection, retrying transient startup failures."""
        failures = await self._probe_until_stable(
            settings,
            engine,
            timeout_seconds=timeout_seconds,
        )
        self._cache(failures)
        return failures

    async def check(self, settings: Settings, engine: AsyncEngine | None) -> list[str]:
        """Wait briefly for one shared refresh without cancelling slow recovery."""
        cached = self._fresh_cache()
        if cached is not None:
            return cached

        cached, refresh = await self._cached_or_refresh(settings, engine)
        if cached is not None:
            return cached
        assert refresh is not None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return list(await asyncio.shield(refresh))
        except TimeoutError:
            return ["database_readiness_timeout"]

    async def close(self) -> None:
        """Cancel a detached refresh before its database engine is disposed."""
        async with self._lock:
            refresh = self._inflight
            self._inflight = None
        if refresh is not None and not refresh.done():
            refresh.cancel()
            try:
                await refresh
            except asyncio.CancelledError:
                pass

    def _fresh_cache(self) -> list[str] | None:
        if self._cached_until > monotonic():
            return list(self._cached_failures)
        return None

    async def _cached_or_refresh(
        self,
        settings: Settings,
        engine: AsyncEngine | None,
    ) -> tuple[list[str] | None, asyncio.Task[list[str]] | None]:
        async with self._lock:
            cached = self._fresh_cache()
            if cached is not None:
                return cached, None
            if self._inflight is None or self._inflight.done():
                self._inflight = asyncio.create_task(
                    self._refresh(settings, engine),
                    name="runtime-readiness-refresh",
                )
            return None, self._inflight

    async def _refresh(
        self,
        settings: Settings,
        engine: AsyncEngine | None,
    ) -> list[str]:
        failures = await self._probe_until_stable(
            settings,
            engine,
            timeout_seconds=self._refresh_timeout_seconds,
        )
        self._cache(failures)
        return failures

    def _cache(self, failures: list[str]) -> None:
        self._cached_failures = tuple(failures)
        self._cached_until = monotonic() + self._cache_seconds

    async def _probe_until_stable(
        self,
        settings: Settings,
        engine: AsyncEngine | None,
        *,
        timeout_seconds: float,
    ) -> list[str]:
        deadline = monotonic() + timeout_seconds
        failures = ["database_readiness_timeout"]
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return failures
            try:
                async with asyncio.timeout(remaining):
                    failures = await runtime_readiness_failures(settings, engine)
            except TimeoutError:
                return ["database_readiness_timeout"]
            if "database_or_schema_unavailable" not in failures:
                return failures
            remaining = deadline - monotonic()
            if remaining <= 0:
                return failures
            await asyncio.sleep(min(0.25, remaining))


async def runtime_readiness_failures(
    settings: Settings,
    engine: AsyncEngine | None,
) -> list[str]:
    """Return stable failure codes without exposing credentials or DB details."""
    failures: list[str] = []
    for setting in runtime_configuration_missing(settings):
        # Keep the established readiness error names stable while sharing the
        # exact validation with discovery. Values (especially secrets) never
        # appear in the response.
        failures.append(
            {
                "APP_DATABASE_URL": "missing_database_url",
                "APP_SERVER_ACCESS_TOKEN": "missing_server_access_token",
                "APP_AUTH_JWT_SECRET": "invalid_auth_jwt_secret",
                "APP_SERVER_ID": "missing_server_id",
            }.get(setting, f"missing:{setting}"),
        )
    if not settings.database_url:
        return failures
    if engine is None:
        failures.append("database_engine_unavailable")
        return failures

    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM app_schema_contracts WHERE contract = :contract)"
                ),
                {"contract": REQUIRED_SCHEMA_CONTRACT},
            )
            contract_installed = bool(result.scalar_one())
    except Exception:
        logger.exception("Database readiness check failed.")
        failures.append("database_or_schema_unavailable")
        return failures
    if not contract_installed:
        failures.append("schema_contract_missing")
    return failures
