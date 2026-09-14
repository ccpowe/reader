"""Storage lifetime rules independent of user subscriptions and provider calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import String, all_, and_, bindparam, delete, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.locks import acquire_user_transaction_lock
from app.storage.models import (
    Content,
    RankingSnapshot,
    SourceEntry,
    TranslationArtifact,
    TranslationWork,
)
from app.translation.domain import source_text_hash

EPHEMERAL_PURPOSES = ("web_segment", "caption")
SHARED_PURPOSES = ("title", "ranking_title", "excerpt", "ranking_description")


async def lock_shared_lifecycle(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('translation-shared-lifecycle'))")
    )


async def live_shared_hashes(session: AsyncSession) -> dict[str, set[str]]:
    """Recover references from current authoritative records, including legacy caches.

    The same normalization as TranslationDemand is used. No inferred ownership is
    assigned to arbitrary reader paragraphs whose serialized fragments are absent
    from the stored content model.
    """
    titles: set[str] = set()
    descriptions: set[str] = set()
    for title, excerpt in (await session.execute(select(Content.title, Content.excerpt))).all():
        if title and title.strip():
            titles.add(source_text_hash(title))
        if excerpt and excerpt.strip():
            descriptions.add(source_text_hash(excerpt))
    for title in await session.scalars(select(SourceEntry.title)):
        if title and title.strip():
            titles.add(source_text_hash(title))
    for payload in await session.scalars(select(RankingSnapshot.payload)):
        for item in (payload or {}).get("items", []):
            if not isinstance(item, dict):
                continue
            title, description = item.get("title"), item.get("description")
            if isinstance(title, str) and title.strip():
                titles.add(source_text_hash(title))
            if isinstance(description, str) and description.strip():
                descriptions.add(source_text_hash(description))
    return {
        "title": titles,
        "ranking_title": titles,
        "excerpt": descriptions,
        "ranking_description": descriptions,
    }


async def purge_translation_cache(session: AsyncSession, *, limit: int = 1000) -> int:
    """Bounded deletion; expired rows are also rejected synchronously on reads.

    Work is deleted before artifacts so a leased worker cannot recreate removed
    successes. A worker that already locked its row finishes before the delete;
    its artifact is then removed in the next statement.
    """
    await lock_shared_lifecycle(session)
    refs = await live_shared_hashes(session)
    removed = 0
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    from app.core.settings import get_settings
    from app.translation.engines import managed_engine_catalog

    engines = managed_engine_catalog(get_settings())
    for model in (TranslationWork, TranslationArtifact):
        obsolete_model = or_(
            model.engine_id.not_in([e.engine_id for e in engines]),
            *(
                and_(
                    model.engine_id == e.engine_id, model.model_name.is_distinct_from(e.model_name)
                )
                for e in engines
            ),
        )
        missing_title = and_(
            model.owner_id.is_(None),
            model.purpose.in_(("title", "ranking_title")),
            model.source_hash
            != all_(bindparam("live_titles", list(refs["title"]), type_=ARRAY(String))),
        )
        missing_description = and_(
            model.owner_id.is_(None),
            model.purpose.in_(("excerpt", "ranking_description")),
            model.source_hash
            != all_(bindparam("live_descriptions", list(refs["excerpt"]), type_=ARRAY(String))),
        )
        expired_ephemeral = and_(
            model.purpose.in_(EPHEMERAL_PURPOSES),
            or_(model.owner_id.is_(None), model.created_at <= cutoff, obsolete_model),
        )
        candidates = (
            select(model.id)
            .where(or_(missing_title, missing_description, expired_ephemeral))
            .order_by(model.created_at, model.id)
            .limit(limit)
        )
        result = await session.execute(delete(model).where(model.id.in_(candidates)))
        removed += int(result.rowcount or 0)
    return removed


async def clear_user_ephemeral_cache(
    session: AsyncSession,
    owner_id: UUID,
    *,
    keep_fingerprint: str | None = None,
) -> int:
    """Caller holds the preference lock; live tasks lose publish authority too."""
    removed = 0
    for model in (TranslationWork, TranslationArtifact):
        predicates = [model.owner_id == owner_id, model.purpose.in_(EPHEMERAL_PURPOSES)]
        if keep_fingerprint is not None:
            predicates.append(model.engine_fingerprint != keep_fingerprint)
        result = await session.execute(delete(model).where(*predicates))
        removed += int(result.rowcount or 0)
    return removed


async def current_ephemeral_context(session: AsyncSession, owner_id: UUID):
    from app.translation.projection import get_translation_context

    await acquire_user_transaction_lock(
        session, namespace="translation-preference", user_id=owner_id
    )
    return await get_translation_context(session, owner_id, fresh=True)


def ephemeral_fingerprint(context) -> str | None:
    if context.engine is None or not context.enabled:
        return None
    return source_text_hash(
        context.engine.fingerprint + ":" + (context.cache_generation or "default")
    )
