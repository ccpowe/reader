"""Read-only, per-account subscription update queries for MCP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import Content, FeedSource, SourceEntry, SourceSubscription

PAGE_SIZE = 30


@dataclass(frozen=True)
class SubscriptionCount:
    subscription_id: UUID
    source_id: UUID
    source_name: str | None
    source_kind: str
    count: int


@dataclass(frozen=True)
class UpdateItem:
    content_id: UUID
    source_id: UUID
    title: str
    external_url: str
    content_type: str
    published_at: datetime | None
    fetched_at: datetime
    effective_at: datetime


@dataclass(frozen=True)
class UpdatePage:
    total: int
    items: list[UpdateItem]
    next_key: tuple[datetime, UUID] | None


def _ranked_entries(
    start_time: datetime, end_time: datetime, *, source_ids, cutoff: datetime | None = None
):
    """Count the first observed entry per Content, not later native-ID aliases.

    The cutoff excludes later fetch timestamps before alias ranking. This is a
    live keyset query: concurrent commits, edits, and retention are not frozen.
    """
    entries = (
        select(
            SourceEntry.source_id.label("source_id"),
            SourceEntry.content_id.label("content_id"),
            SourceEntry.id.label("entry_id"),
            Content.published_at.label("published_at"),
            func.min(SourceEntry.fetched_at)
            .over(partition_by=SourceEntry.content_id)
            .label("first_fetched_at"),
            func.row_number()
            .over(
                partition_by=SourceEntry.content_id,
                order_by=(SourceEntry.fetched_at.asc(), SourceEntry.id.asc()),
            )
            .label("content_rank"),
        )
        .join(Content, Content.id == SourceEntry.content_id)
        .where(SourceEntry.source_id.in_(source_ids))
    )
    if cutoff is not None:
        entries = entries.where(SourceEntry.fetched_at <= cutoff)
    entries = entries.subquery()
    effective_at = func.coalesce(entries.c.published_at, entries.c.first_fetched_at)
    return (
        select(
            entries.c.source_id,
            entries.c.entry_id,
            effective_at.label("effective_at"),
        )
        .where(
            entries.c.content_rank == 1,
            effective_at >= start_time,
            effective_at < end_time,
        )
        .subquery()
    )


async def subscription_summary(
    session: AsyncSession,
    *,
    user_id: UUID,
    start_time: datetime,
    end_time: datetime,
    group: str | None,
) -> list[tuple[str | None, SubscriptionCount]]:
    filters = [SourceSubscription.user_id == user_id, SourceSubscription.is_enabled.is_(True)]
    if group is not None:
        filters.append(
            SourceSubscription.folder_name.is_(None)
            if group == ""
            else SourceSubscription.folder_name == group
        )
    source_ids = select(SourceSubscription.source_id).where(*filters)
    ranked = _ranked_entries(start_time, end_time, source_ids=source_ids)
    counts = (
        select(ranked.c.source_id, func.count().label("update_count"))
        .group_by(ranked.c.source_id)
        .subquery()
    )
    rows = await session.execute(
        select(SourceSubscription, FeedSource, func.coalesce(counts.c.update_count, 0))
        .join(FeedSource, FeedSource.id == SourceSubscription.source_id)
        .outerjoin(counts, counts.c.source_id == SourceSubscription.source_id)
        .where(*filters)
        .order_by(SourceSubscription.folder_name, FeedSource.display_name, SourceSubscription.id)
    )
    return [
        (
            subscription.folder_name,
            SubscriptionCount(
                subscription_id=subscription.id,
                source_id=source.id,
                source_name=subscription.custom_name or source.display_name,
                source_kind=str(source.kind),
                count=count,
            ),
        )
        for subscription, source, count in rows
    ]


async def subscription_updates(
    session: AsyncSession,
    *,
    user_id: UUID,
    subscription_id: UUID,
    start_time: datetime,
    end_time: datetime,
    fetched_cutoff: datetime,
    after: tuple[datetime, UUID] | None = None,
) -> UpdatePage | None:
    source_id = await session.scalar(
        select(SourceSubscription.source_id).where(
            SourceSubscription.id == subscription_id,
            SourceSubscription.user_id == user_id,
            SourceSubscription.is_enabled.is_(True),
        )
    )
    if source_id is None:
        return None
    ranked = _ranked_entries(start_time, end_time, source_ids=[source_id], cutoff=fetched_cutoff)
    eligible = (
        select(ranked.c.entry_id, ranked.c.effective_at)
        .where(ranked.c.source_id == source_id)
        .subquery()
    )
    total = await session.scalar(select(func.count()).select_from(eligible)) or 0
    query = (
        select(SourceEntry, Content, eligible.c.effective_at)
        .join(eligible, eligible.c.entry_id == SourceEntry.id)
        .join(Content, Content.id == SourceEntry.content_id)
    )
    if after is not None:
        query = query.where(tuple_(eligible.c.effective_at, SourceEntry.id) < after)
    rows = list(
        await session.execute(
            query.order_by(eligible.c.effective_at.desc(), SourceEntry.id.desc()).limit(
                PAGE_SIZE + 1
            )
        )
    )
    has_more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    items = [
        UpdateItem(
            content_id=content.id,
            source_id=entry.source_id,
            title=entry.title,
            external_url=entry.external_url,
            content_type=str(content.kind),
            published_at=content.published_at,
            fetched_at=entry.fetched_at,
            effective_at=effective_at,
        )
        for entry, content, effective_at in rows
    ]
    next_key = (rows[-1][2], rows[-1][0].id) if has_more else None
    return UpdatePage(total=total, items=items, next_key=next_key)
