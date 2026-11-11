"""Title translation in the background, and the daily clean-up of translations.

- The scanner calls `request_titles` in the transaction that stores new articles:
  one "pending" row per title and per (locale, engine) wanted by the source's
  followers who translate automatically.
- The `title_translation` worker loop calls `translate_pending_titles`: it takes
  pending rows (and failed or abandoned ones, up to 3 attempts), groups them by
  locale and engine and runs them through the one Translator.
- The feed calls `title_translations` to show finished titles for the reader's
  current preference; anything missing the app asks for via POST /translations.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from reader.core.settings import Settings
from reader.models import Subscription, Translation, TranslationPreference
from reader.translation.engines import Engine, catalog, find_engine
from reader.translation.service import (
    FAILED_PAUSE,
    STALE_RUNNING,
    USE_MARK_INTERVAL,
    Claimed,
    Translator,
    normalize,
    text_hash,
)

# The background gives up after this many attempts; opening the feed still retries.
BACKGROUND_ATTEMPTS = 3


@dataclass(frozen=True)
class Preference:
    target_locale: str
    enabled: bool
    # None when the chosen (or default) engine does not exist.
    engine: Engine | None


async def load_preference(db: AsyncSession, user_id: UUID, settings: Settings) -> Preference:
    """The user's translation preference, with server defaults filled in."""
    row = await db.get(TranslationPreference, user_id)
    return Preference(
        target_locale=row.target_locale if row else settings.translation_default_target_locale,
        enabled=row.enabled if row else True,
        engine=find_engine(settings, row.engine_id if row else None),
    )


# --- Requesting (from the scanner) ---------------------------------------------------


async def request_titles(
    db: AsyncSession, settings: Settings, source_id: UUID, titles: Iterable[str]
) -> int:
    """Queue titles for every (locale, engine) the source's followers translate into.

    Runs inside the caller's transaction; already known titles are left alone.
    """
    titles = {normalize(title) for title in titles if title.strip()}
    if not titles:
        return 0
    followers = await db.execute(
        select(
            TranslationPreference.target_locale,
            TranslationPreference.enabled,
            TranslationPreference.engine_id,
        )
        .select_from(Subscription)
        .outerjoin(TranslationPreference, TranslationPreference.user_id == Subscription.user_id)
        .where(Subscription.source_id == source_id, Subscription.is_enabled.is_(True))
    )
    targets: set[tuple[str, str]] = set()
    for locale, enabled, engine_id in followers:
        if enabled is False:
            continue
        engine = find_engine(settings, engine_id)
        if engine is None or not engine.available:
            continue
        targets.add((locale or settings.translation_default_target_locale, engine.fingerprint))
    rows = pending_rows(titles, targets)
    if not rows:
        return 0
    await db.execute(
        pg_insert(Translation)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_translations_identity")
    )
    return len(rows)


def pending_rows(titles: Iterable[str], targets: Iterable[tuple[str, str]]) -> list[dict]:
    """Rows to insert, in the same lock order as Translator._claim (by hash), so a scan
    and a POST /translations for overlapping titles never deadlock."""
    rows = [
        {
            "purpose": "title",
            "source_hash": text_hash(title),
            "target_locale": locale,
            "engine_fingerprint": fingerprint,
            "source_text": title,
            "status": "pending",
        }
        for locale, fingerprint in targets
        for title in titles
    ]
    return sorted(
        rows, key=lambda row: (row["source_hash"], row["target_locale"], row["engine_fingerprint"])
    )


# --- The worker loop ----------------------------------------------------------------


