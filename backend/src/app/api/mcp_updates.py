"""Authenticated, read-only subscription updates for the local MCP bridge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AUTH_ALGORITHM, AuthenticatedUser, require_current_user, signing_secret
from app.core.settings import get_settings
from app.services.mcp_updates import subscription_summary, subscription_updates
from app.storage.database import get_session

router = APIRouter(prefix="/mcp", tags=["mcp"])
_CURSOR_TYPE = "subscription-updates"
_CURSOR_LIFETIME = timedelta(days=1)


class SubscriptionUpdateCount(BaseModel):
    subscription_id: str
    source_id: str
    source_name: str | None
    source_kind: str
    count: int


class UpdateGroup(BaseModel):
    name: str | None
    total: int
    subscriptions: list[SubscriptionUpdateCount]


class UpdatesSummaryResponse(BaseModel):
    start_time: datetime
    end_time: datetime
    total: int
    groups: list[UpdateGroup]


class SubscriptionUpdateItem(BaseModel):
    content_id: str
    source_id: str
    title: str
    external_url: str
    content_type: str
    published_at: datetime | None
    fetched_at: datetime
    effective_at: datetime


class SubscriptionUpdatesResponse(BaseModel):
    subscription_id: str
    total: int
    items: list[SubscriptionUpdateItem]
    next_cursor: str | None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail="Time bounds must include a timezone.")
    return value.astimezone(UTC)


def _window(start_time: datetime, end_time: datetime) -> tuple[datetime, datetime]:
    start, end = _aware_utc(start_time), _aware_utc(end_time)
    if start >= end:
        raise HTTPException(status_code=422, detail="start_time must precede end_time.")
    return start, end


def _encode_cursor(
    *,
    user_id: UUID,
    subscription_id: UUID,
    start: datetime,
    end: datetime,
    cutoff: datetime,
    at: datetime,
    entry_id: UUID,
) -> str:
    return jwt.encode(
        {
            "typ": _CURSOR_TYPE,
            "sub": str(user_id),
            "subscription": str(subscription_id),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "cutoff": cutoff.isoformat(),
            "at": at.isoformat(),
            "entry": str(entry_id),
            "exp": datetime.now(UTC) + _CURSOR_LIFETIME,
        },
        signing_secret(get_settings()),
        algorithm=AUTH_ALGORITHM,
    )


def _decode_cursor(
    value: str,
    *,
    user_id: UUID,
    subscription_id: UUID,
    start: datetime,
    end: datetime,
) -> tuple[datetime, tuple[datetime, UUID]]:
    try:
        payload = jwt.decode(
            value,
            signing_secret(get_settings()),
            algorithms=[AUTH_ALGORITHM],
            options={
                "require": [
                    "exp",
                    "typ",
                    "sub",
                    "subscription",
                    "start",
                    "end",
                    "cutoff",
                    "at",
                    "entry",
                ]
            },
        )
        if (
            payload["typ"] != _CURSOR_TYPE
            or payload["sub"] != str(user_id)
            or payload["subscription"] != str(subscription_id)
            or payload["start"] != start.isoformat()
            or payload["end"] != end.isoformat()
        ):
            raise ValueError("cursor scope mismatch")
        cutoff = datetime.fromisoformat(payload["cutoff"])
        at = datetime.fromisoformat(payload["at"])
        entry_id = UUID(payload["entry"])
        if (
            cutoff.tzinfo is None
            or cutoff.utcoffset() is None
            or at.tzinfo is None
            or at.utcoffset() is None
        ):
            raise ValueError("naive cursor timestamp")
        return cutoff.astimezone(UTC), (at.astimezone(UTC), entry_id)
    except (jwt.PyJWTError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail="Invalid updates cursor.") from exc


@router.get("/updates/summary", response_model=UpdatesSummaryResponse)
async def get_updates_summary(
    start_time: datetime,
    end_time: datetime,
    group: str | None = Query(
        default=None,
        max_length=120,
        description="Omit for all groups; pass an empty string for ungrouped subscriptions.",
    ),
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> UpdatesSummaryResponse:
    """Read all enabled subscriptions in [start_time, end_time), including zero counts.

    Publication time falls back to first fetch. No read/translation state changes;
    invalid or naive time bounds return 422.
    """
    start, end = _window(start_time, end_time)
    counts = await subscription_summary(
        session,
        user_id=current_user.id,
        start_time=start,
        end_time=end,
        group=group.strip() if group is not None else None,
    )
    groups: dict[str | None, UpdateGroup] = {}
    total = 0
    for folder, count in counts:
        bucket = groups.setdefault(folder, UpdateGroup(name=folder, total=0, subscriptions=[]))
        bucket.subscriptions.append(
            SubscriptionUpdateCount(
                subscription_id=str(count.subscription_id),
                source_id=str(count.source_id),
                source_name=count.source_name,
                source_kind=count.source_kind,
                count=count.count,
            )
        )
        bucket.total += count.count
        total += count.count
    return UpdatesSummaryResponse(
        start_time=start, end_time=end, total=total, groups=list(groups.values())
    )


@router.get("/subscriptions/{subscription_id}/updates", response_model=SubscriptionUpdatesResponse)
async def list_subscription_updates(
    subscription_id: UUID,
    start_time: datetime,
    end_time: datetime,
    cursor: str | None = Query(default=None, max_length=1024),
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionUpdatesResponse:
    """Read 30 raw updates with a signed, account/window-bound continuation cursor.

    This endpoint never marks content read. Missing or disabled subscriptions
    return 404; invalid bounds or expired/mismatched cursors return 422.
    """
    start, end = _window(start_time, end_time)
    cutoff, after = (
        _decode_cursor(
            cursor,
            user_id=current_user.id,
            subscription_id=subscription_id,
            start=start,
            end=end,
        )
        if cursor is not None
        else (datetime.now(UTC), None)
    )
    page = await subscription_updates(
        session,
        user_id=current_user.id,
        subscription_id=subscription_id,
        start_time=start,
        end_time=end,
        fetched_cutoff=cutoff,
        after=after,
    )
    if page is None:
        raise HTTPException(status_code=404, detail="Subscription not found.")
    next_cursor = None
    if page.next_key is not None:
        at, entry_id = page.next_key
        next_cursor = _encode_cursor(
            user_id=current_user.id,
            subscription_id=subscription_id,
            start=start,
            end=end,
            cutoff=cutoff,
            at=at,
            entry_id=entry_id,
        )
    return SubscriptionUpdatesResponse(
        subscription_id=str(subscription_id),
        total=page.total,
        items=[
            SubscriptionUpdateItem(
                content_id=str(item.content_id),
                source_id=str(item.source_id),
                title=item.title,
                external_url=item.external_url,
                content_type=item.content_type,
                published_at=item.published_at,
                fetched_at=item.fetched_at,
                effective_at=item.effective_at,
            )
            for item in page.items
        ],
        next_cursor=next_cursor,
    )
