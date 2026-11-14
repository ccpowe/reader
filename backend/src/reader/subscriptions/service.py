"""Subscription use cases.

A source is shared by everyone who follows it; a subscription is one user's
link to it with their own name, group and "seen up to" sequence. Unfollowed
sources are kept (no longer scanned) so following again resumes where it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from reader.core.errors import AppError
from reader.ingestion.feed_discovery import discover_web_feed
from reader.ingestion.identity import (
    SourceIdentity,
    canonical_reddit_identity,
    canonical_rss_identity,
    canonical_x_identity,
    canonical_youtube_identity,
)
from reader.ingestion.url_safety import (
    PublicAsyncClient,
    UnresolvableHost,
    UnsafeSourceUrl,
    validate_public_http_url,
)
from reader.ingestion.youtube import resolve_youtube_channel_id
from reader.models import Article, Source, Subscription

SubscriptionType = Literal["rss", "website", "youtube", "x", "reddit"]


@dataclass(frozen=True)
class ResolvedSource:
    identity: SourceIdentity
    display_name: str | None
    config: dict[str, Any]


@dataclass(frozen=True)
class SubscriptionView:
    subscription: Subscription
    source: Source
    new_count: int


def _not_found() -> AppError:
    return AppError(404, "subscription_not_found", "No such subscription.")


async def resolve_source(kind: SubscriptionType, value: str) -> ResolvedSource:
    """Turn what the user typed into a canonical source; may contact the network."""
    try:
        if kind == "rss":
            identity = canonical_rss_identity(value.strip())
            await validate_public_http_url(identity.canonical_url)
            return ResolvedSource(identity, None, {})
        if kind == "website":
            return await _resolve_website(value.strip())
        if kind == "youtube":
            async with PublicAsyncClient() as client:
                channel_id = await resolve_youtube_channel_id(value, client)
            return ResolvedSource(
                canonical_youtube_identity(channel_id), None, {"channel_id": channel_id}
            )
        if kind == "x":
            identity = canonical_x_identity(value)  # validates the handle
            handle = value.strip().removeprefix("@").strip()
            return ResolvedSource(identity, f"@{handle}", {"handle": handle})
        identity = canonical_reddit_identity(value)
        subreddit = identity.canonical_url.rstrip("/").rsplit("/", 1)[-1]
        return ResolvedSource(identity, f"r/{subreddit}", {"subreddit": subreddit})
    except UnresolvableHost as exc:
        raise AppError(502, "source_unreachable", "The address could not be reached.") from exc
    except UnsafeSourceUrl as exc:
        raise AppError(422, "unsafe_url", str(exc)) from exc
    except ValueError as exc:
        raise AppError(422, f"invalid_{kind}", str(exc)) from exc


async def _resolve_website(url: str) -> ResolvedSource:
    """A website is subscribed through its RSS/Atom feed; Reader does not crawl pages."""
    await validate_public_http_url(url)
    async with PublicAsyncClient() as client:
        result = await discover_web_feed(url, client)
    if result.status in {"retry", "blocked"}:
        raise AppError(502, "source_unreachable", "The address could not be reached.")
    if result.status != "found" or result.feed_url is None:
        raise AppError(422, "feed_not_found", "This website has no RSS/Atom feed.")
    identity = canonical_rss_identity(result.feed_url)
    return ResolvedSource(identity, None, {"website": url})


async def subscribe(
    db: AsyncSession,
    user_id: UUID,
    resolved: ResolvedSource,
    *,
    group: str | None,
    custom_name: str | None,
) -> SubscriptionView:
    """Follow a source, creating it if nobody has yet. Following twice is harmless."""
    now = datetime.now(UTC)
    identity = resolved.identity
    await db.execute(
        pg_insert(Source)
        .values(
            kind=identity.kind,
            canonical_key=identity.canonical_key,
            url=identity.canonical_url,
            display_name=resolved.display_name,
            config=resolved.config,
            next_scan_at=now,
        )
        .on_conflict_do_nothing(index_elements=[Source.canonical_key])
    )
    source = await db.scalar(
        select(Source).where(Source.canonical_key == identity.canonical_key).with_for_update()
    )
    assert source is not None
    subscription = await db.scalar(
        select(Subscription).where(
            Subscription.user_id == user_id, Subscription.source_id == source.id
        )
    )
    if subscription is None:
        subscription = Subscription(
            user_id=user_id,
            source_id=source.id,
            group_name=group,
            custom_name=custom_name,
            # Only articles that arrive after following count as new.
            last_viewed_sequence=source.latest_sequence,
        )
        db.add(subscription)
        # A source nobody followed was not scanned; bring it up to date now.
        if source.lease_token is None:
            source.next_scan_at = min(source.next_scan_at or now, now)
    elif not subscription.is_enabled:
        subscription.is_enabled = True
        source.next_scan_at = min(source.next_scan_at or now, now)
    await db.commit()
    return await get_subscription(db, user_id, subscription.id)


def _new_count_column():
    return (
        select(func.count(Article.id))
        .where(
            Article.source_id == Subscription.source_id,
            Article.sequence > Subscription.last_viewed_sequence,
        )
        .correlate(Subscription)
        .scalar_subquery()
    )


async def list_subscriptions(db: AsyncSession, user_id: UUID) -> list[SubscriptionView]:
    rows = await db.execute(
        select(Subscription, Source, _new_count_column())
        .join(Source, Source.id == Subscription.source_id)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc())
    )
    return [
        SubscriptionView(subscription, source, int(count or 0))
        for subscription, source, count in rows
    ]


async def get_subscription(
    db: AsyncSession, user_id: UUID, subscription_id: UUID
) -> SubscriptionView:
    row = (
        await db.execute(
            select(Subscription, Source, _new_count_column())
            .join(Source, Source.id == Subscription.source_id)
            .where(Subscription.id == subscription_id, Subscription.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        raise _not_found()
    subscription, source, count = row
    return SubscriptionView(subscription, source, int(count or 0))


_UNSET: Any = object()


async def update_subscription(
    db: AsyncSession,
    user_id: UUID,
    subscription_id: UUID,
    *,
    group: str | None = _UNSET,
    custom_name: str | None = _UNSET,
    include_in_home: bool | None = None,
    is_enabled: bool | None = None,
) -> SubscriptionView:
    """Change only the fields given; `None` for group or name clears it."""
    subscription = await _owned(db, user_id, subscription_id)
    if group is not _UNSET:
        subscription.group_name = group
    if custom_name is not _UNSET:
        subscription.custom_name = custom_name
    if include_in_home is not None:
        subscription.include_in_home = include_in_home
    if is_enabled is not None:
        subscription.is_enabled = is_enabled
    await db.commit()
    return await get_subscription(db, user_id, subscription_id)


async def unsubscribe(db: AsyncSession, user_id: UUID, subscription_id: UUID) -> None:
    subscription = await _owned(db, user_id, subscription_id)
    await db.delete(subscription)
    await db.commit()


async def mark_viewed(
    db: AsyncSession, user_id: UUID, subscription_id: UUID, sequence: int
) -> SubscriptionView:
    """Record "seen up to `sequence`": never past the source's latest, never backwards."""
    subscription = await _owned(db, user_id, subscription_id)
    latest = await db.scalar(
        select(Source.latest_sequence).where(Source.id == subscription.source_id)
    )
    subscription.last_viewed_sequence = max(
        subscription.last_viewed_sequence, min(sequence, int(latest or 0))
    )
    await db.commit()
    return await get_subscription(db, user_id, subscription_id)


async def _owned(db: AsyncSession, user_id: UUID, subscription_id: UUID) -> Subscription:
    subscription = await db.scalar(
        select(Subscription)
        .where(Subscription.id == subscription_id, Subscription.user_id == user_id)
        .with_for_update()
    )
    if subscription is None:
        raise _not_found()
    return subscription


async def followed_source_icon(db: AsyncSession, source_id: UUID) -> str | None:
    """The icon URL of a source someone on this server follows, or None."""
    return await db.scalar(
        select(Source.icon_url).where(
            Source.id == source_id,
            exists().where(Subscription.source_id == Source.id),
        )
    )