async def translate_pending_titles(translator: Translator, limit: int = 100) -> int:
    """Take up to `limit` title rows and translate them; return how many were taken."""
    now = datetime.now(UTC)
    retryable = Translation.attempts < BACKGROUND_ATTEMPTS
    takeable = (
        select(Translation.id)
        .where(
            Translation.purpose == "title",
            or_(
                Translation.status == "pending",
                (Translation.status == "running")
                & (Translation.started_at < now - STALE_RUNNING)
                & retryable,
                (Translation.status == "failed")
                & (Translation.finished_at < now - FAILED_PAUSE)
                & retryable,
            ),
        )
        .order_by(Translation.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    async with translator.factory() as db:
        # Abandoned on its last background attempt: end it, or it stays "running".
        await db.execute(
            update(Translation)
            .where(
                Translation.purpose == "title",
                Translation.status == "running",
                Translation.started_at < now - STALE_RUNNING,
                Translation.attempts >= BACKGROUND_ATTEMPTS,
            )
            .values(status="failed", finished_at=now, error_code="abandoned")
        )
        claimed = list(
            await db.execute(
                update(Translation)
                .where(Translation.id.in_(takeable.scalar_subquery()))
                .values(
                    status="running",
                    attempts=Translation.attempts + 1,
                    started_at=now,
                    finished_at=None,
                    error_code=None,
                )
                .returning(
                    Translation.id,
                    Translation.source_text,
                    Translation.attempts,
                    Translation.target_locale,
                    Translation.engine_fingerprint,
                )
            )
        )
        await db.commit()
    if not claimed:
        return 0
    engines = {engine.fingerprint: engine for engine in catalog(translator.settings)}
    groups: dict[tuple[str, str], list[Claimed]] = defaultdict(list)
    for row in claimed:
        groups[(row.target_locale, row.engine_fingerprint)].append(
            Claimed(row.id, row.source_text, row.attempts)
        )
    for (locale, fingerprint), rows in groups.items():
        engine = engines.get(fingerprint)
        if engine is None or not engine.available:
            # The engine was removed or reconfigured; these titles wait for a request.
            await translator.fail_claimed(rows, "engine_unavailable")
            continue
        await translator.run(rows, "title", locale, engine)
    return len(claimed)


# --- Reading (for the feed) ---------------------------------------------------------


@dataclass(frozen=True)
class TitleTranslation:
    text: str
    engine_fingerprint: str


async def title_translations(
    db: AsyncSession, user_id: UUID, settings: Settings, titles: Iterable[str]
) -> dict[str, TitleTranslation]:
    """Finished translations of these titles for the user's preference, by original title."""
    preference = await load_preference(db, user_id, settings)
    if not preference.enabled or preference.engine is None:
        return {}
    by_hash: dict[str, list[str]] = defaultdict(list)
    for title in titles:
        by_hash[text_hash(title)].append(title)
    if not by_hash:
        return {}
    fingerprint = preference.engine.fingerprint
    rows = list(
        await db.execute(
            select(
                Translation.id,
                Translation.source_hash,
                Translation.translated_text,
                Translation.last_used_at,
            ).where(
                Translation.purpose == "title",
                Translation.target_locale == preference.target_locale,
                Translation.engine_fingerprint == fingerprint,
                Translation.status == "succeeded",
                Translation.source_hash.in_(list(by_hash)),
            )
        )
    )
    now = datetime.now(UTC)
    stale = [row.id for row in rows if row.last_used_at < now - USE_MARK_INTERVAL]
    if stale:
        await db.execute(
            update(Translation).where(Translation.id.in_(stale)).values(last_used_at=now)
        )
        await db.commit()
    found = {}
    for row in rows:
        for title in by_hash[row.source_hash]:
            found[title] = TitleTranslation(row.translated_text, fingerprint)
    return found


# --- Clean-up -----------------------------------------------------------------------


async def prune_translations(db: AsyncSession, keep_days: int) -> int:
    """Delete translations unused for `keep_days`; work in progress is kept."""
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    removed = (
        await db.execute(
            delete(Translation).where(
                Translation.last_used_at < cutoff, Translation.status != "running"
            )
        )
    ).rowcount
    await db.commit()
    return removed
