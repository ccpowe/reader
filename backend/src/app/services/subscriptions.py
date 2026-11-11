"""Use-cases for creating shared sources and personal subscriptions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import SourceKind, SourceStatus, SourceVisibility, SyncPhase
from app.ingestion.source_identity import SourceIdentity
from app.services.ranking_snapshots import ensure_reddit_snapshots
from app.storage.locks import acquire_user_transaction_lock
from app.storage.models import FeedSource, SourceSubscription, SourceSyncState
from app.storage.profiles import ensure_profile

_USE_SOURCE_DISPLAY_NAME = object()


class SourceSubscriptionLimitExceeded(RuntimeError):
    """A user has reached the durable Reddit community subscription cap."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"At most {limit} Reddit communities may be subscribed.")
        self.limit = limit


async def _check_reddit_subscription_limit(
    session: AsyncSession,
    *,
    user_id: UUID,
    canonical_key: str,
    limit: int,
) -> None:
    """Serialize a user's count check so concurrent creates cannot exceed the cap."""
    await acquire_user_transaction_lock(
        session,
        namespace="reddit-subscriptions",
        user_id=user_id,
    )
    already_subscribed = bool(
        await session.scalar(
            select(
                exists().where(
                    SourceSubscription.user_id == user_id,
                    SourceSubscription.source_id == FeedSource.id,
                    FeedSource.canonical_key == canonical_key,
                )
            )
        )
    )
    if already_subscribed:
        return
    subscription_count = int(
        await session.scalar(
            select(func.count())
            .select_from(SourceSubscription)
            .join(FeedSource, FeedSource.id == SourceSubscription.source_id)
            .where(
                SourceSubscription.user_id == user_id,
                FeedSource.kind == SourceKind.REDDIT,
            )
        )
        or 0
    )
    if subscription_count >= limit:
        raise SourceSubscriptionLimitExceeded(limit)


async def subscribe_to_source(
    session: AsyncSession,
    *,
    user_id: UUID,
    identity: SourceIdentity,
    display_name: str | None,
    folder_name: str | None,
    source_config: dict[str, Any] | None = None,
    subscription_display_name: str | None | object = _USE_SOURCE_DISPLAY_NAME,
) -> tuple[FeedSource, SourceSubscription, SourceSyncState, bool]:
    """Commit a shared source and the caller's personal subscription."""
    try:
        result = await _prepare_source_subscription(
            session,
            user_id=user_id,
            identity=identity,
            display_name=display_name,
            folder_name=folder_name,
            source_config=source_config,
            subscription_display_name=subscription_display_name,
        )
        await session.commit()
        await _refresh_subscription_result(session, result)
        return result
    except Exception:
        await session.rollback()
        raise


async def subscribe_to_reddit_source(
    session: AsyncSession,
    *,
    user_id: UUID,
    identity: SourceIdentity,
    folder_name: str | None,
    limit: int,
) -> tuple[FeedSource, SourceSubscription, SourceSyncState, bool]:
    """Serialize quota and commit the subscription with its ranking boards."""
    subreddit = identity.canonical_key.removeprefix("reddit:")
    try:
        await _check_reddit_subscription_limit(
            session, user_id=user_id, canonical_key=identity.canonical_key, limit=limit
        )
        result = await _prepare_source_subscription(
            session,
            user_id=user_id,
            identity=identity,
            display_name=f"r/{subreddit}",
            folder_name=folder_name,
            source_config={"subreddit": subreddit},
        )
        await ensure_reddit_snapshots(session, subreddit)
        await session.commit()
        await _refresh_subscription_result(session, result)
        return result
    except Exception:
        await session.rollback()
        raise


async def _refresh_subscription_result(
    session: AsyncSession,
    result: tuple[FeedSource, SourceSubscription, SourceSyncState, bool],
) -> None:
    source, subscription, sync_state, _ = result
    await session.refresh(source)
    await session.refresh(subscription)
    await session.refresh(sync_state)


