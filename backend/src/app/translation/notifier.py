"""Best-effort PostgreSQL wake-up signal for the Translation Executor.

Durability lives in ``translation_work``. LISTEN/NOTIFY only removes idle
polling latency, so a dropped listener safely degrades to periodic reads.
"""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType

import asyncpg

from app.core.settings import Settings
from app.translation.store import WORK_NOTIFY_CHANNEL

logger = logging.getLogger(__name__)


class TranslationWorkSignal:
    """Wake a worker on committed demand, with polling as the fallback."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._event = asyncio.Event()
        self._connection: asyncpg.Connection | None = None
        self._connected_once = False
        self._warned = False

    async def __aenter__(self) -> TranslationWorkSignal:
        await self._connect()
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def wait(
        self,
        timeout_seconds: float,
        *,
        fallback_timeout_seconds: float,
    ) -> None:
        """Wait for committed work, or return on the safety-poll deadline."""
        if self._connection is None or self._connection.is_closed():
            await self._connect()
        wait_seconds = timeout_seconds if self._connection is not None else fallback_timeout_seconds
        try:
            await asyncio.wait_for(self._event.wait(), timeout=wait_seconds)
        except TimeoutError:
            return
        finally:
            self._event.clear()

    async def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None or connection.is_closed():
            return
        try:
            await connection.remove_listener(WORK_NOTIFY_CHANNEL, self._on_notify)
        finally:
            await connection.close()

    async def _connect(self) -> None:
        if not self._settings.database_url:
            return
        if self._connection is not None and not self._connection.is_closed():
            return
        try:
            connection = await asyncpg.connect(
                _asyncpg_dsn(self._settings.database_url),
                ssl="require" if self._settings.database_ssl else False,
            )
            await connection.add_listener(WORK_NOTIFY_CHANNEL, self._on_notify)
            self._connection = connection
            if self._warned:
                logger.info("Translation work notification listener reconnected.")
            elif not self._connected_once:
                logger.info("Translation work notification listener connected.")
            self._connected_once = True
            self._warned = False
        except Exception:
            if not self._warned:
                logger.warning(
                    "Translation work notifications unavailable; using safety polling.",
                    exc_info=True,
                )
            self._warned = True
            self._connection = None

    def _on_notify(
        self,
        _connection: asyncpg.Connection,
        _process_id: int,
        _channel: str,
        _payload: str,
    ) -> None:
        self._event.set()


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
