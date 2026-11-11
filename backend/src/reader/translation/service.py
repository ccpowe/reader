"""The one translation service: cache, claim, call the model, store.

`Translator.translate(texts, purpose, locale, engine)`:
  1. Look each text up in `translations` (purpose + text hash + locale + engine
     fingerprint). A finished translation is returned as is.
  2. Claim the texts nobody is working on: insert a "running" row, or take over a
     pending one, a failed one after a short pause, or a running one whose worker
     vanished (started more than 5 minutes ago). Claiming is one atomic statement
     per row, so the same text arriving twice at once is sent to the model once.
  3. Translate the claimed texts in batches: reserve characters from the global
     budget (once a model slot is free), call the engine, check markers, write each
     result. A batch the model garbled is retried item by item; an item whose
     markers broke is retried once with feedback.

A claim is identified by the row's `attempts` value at claim time. Before each
model call the run renews `started_at` on every row it still holds, and every write is
fenced on that value, so a run that was taken over never overwrites the newer one.
  4. Wait up to `wait` seconds for step 3, then report what is done; the work
     carries on in the background and a later identical request picks it up.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reader.core.settings import Settings
from reader.models import Translation
from reader.translation import budget
from reader.translation.engines import Engine, EngineError, EnginePool
from reader.translation.prompt import (
    PromptItem,
    batches,
    build_messages,
    parse_reply,
    uses_markers,
)
from reader.translation.rich_text import validate_tokens

logger = logging.getLogger(__name__)

STALE_RUNNING = timedelta(minutes=5)
FAILED_PAUSE = timedelta(seconds=60)
# Reading a translation marks it used at most this often, so reads rarely write.
USE_MARK_INTERVAL = timedelta(days=1)


def normalize(text: str) -> str:
    """Whitespace differences never change a translation."""
    return " ".join(text.split())


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Outcome:
    status: str  # succeeded | pending | failed
    text: str | None = None
    error_code: str | None = None
    retry_after: float | None = None


@dataclass(frozen=True)
class _Claimed:
    id: UUID
    text: str
    attempt: int


class Translator:
    """Lives as long as its process; owns the background translation tasks."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        engines: EnginePool | None = None,
    ):
        self.factory = factory
        self.settings = settings
        self.engines = engines or EnginePool(settings)
        self._tasks: set[asyncio.Task] = set()

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.engines.aclose()

    async def translate(
        self,
        texts: Sequence[str],
        purpose: str,
        target_locale: str,
        engine: Engine,
        *,
        wait: float,
    ) -> list[Outcome]:
        by_hash = {text_hash(text): normalize(text) for text in texts}
        claimed = await self._claim(by_hash, purpose, target_locale, engine)
        if claimed:
            task = asyncio.create_task(self.run(claimed, purpose, target_locale, engine))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            await asyncio.wait({task}, timeout=wait)
        outcomes = await self._read(list(by_hash), purpose, target_locale, engine)
        return [outcomes[text_hash(text)] for text in texts]

    # --- Claiming and reading -------------------------------------------------------

    async def _claim(
        self, by_hash: dict[str, str], purpose: str, locale: str, engine: Engine
    ) -> list[_Claimed]:
        now = datetime.now(UTC)
        identity = {
            "purpose": purpose,
            "target_locale": locale,
            "engine_fingerprint": engine.fingerprint,
        }
        takeable = or_(
            Translation.status == "pending",
            (Translation.status == "running") & (Translation.started_at < now - STALE_RUNNING),
            (Translation.status == "failed") & (Translation.finished_at < now - FAILED_PAUSE),
        )
        claimed = []
        async with self.factory() as db:
            # Rows are locked in hash order, so overlapping requests cannot deadlock.
            for source_hash, text in sorted(by_hash.items()):
                statement = pg_insert(Translation).values(
                    **identity,
                    source_hash=source_hash,
                    source_text=text,
                    status="running",
                    attempts=1,
                    started_at=now,
                    last_used_at=now,
                )
                statement = statement.on_conflict_do_update(
                    constraint="uq_translations_identity",
                    set_={
                        "status": "running",
                        "attempts": Translation.attempts + 1,
                        "started_at": now,
                        "finished_at": None,
                        "error_code": None,
                        "last_used_at": now,
                    },
                    where=takeable,
                ).returning(Translation.id, Translation.attempts)
                row = (await db.execute(statement)).first()
                if row is not None:
                    claimed.append(_Claimed(row.id, text, row.attempts))
            await db.commit()
        # Back to reading order: the model may use neighbouring texts for context.
        order = {text: index for index, text in enumerate(by_hash.values())}
        return sorted(claimed, key=lambda row: order[row.text])

    async def _read(
        self, hashes: list[str], purpose: str, locale: str, engine: Engine
    ) -> dict[str, Outcome]:
        now = datetime.now(UTC)
        async with self.factory() as db:
            rows = list(
                await db.scalars(
                    select(Translation).where(
                        Translation.purpose == purpose,
                        Translation.target_locale == locale,
                        Translation.engine_fingerprint == engine.fingerprint,
                        Translation.source_hash.in_(hashes),
                    )
                )
            )
            stale = [
                row.id
                for row in rows
                if row.status == "succeeded" and row.last_used_at < now - USE_MARK_INTERVAL
            ]
            if stale:
                await db.execute(
                    update(Translation).where(Translation.id.in_(stale)).values(last_used_at=now)
                )
                await db.commit()
        found = {row.source_hash: row for row in rows}
        outcomes = {}
        for source_hash in hashes:
            row = found.get(source_hash)
            if row is None or row.status in {"pending", "running"}:
                outcomes[source_hash] = Outcome("pending")
            elif row.status == "succeeded":
                outcomes[source_hash] = Outcome("succeeded", text=row.translated_text)
            else:
                # A failed text may be tried again after the pause (the minute budget
                # has renewed by then; a spent daily budget fails again).
                outcomes[source_hash] = Outcome(
                    "failed", error_code=row.error_code, retry_after=FAILED_PAUSE.total_seconds()
                )
        return outcomes

    # --- Running --------------------------------------------------------------------

    async def run(self, claimed: list[_Claimed], purpose: str, locale: str, engine: Engine) -> None:
        """Translate claimed rows; every row still held ends succeeded or failed."""
        held = {row.id: row.attempt for row in claimed}
        items = [PromptItem(str(row.id), row.text) for row in claimed]
        try:
            for batch in batches(
                items,
                max_items=self.settings.translation_max_items_per_request,
                max_chars=self.settings.translation_max_chars_per_request,
            ):
                await self._run_batch(batch, held, purpose, locale, engine)
        except asyncio.CancelledError:
            # Shutting down: the rows stay "running" and are taken over after 5 minutes.
            raise
        except Exception as exc:
            # The exception type only: messages can carry text being translated.
            logger.error("Translation run failed: purpose=%s error=%s", purpose, type(exc).__name__)
            await self._fail(held, list(held), "internal_error")

    async def _renew(self, held: dict[UUID, int]) -> None:
        """Extend the claim on every row not yet finished; forget rows taken over."""
        if not held:
            return
        async with self.factory() as db:
            kept = set(
                await db.scalars(
                    update(Translation)
                    .where(_held_by(held, list(held)))
                    .values(started_at=datetime.now(UTC))
                    .returning(Translation.id)
                )
            )
            await db.commit()
        for row_id in list(held):
            if row_id not in kept:
                del held[row_id]

    async def _run_batch(
        self,
        batch: list[PromptItem],
        held: dict[UUID, int],
        purpose: str,
        locale: str,
        engine: Engine,
    ) -> None:
        # Before every model call: keep the claim alive, drop rows taken over meanwhile.
        await self._renew(held)
        batch = [item for item in batch if UUID(item.item_id) in held]
        if not batch:
            return
        ids = [UUID(item.item_id) for item in batch]

        async def reserve() -> None:
            await budget.reserve(self.factory, self.settings, sum(len(i.text) for i in batch))

        system, user = build_messages(batch, purpose, locale)
        try:
            reply = await self.engines.complete(engine, system, user, before=reserve)
            translated = parse_reply(reply, batch)
        except budget.QuotaExceeded:
            await self._fail(held, ids, "translation_quota_exceeded")
            return
        except EngineError as exc:
            if exc.isolate and len(batch) > 1:
                for item in batch:
                    await self._run_batch([item], held, purpose, locale, engine)
                return
            logger.warning("Translation call failed: engine=%s code=%s", engine.id, exc.code)
            await self._fail(held, ids, exc.code)
            return
        done: dict[UUID, str] = {}
        retry: list[PromptItem] = []
        for item in batch:
            text = translated[item.item_id]
            if uses_markers(purpose, item.text) and validate_tokens(item.text, text):
                if item.marker_feedback:
                    await self._fail(held, [UUID(item.item_id)], "marker_mismatch")
                else:
                    retry.append(PromptItem(item.item_id, item.text, marker_feedback=True))
                continue
            done[UUID(item.item_id)] = text
        await self._succeed(held, done)
        for item in retry:
            await self._run_batch([item], held, purpose, locale, engine)

    async def _succeed(self, held: dict[UUID, int], done: dict[UUID, str]) -> None:
        if not done:
            return
        now = datetime.now(UTC)
        async with self.factory() as db:
            for row_id, text in done.items():
                await db.execute(
                    update(Translation)
                    .where(_held_by(held, [row_id]))
                    .values(
                        status="succeeded",
                        translated_text=text,
                        finished_at=now,
                        error_code=None,
                    )
                )
            await db.commit()
        for row_id in done:
            held.pop(row_id, None)

    async def _fail(self, held: dict[UUID, int], ids: list[UUID], code: str) -> None:
        ids = [row_id for row_id in ids if row_id in held]
        if not ids:
            return
        async with self.factory() as db:
            await db.execute(
                update(Translation)
                .where(_held_by(held, ids))
                .values(status="failed", finished_at=datetime.now(UTC), error_code=code[:120])
            )
            await db.commit()
        for row_id in ids:
            held.pop(row_id, None)


def _held_by(held: dict[UUID, int], ids: list[UUID]):
    """Rows still running under this claim (not taken over since)."""
    return (
        tuple_(Translation.id, Translation.attempts).in_([(row_id, held[row_id]) for row_id in ids])
    ) & (Translation.status == "running")