async def _prepare_source_subscription(
    session: AsyncSession,
    *,
    user_id: UUID,
    identity: SourceIdentity,
    display_name: str | None,
    folder_name: str | None,
    source_config: dict[str, Any] | None = None,
    subscription_display_name: str | None | object = _USE_SOURCE_DISPLAY_NAME,
) -> tuple[FeedSource, SourceSubscription, SourceSyncState, bool]:
    """Create/reuse a shared source and create the caller's subscription.

    Returns the source, subscription, durable sync state, and whether a new
    global source was created. The unique source key is the final authority
    under concurrent requests; a nested transaction safely handles a
    competing insert.
    """
    await ensure_profile(session, user_id)
    source = await _find_source(session, identity.canonical_key)
    created_source = False
    if source is None:
        candidate = FeedSource(
            kind=identity.kind,
            canonical_key=identity.canonical_key,
            canonical_url=identity.canonical_url,
            display_name=display_name,
            config=source_config or {},
            visibility=SourceVisibility.SHARED,
            status=SourceStatus.PENDING,
        )
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
            source = candidate
            created_source = True
        except IntegrityError:
            source = await _find_source(session, identity.canonical_key)
            if source is None:  # Defensive: a concurrent transaction was rolled back.
                raise

    sync_state = await session.get(SourceSyncState, source.id, with_for_update=True)
    if sync_state is None:
        is_reddit = identity.kind == SourceKind.REDDIT
        await session.execute(
            pg_insert(SourceSyncState)
            .values(
                source_id=source.id,
                phase=SyncPhase.IDLE,
                committed_checkpoint={},
                pending_checkpoint={},
                continuation=None,
                provider_mode="reddit_snapshot" if is_reddit else None,
                initial_sync_completed=is_reddit,
                next_scan_at=None if is_reddit else datetime.now(UTC),
                consecutive_failures=0,
                consecutive_limit_runs=0,
                gap_detected=False,
            )
            .on_conflict_do_nothing(index_elements=[SourceSyncState.source_id])
        )
        sync_state = await session.get(
            SourceSyncState, source.id, populate_existing=True, with_for_update=True
        )
        if sync_state is None:
            raise RuntimeError("Source sync state upsert did not return the stored row.")

    subscription = await _find_subscription(session, user_id, source.id)
    if subscription is None:
        viewed_update_sequence = source.latest_update_sequence
        subscription_id = uuid4()
        inserted_subscription_id = await session.scalar(
            pg_insert(SourceSubscription)
            .values(
                id=subscription_id,
                user_id=user_id,
                source_id=source.id,
                custom_name=(
                    display_name
                    if subscription_display_name is _USE_SOURCE_DISPLAY_NAME
                    else subscription_display_name
                ),
                folder_name=folder_name,
                is_enabled=True,
                include_in_home=True,
                last_viewed_update_sequence=viewed_update_sequence,
            )
            .on_conflict_do_nothing(
                index_elements=[SourceSubscription.user_id, SourceSubscription.source_id]
            )
            .returning(SourceSubscription.id)
        )
        subscription = await _find_subscription(session, user_id, source.id)
        if subscription is None:
            raise RuntimeError("Source subscription upsert did not return the stored row.")
        if inserted_subscription_id is not None and identity.kind != SourceKind.REDDIT:
            sync_state.next_scan_at = datetime.now(UTC)

    elif not subscription.is_enabled:
        subscription.is_enabled = True
        if identity.kind != SourceKind.REDDIT:
            sync_state.next_scan_at = datetime.now(UTC)

    await session.flush()
    return source, subscription, sync_state, created_source


async def _find_source(session: AsyncSession, canonical_key: str) -> FeedSource | None:
    return await session.scalar(
        select(FeedSource).where(FeedSource.canonical_key == canonical_key).with_for_update()
    )


async def _find_subscription(
    session: AsyncSession, user_id: UUID, source_id: UUID
) -> SourceSubscription | None:
    return await session.scalar(
        select(SourceSubscription).where(
            SourceSubscription.user_id == user_id,
            SourceSubscription.source_id == source_id,
        )
    )
