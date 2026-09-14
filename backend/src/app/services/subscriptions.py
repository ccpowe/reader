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
from app.ingestion.web_feed import configured_web_feed_url, web_feed_probe_required
from app.ingestion.web_rules import WebRuleError, parse_web_rule
from app.services.web_rule_jobs import ensure_web_rule_job
from app.storage.locks import acquire_user_transaction_lock
from app.storage.models import FeedSource, SourceSubscription, SourceSyncState
from app.storage.profiles import ensure_profile

_USE_SOURCE_DISPLAY_NAME = object()


class SourceSubscriptionLimitExceeded(RuntimeError):
    """A user has reached the durable Reddit community subscription cap."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"At most {limit} Reddit communities may be subscribed.")
        self.limit = limit


async def enforce_reddit_subscription_limit(
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
        await session.rollback()
        raise SourceSubscriptionLimitExceeded(limit)


async def subscribe_to_shared_source(
    session: AsyncSession,
    *,
    user_id: UUID,
    identity: SourceIdentity,
    display_name: str | None,
    folder_name: str | None,
    source_config: dict[str, Any] | None = None,
    subscription_display_name: str | None | object = _USE_SOURCE_DISPLAY_NAME,
    commit: bool = True,
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

    needs_web_rule = web_source_needs_rule(source)
    needs_feed_probe = source.kind == SourceKind.WEB and web_feed_probe_required(
        source.canonical_url, source.config
    )
    sync_state = await session.get(SourceSyncState, source.id, with_for_update=True)
    if sync_state is None:
        is_reddit = identity.kind == SourceKind.REDDIT
        await session.execute(
            pg_insert(SourceSyncState)
            .values(
                source_id=source.id,
                phase=SyncPhase.DEGRADED if needs_web_rule else SyncPhase.IDLE,
                committed_checkpoint={},
                pending_checkpoint={},
                continuation=None,
                provider_mode="reddit_snapshot" if is_reddit else None,
                initial_sync_completed=is_reddit,
                next_scan_at=None if is_reddit or needs_web_rule else datetime.now(UTC),
                last_error_code=(
                    "web_rule_required"
                    if needs_web_rule
                    else "web_feed_discovering"
                    if needs_feed_probe
                    else None
                ),
                last_error_message="Web source needs an accepted native rule."
                if needs_web_rule
                else "Checking the website for a usable RSS or Atom feed."
                if needs_feed_probe
                else None,
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
            )
            .on_conflict_do_nothing(
                index_elements=[SourceSubscription.user_id, SourceSubscription.source_id]
            )
            .returning(SourceSubscription.id)
        )
        subscription = await _find_subscription(session, user_id, source.id)
        if subscription is None:
            raise RuntimeError("Source subscription upsert did not return the stored row.")
        if (
            inserted_subscription_id is not None
            and identity.kind != SourceKind.REDDIT
            and not needs_web_rule
        ):
            sync_state.next_scan_at = datetime.now(UTC)

    elif not subscription.is_enabled:
        subscription.is_enabled = True
        if identity.kind != SourceKind.REDDIT and not needs_web_rule:
            sync_state.next_scan_at = datetime.now(UTC)

    if needs_feed_probe:
        if sync_state.next_scan_at is None:
            sync_state.next_scan_at = datetime.now(UTC)
        if getattr(sync_state, "last_error_code", None) is None or str(
            sync_state.last_error_code
        ).startswith("web_rule_"):
            sync_state.last_error_code = "web_feed_discovering"
            sync_state.last_error_message = "Checking the website for a usable RSS or Atom feed."
            if getattr(sync_state, "phase", None) == SyncPhase.DEGRADED:
                sync_state.phase = SyncPhase.IDLE
    elif needs_web_rule:
        sync_state.phase = SyncPhase.DEGRADED
        sync_state.next_scan_at = None
        sync_state.last_error_code = "web_rule_required"
        sync_state.last_error_message = "Web source needs an accepted native rule."
        await session.flush()
        await ensure_web_rule_job(session, source=source, state=sync_state, reason="author")

    if commit:
        await session.commit()
        await session.refresh(source)
        await session.refresh(subscription)
        await session.refresh(sync_state)
    else:
        await session.flush()
    return source, subscription, sync_state, created_source


async def _find_source(session: AsyncSession, canonical_key: str) -> FeedSource | None:
    return await session.scalar(
        select(FeedSource).where(FeedSource.canonical_key == canonical_key).with_for_update()
    )


def web_source_needs_rule(source: FeedSource) -> bool:
    if source.kind != SourceKind.WEB:
        return False
    if configured_web_feed_url(source.canonical_url, source.config) or web_feed_probe_required(
        source.canonical_url, source.config
    ):
        return False
    try:
        parse_web_rule((source.config or {}).get("web_rule"), source_url=source.canonical_url)
    except WebRuleError:
        return True
    return False


async def _find_subscription(
    session: AsyncSession, user_id: UUID, source_id: UUID
) -> SourceSubscription | None:
    return await session.scalar(
        select(SourceSubscription).where(
            SourceSubscription.user_id == user_id,
            SourceSubscription.source_id == source_id,
        )
    )
