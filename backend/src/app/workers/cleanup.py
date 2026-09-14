"""Bounded content and translation lifecycle cleanup for shared sources."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings
from app.storage.models import (
    Content,
    FeedSource,
    SourceEntry,
    SourceSubscription,
    TranslationWork,
    UserSavedContent,
)
from app.translation.engines import managed_engine_catalog


def obsolete_model_work_statement(settings: Settings, *, limit: int = 100):
    """Cancel bounded obsolete work without rewriting its immutable identity.

    Live leases are left to finish on their original model. After a coordinated
    restart, abandoned leases become eligible on a subsequent cleanup cycle.
    Successful cache artifacts are never touched.
    """
    now = datetime.now(UTC)
    predicates = [
        or_(
            *(
                and_(
                    TranslationWork.engine_id == engine.engine_id,
                    TranslationWork.model_name.is_distinct_from(engine.model_name),
                )
                for engine in managed_engine_catalog(settings)
            )
        ),
        or_(
            TranslationWork.status == "pending",
            and_(
                TranslationWork.status == "running",
                TranslationWork.lease_expires_at <= now,
            ),
        ),
    ]
    candidates = (
        select(TranslationWork.id)
        .where(*predicates)
        .order_by(TranslationWork.created_at, TranslationWork.id)
        .limit(limit)
    )
    return (
        update(TranslationWork)
        .where(TranslationWork.id.in_(candidates), *predicates)
        .values(
            status="cancelled",
            lease_token=None,
            lease_expires_at=None,
            finished_at=now,
            last_attempt_finished_at=now,
            error_code="translation_model_changed",
            error_message="Translation model configuration changed.",
            error_retryable=False,
            updated_at=now,
        )
        .returning(TranslationWork.id)
    )


async def cancel_obsolete_model_work(session: AsyncSession, settings: Settings) -> int:
    from app.translation.lifecycle import lock_shared_lifecycle

    # The same cleanup transaction later collects artifacts. Acquire lifecycle
    # before any work row, matching publication and preventing lock inversion.
    await lock_shared_lifecycle(session)
    result = await session.execute(obsolete_model_work_statement(settings))
    return len(result.scalars().all())


def orphan_source_delete_statement(*, limit: int = 100):
    """Build an atomic deletion for sources no user still needs.

    Content retention first locks and removes eligible unsaved contents. Only
    empty sources are removed here, so an orphan with a saved or busy entry is
    retained. The predicate is repeated on DELETE for concurrent subscriptions.
    """
    has_subscription = exists(
        select(SourceSubscription.id).where(SourceSubscription.source_id == FeedSource.id)
    )
    has_saved_entry = exists(
        select(UserSavedContent.id)
        .join(SourceEntry, SourceEntry.content_id == UserSavedContent.content_id)
        .where(SourceEntry.source_id == FeedSource.id)
    )
    has_content = exists(select(Content.id).where(Content.authority_source_id == FeedSource.id))
    candidates = (
        select(FeedSource.id)
        .where(~has_subscription, ~has_saved_entry, ~has_content)
        .order_by(FeedSource.updated_at)
        .limit(limit)
    )
    return (
        delete(FeedSource)
        .where(
            FeedSource.id.in_(candidates),
            ~has_subscription,
            ~has_saved_entry,
            ~has_content,
        )
        .returning(FeedSource.id)
    )


async def purge_orphan_sources(session: AsyncSession, *, limit: int = 100) -> int:
    """Run bounded retention and cache cleanup, then commit their transaction."""
    from app.translation.lifecycle import lock_shared_lifecycle, purge_translation_cache
    from app.workers.content_retention import purge_retained_contents

    await lock_shared_lifecycle(session)
    retained = await purge_retained_contents(session, source_limit=limit)
    result = await session.execute(orphan_source_delete_statement(limit=limit))
    deleted_ids = list(result.scalars())
    translations = await purge_translation_cache(session)
    await session.commit()
    return len(deleted_ids) + retained + translations
