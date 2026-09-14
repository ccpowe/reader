"""Bound shared content storage without changing ingestion checkpoints."""

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import (
    Content,
    FeedSource,
    IngestionCandidate,
    SourceEntry,
    SourceSubscription,
    UserSavedContent,
)


def _unsaved():
    return ~exists(select(UserSavedContent.id).where(UserSavedContent.content_id == Content.id))


def _expired_ids(source_id, keep: int):
    # DISTINCT ON chooses the entry actually appearing first in the feed, without
    # charging duplicate source entries against the content quota.
    latest_entry = (
        select(SourceEntry.content_id, SourceEntry.feed_sort_at, SourceEntry.id)
        .where(SourceEntry.source_id == source_id)
        .distinct(SourceEntry.content_id)
        .order_by(SourceEntry.content_id, SourceEntry.feed_sort_at.desc(), SourceEntry.id.desc())
        .subquery()
    )
    return (
        select(Content.id)
        .outerjoin(latest_entry, latest_entry.c.content_id == Content.id)
        .where(Content.authority_source_id == source_id, _unsaved())
        .order_by(
            func.coalesce(latest_entry.c.feed_sort_at, Content.created_at).desc(),
            latest_entry.c.id.desc().nulls_last(),
            Content.id.desc(),
        )
        .offset(keep)
    )


async def purge_retained_contents(
    session: AsyncSession, *, keep_per_source: int = 500, source_limit: int = 100
) -> int:
    """Delete excess unsaved contents; the caller owns the transaction/commit.

    Disabled subscriptions still count as subscriptions. With no subscriptions,
    all unsaved contents expire. Saves by any user are exempt from the quota.
    Sources and contents currently locked by another transaction are retried on
    a later pass. Source/content FOR UPDATE locks conflict with subscription/save
    FK key-share locks, followed by fresh reads to observe preceding commits.
    Unfinished ingestion candidates are left to complete before content removal.
    """
    if keep_per_source < 0 or source_limit < 1:
        raise ValueError("Retention limits must be nonnegative with a positive source limit")
    has_subscription = exists(
        select(SourceSubscription.id).where(SourceSubscription.source_id == FeedSource.id)
    )
    ordinary_count = (
        select(func.count(Content.id))
        .where(Content.authority_source_id == FeedSource.id, _unsaved())
        .correlate(FeedSource)
        .scalar_subquery()
    )
    sources = list(
        (
            await session.scalars(
                select(FeedSource.id)
                .where(
                    or_(
                        ordinary_count > keep_per_source,
                        (~has_subscription) & (ordinary_count > 0),
                    )
                )
                .order_by(FeedSource.id)
                .limit(source_limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    removed = 0
    for source_id in sources:
        subscribed = await session.scalar(
            select(exists().where(SourceSubscription.source_id == source_id))
        )
        keep = keep_per_source if subscribed else 0
        expired = _expired_ids(source_id, keep)
        # Acquire candidate locks before content locks, matching ingestion's
        # order. Skip claims held by workers to avoid a worker/cleanup deadlock.
        expired_native_ids = select(SourceEntry.native_id).where(
            SourceEntry.source_id == source_id, SourceEntry.content_id.in_(expired)
        )
        locked_candidates = list(
            (
                await session.scalars(
                    select(IngestionCandidate.id)
                    .where(
                        IngestionCandidate.source_id == source_id,
                        IngestionCandidate.status.in_(("completed", "failed")),
                        or_(
                            IngestionCandidate.content_id.in_(expired),
                            IngestionCandidate.native_id.in_(expired_native_ids),
                        ),
                    )
                    .order_by(IngestionCandidate.id)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        busy = exists(
            select(IngestionCandidate.id).where(
                IngestionCandidate.source_id == source_id,
                or_(
                    IngestionCandidate.content_id == Content.id,
                    IngestionCandidate.native_id.in_(
                        select(SourceEntry.native_id)
                        .where(SourceEntry.content_id == Content.id)
                        .correlate(Content)
                    ),
                ),
                IngestionCandidate.id.not_in(locked_candidates),
            )
        )
        locked_ids = list(
            (
                await session.scalars(
                    select(Content.id)
                    .where(Content.id.in_(expired), ~busy)
                    .order_by(Content.id)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        if not locked_ids:
            continue
        # A save may have committed between candidate selection and locking.
        # Recalculate the quota with a fresh READ COMMITTED statement.
        removable = list(
            (
                await session.scalars(
                    select(Content.id).where(Content.id.in_(locked_ids), Content.id.in_(expired))
                )
            ).all()
        )
        if not removable:
            continue
        native_ids = select(SourceEntry.native_id).where(SourceEntry.content_id.in_(removable))
        await session.execute(
            delete(IngestionCandidate).where(
                IngestionCandidate.source_id == source_id,
                or_(
                    IngestionCandidate.content_id.in_(removable),
                    IngestionCandidate.native_id.in_(native_ids),
                ),
            )
        )
        result = await session.execute(
            delete(Content).where(Content.id.in_(removable)).returning(Content.id)
        )
        removed += len(result.scalars().all())
    return removed
