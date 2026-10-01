"""Endpoints for a user's source subscriptions."""

from __future__ import annotations

import base64
import binascii
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from time import monotonic
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_current_user
from app.core.settings import get_settings
from app.domain.enums import SourceStatus, SyncPhase
from app.ingestion.source_identity import (
    MAX_RAW_SUBREDDIT_LENGTH,
    canonical_reddit_identity,
    canonical_rss_identity,
    canonical_web_identity,
    canonical_x_identity,
    canonical_youtube_identity,
)
from app.ingestion.url_safety import (
    PublicAsyncClient,
    UnsafeSourceUrl,
    safe_stream_get,
    validate_public_http_url,
)
from app.ingestion.youtube import resolve_youtube_channel_id
from app.services.source_health import web_rule_health_projections
from app.services.subscriptions import (
    SourceSubscriptionLimitExceeded,
    subscribe_to_reddit_source,
    subscribe_to_source,
)
from app.storage.database import get_session
from app.storage.models import (
    FeedSource,
    SourceEntry,
    SourceSubscription,
    SourceSyncState,
)

router = APIRouter(prefix="/sources", tags=["sources"])
logger = logging.getLogger(__name__)
_MAX_AVATAR_BYTES = 2 * 1024 * 1024
_AVATAR_CACHE_TTL_SECONDS = 24 * 60 * 60
_AVATAR_CACHE_MAX_ENTRIES = 128
_ALLOWED_AVATAR_CONTENT_TYPES = frozenset(
    {
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/vnd.microsoft.icon",
        "image/webp",
        "image/x-icon",
    }
)


@dataclass(frozen=True)
class _AvatarCacheEntry:
    url: str
    content: bytes
    content_type: str
    expires_at: float


# This is deliberately process-local: avatars are public source metadata, and
# the cache is only an optimisation. Access is still checked before every hit.
_avatar_cache: OrderedDict[UUID, _AvatarCacheEntry] = OrderedDict()


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddRssSourceRequest(StrictRequest):
    url: str = Field(min_length=1, max_length=4096)
    display_name: str | None = Field(default=None, max_length=300)
    folder_name: str | None = Field(default=None, max_length=120)


class AddWebSourceRequest(AddRssSourceRequest):
    """A public blog index URL; automatically prefer a discovered RSS/Atom feed."""


class AddRedditSourceRequest(StrictRequest):
    # This bounds the raw transport value. Canonical identity validation owns
    # the actual 2-21 character community-name contract after trimming `r/`.
    subreddit: str = Field(min_length=2, max_length=MAX_RAW_SUBREDDIT_LENGTH)
    folder_name: str | None = Field(default=None, max_length=120)


class AddYouTubeSourceRequest(StrictRequest):
    # Accept a UC channel ID, @handle, or channel URL.  The endpoint resolves
    # and validates the exact channel ID before persisting a subscription.
    channel_id: str = Field(min_length=1, max_length=4096)
    folder_name: str | None = Field(default=None, max_length=120)


class AddXSourceRequest(StrictRequest):
    handle: str = Field(min_length=1, max_length=16)
    folder_name: str | None = Field(default=None, max_length=120)


class UpdateSourceSubscriptionRequest(StrictRequest):
    folder_name: str | None = Field(default=None, max_length=120)
    include_in_home: bool | None = None


class MarkSourceViewedRequest(StrictRequest):
    channel_update_token: str = Field(min_length=1, max_length=512)


class SourceSubscriptionResponse(BaseModel):
    source_id: str
    subscription_id: str
    canonical_url: str
    status: SourceStatus
    next_scan_at: datetime | None
    created_shared_source: bool


class SourceListItemResponse(BaseModel):
    source_id: str
    subscription_id: str
    kind: str
    canonical_url: str
    display_name: str | None
    avatar_url: str | None
    folder_name: str | None
    include_in_home: bool
    new_count: int
    status: SourceStatus
    sync_phase: SyncPhase
    last_complete_at: datetime | None
    next_scan_at: datetime | None
    gap_detected: bool
    backlog_cycles: int
    last_error_code: str | None = Field(
        description=(
            "Latest synchronization error or warning. web_rule_partial_parse means usable "
            "articles were found but some rows lacked a title or URL; "
            "web_rule_missing_fields means no usable articles were found."
        )
    )


@router.post("/rss", response_model=SourceSubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def add_rss_source(
    payload: AddRssSourceRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceSubscriptionResponse:
    """Subscribe the authenticated user to a public RSS/Atom feed."""
    logger.info("Source add requested: kind=rss user_id=%s", current_user.id)
    try:
        identity = canonical_rss_identity(payload.url)
        await validate_public_http_url(identity.canonical_url)
    except UnsafeSourceUrl as exc:
        logger.warning("Source add rejected: kind=rss user_id=%s reason=%s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    source, subscription, sync_state, created_source = await subscribe_to_source(
        session,
        user_id=current_user.id,
        identity=identity,
        display_name=payload.display_name,
        folder_name=_normalise_folder_name(payload.folder_name),
        source_config={},
    )
    logger.info(
        "Source add complete: kind=rss source_id=%s created_shared_source=%s",
        source.id,
        created_source,
    )
    return _to_response(source, subscription, sync_state, created_source)


@router.post("/web", response_model=SourceSubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def add_web_source(
    payload: AddWebSourceRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceSubscriptionResponse:
    """Subscribe to a website, probing RSS/Atom before authoring a crawl rule."""
    logger.info("Source add requested: kind=web user_id=%s", current_user.id)
    try:
        identity = canonical_web_identity(payload.url)
        await validate_public_http_url(identity.canonical_url)
    except UnsafeSourceUrl as exc:
        logger.warning("Source add rejected: kind=web user_id=%s reason=%s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    source, subscription, sync_state, created_source = await subscribe_to_source(
        session,
        user_id=current_user.id,
        identity=identity,
        display_name=payload.display_name,
        folder_name=_normalise_folder_name(payload.folder_name),
        source_config={},
    )
    logger.info(
        "Source add complete: kind=web source_id=%s created_shared_source=%s",
        source.id,
        created_source,
    )
    return _to_response(source, subscription, sync_state, created_source)


@router.post(
    "/reddit", response_model=SourceSubscriptionResponse, status_code=status.HTTP_201_CREATED
)
async def add_reddit_source(
    payload: AddRedditSourceRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceSubscriptionResponse:
    """Subscribe to one subreddit; ranking views remain shared snapshots."""
    try:
        identity = canonical_reddit_identity(payload.subreddit)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    try:
        source, subscription, sync_state, created_source = await subscribe_to_reddit_source(
            session,
            user_id=current_user.id,
            identity=identity,
            folder_name=_normalise_folder_name(payload.folder_name),
            limit=get_settings().reddit_subscription_limit_per_user,
        )
    except SourceSubscriptionLimitExceeded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_response(source, subscription, sync_state, created_source)


@router.post(
    "/youtube", response_model=SourceSubscriptionResponse, status_code=status.HTTP_201_CREATED
)
async def add_youtube_source(
    payload: AddYouTubeSourceRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceSubscriptionResponse:
    """Subscribe to a YouTube channel resolved to its stable channel ID."""
    try:
        async with PublicAsyncClient() as client:
            channel_id = await resolve_youtube_channel_id(payload.channel_id, client)
        identity = canonical_youtube_identity(channel_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    source, subscription, sync_state, created_source = await subscribe_to_source(
        session,
        user_id=current_user.id,
        identity=identity,
        display_name=f"YouTube · {channel_id}",
        folder_name=_normalise_folder_name(payload.folder_name),
        source_config={"channel_id": channel_id},
        subscription_display_name=None,
    )
    return _to_response(source, subscription, sync_state, created_source)


@router.post("/x", response_model=SourceSubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def add_x_source(
    payload: AddXSourceRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceSubscriptionResponse:
    """Subscribe to an explicitly named public X account."""
    try:
        identity = canonical_x_identity(payload.handle)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    handle = payload.handle.removeprefix("@").strip()
    source, subscription, sync_state, created_source = await subscribe_to_source(
        session,
        user_id=current_user.id,
        identity=identity,
        display_name=f"@{handle}",
        folder_name=_normalise_folder_name(payload.folder_name),
        source_config={"handle": handle},
    )
    return _to_response(source, subscription, sync_state, created_source)


def _to_response(
    source: FeedSource,
    subscription: SourceSubscription,
    sync_state: SourceSyncState,
    created_source: bool,
) -> SourceSubscriptionResponse:
    return SourceSubscriptionResponse(
        source_id=str(source.id),
        subscription_id=str(subscription.id),
        canonical_url=source.canonical_url,
        status=source.status,
        next_scan_at=sync_state.next_scan_at,
        created_shared_source=created_source,
    )


def _normalise_folder_name(folder_name: str | None) -> str | None:
    if folder_name is None:
        return None
    return folder_name.strip() or None


@router.get("", response_model=list[SourceListItemResponse])
async def list_sources(
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SourceListItemResponse]:
    """List the caller's subscriptions, including shared source sync health."""
    new_count = (
        select(func.count(SourceEntry.id))
        .where(
            SourceEntry.source_id == SourceSubscription.source_id,
            SourceEntry.update_sequence.is_not(None),
            SourceEntry.update_sequence > SourceSubscription.last_viewed_update_sequence,
        )
        .correlate(SourceSubscription)
        .scalar_subquery()
    )
    result = await session.execute(
        select(SourceSubscription, FeedSource, SourceSyncState, new_count)
        .join(FeedSource, SourceSubscription.source_id == FeedSource.id)
        .join(SourceSyncState, SourceSyncState.source_id == FeedSource.id)
        .where(SourceSubscription.user_id == current_user.id)
        .order_by(SourceSubscription.created_at.desc())
    )
    rows = list(result)
    projections = await web_rule_health_projections(
        session, [(source, state) for _, source, state, _ in rows]
    )
    return [
        _source_list_item_response(
            subscription,
            source,
            sync_state,
            new_count=int(count or 0),
            projection=projections.get(source.id),
        )
        for subscription, source, sync_state, count in rows
    ]


def _source_list_item_response(
    subscription: SourceSubscription,
    source: FeedSource,
    sync_state: SourceSyncState,
    *,
    new_count: int,
    projection: tuple[SyncPhase, str | None] | None = None,
) -> SourceListItemResponse:
    phase, error_code = projection or (sync_state.phase, sync_state.last_error_code)
    return SourceListItemResponse(
        source_id=str(source.id),
        subscription_id=str(subscription.id),
        kind=source.kind,
        canonical_url=source.canonical_url,
        display_name=subscription.custom_name or source.display_name,
        avatar_url=source.avatar_url,
        folder_name=subscription.folder_name,
        include_in_home=subscription.include_in_home,
        new_count=new_count,
        status=source.status,
        sync_phase=phase,
        last_complete_at=sync_state.last_complete_at,
        next_scan_at=sync_state.next_scan_at,
        gap_detected=sync_state.gap_detected,
        backlog_cycles=sync_state.consecutive_limit_runs,
        last_error_code=error_code,
    )


@router.get("/{source_id}/avatar")
async def proxy_source_avatar(
    source_id: UUID,
    request: Request,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Serve a subscribed source's icon without exposing its upstream origin."""
    source = await session.scalar(
        select(FeedSource)
        .join(SourceSubscription, SourceSubscription.source_id == FeedSource.id)
        .where(
            FeedSource.id == source_id,
            SourceSubscription.user_id == current_user.id,
        )
    )
    if source is None or not source.avatar_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Avatar not found.")

    avatar_url = source.avatar_url
    # Authorization is complete. Release the pool connection before external I/O.
    await session.rollback()

    cached = _avatar_cache.get(source_id)
    if cached is not None and cached.url == avatar_url and cached.expires_at > monotonic():
        _avatar_cache.move_to_end(source_id)
        return _avatar_response(cached.content, cached.content_type, request)

    # Remove stale entries (including an entry for a source whose avatar URL changed).
    _avatar_cache.pop(source_id, None)
    try:
        async with PublicAsyncClient(follow_redirects=True, timeout=15.0) as client:
            async with safe_stream_get(client, avatar_url, timeout=15.0) as upstream:
                if upstream.status_code != status.HTTP_200_OK:
                    raise _AvatarUnavailableError
                content_type = _safe_avatar_content_type(upstream.headers.get("content-type"))
                declared_length = _content_length(upstream.headers.get("content-length"))
                if (
                    content_type is None
                    or declared_length is None
                    or declared_length > _MAX_AVATAR_BYTES
                ):
                    raise _AvatarUnavailableError
                content = await _read_avatar_bytes(upstream)
    except (httpx.HTTPError, UnsafeSourceUrl) as exc:
        logger.info("Avatar proxy failed: source_id=%s error=%s", source_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Avatar is unavailable."
        ) from exc
    except _AvatarUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Avatar is unavailable."
        ) from exc

    _avatar_cache[source_id] = _AvatarCacheEntry(
        url=avatar_url,
        content=content,
        content_type=content_type,
        expires_at=monotonic() + _AVATAR_CACHE_TTL_SECONDS,
    )
    _avatar_cache.move_to_end(source_id)
    while len(_avatar_cache) > _AVATAR_CACHE_MAX_ENTRIES:
        _avatar_cache.popitem(last=False)
    return _avatar_response(content, content_type, request)


class _AvatarUnavailableError(Exception):
    """An upstream avatar response cannot safely be relayed."""


def _safe_avatar_content_type(value: str | None) -> str | None:
    """Accept only browser-safe raster image media types."""
    if value is None:
        return None
    content_type = value.split(";", 1)[0].strip().lower()
    return content_type if content_type in _ALLOWED_AVATAR_CONTENT_TYPES else None


def _content_length(value: str | None) -> int | None:
    """Parse Content-Length, treating malformed values as unsafe."""
    if value is None:
        return 0
    try:
        length = int(value)
    except ValueError:
        return None
    return length if length >= 0 else None


async def _read_avatar_bytes(upstream: httpx.Response) -> bytes:
    """Bound the decoded image body, not its compressed transfer representation."""
    content = bytearray()
    async for chunk in upstream.aiter_bytes(chunk_size=64 * 1024):
        if len(content) + len(chunk) > _MAX_AVATAR_BYTES:
            raise _AvatarUnavailableError
        content.extend(chunk)
    return bytes(content)


def _avatar_response(content: bytes, content_type: str, request: Request) -> Response:
    """Build a private cacheable response, including a stable validator."""
    etag = f'"{sha256(content).hexdigest()}"'
    headers = {
        "Cache-Control": "private, max-age=86400",
        "ETag": etag,
        "Vary": "Authorization",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=content, media_type=content_type, headers=headers)


@router.patch("/{subscription_id}", response_model=SourceListItemResponse)
async def update_source_subscription(
    subscription_id: str,
    payload: UpdateSourceSubscriptionRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceListItemResponse:
    """Update personal subscription metadata without changing the shared source."""
    try:
        subscription_uuid = UUID(subscription_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found."
        ) from exc

    subscription = await session.scalar(
        select(SourceSubscription).where(
            SourceSubscription.id == subscription_uuid,
            SourceSubscription.user_id == current_user.id,
        )
    )
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found.")
    if "folder_name" in payload.model_fields_set:
        subscription.folder_name = _normalise_folder_name(payload.folder_name)
    if "include_in_home" in payload.model_fields_set:
        if payload.include_in_home is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="include_in_home must be true or false.",
            )
        subscription.include_in_home = payload.include_in_home
    await session.commit()
    return await _load_source_list_item(session, subscription.id, current_user.id)


@router.post("/{subscription_id}/viewed", response_model=SourceListItemResponse)
async def mark_source_viewed(
    subscription_id: str,
    payload: MarkSourceViewedRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceListItemResponse:
    """Acknowledge only the source update snapshot returned by a successful feed load."""
    try:
        subscription_uuid = UUID(subscription_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found."
        ) from exc
    token_source_id, token_sequence = _decode_channel_update_token(payload.channel_update_token)
    subscription = await session.scalar(
        select(SourceSubscription)
        .where(
            SourceSubscription.id == subscription_uuid,
            SourceSubscription.user_id == current_user.id,
        )
        .with_for_update()
    )
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found.")
    if subscription.source_id != token_source_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Channel update token does not match this source.",
        )
    latest_sequence = int(
        await session.scalar(
            select(FeedSource.latest_update_sequence).where(FeedSource.id == subscription.source_id)
        )
        or 0
    )
    if token_sequence > latest_sequence:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Channel update token is newer than the source.",
        )
    subscription.last_viewed_update_sequence = max(
        subscription.last_viewed_update_sequence, token_sequence
    )
    await session.commit()
    return await _load_source_list_item(session, subscription.id, current_user.id)


async def _load_source_list_item(
    session: AsyncSession,
    subscription_id: UUID,
    user_id: UUID,
) -> SourceListItemResponse:
    row = (
        await session.execute(
            select(
                SourceSubscription,
                FeedSource,
                SourceSyncState,
                func.count(SourceEntry.id).filter(
                    SourceEntry.update_sequence.is_not(None),
                    SourceEntry.update_sequence > SourceSubscription.last_viewed_update_sequence,
                ),
            )
            .join(FeedSource, FeedSource.id == SourceSubscription.source_id)
            .join(SourceSyncState, SourceSyncState.source_id == FeedSource.id)
            .outerjoin(SourceEntry, SourceEntry.source_id == FeedSource.id)
            .where(
                SourceSubscription.id == subscription_id,
                SourceSubscription.user_id == user_id,
            )
            .group_by(SourceSubscription, FeedSource, SourceSyncState)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found.")
    subscription, source, sync_state, new_count = row
    projections = await web_rule_health_projections(session, [(source, sync_state)])
    return _source_list_item_response(
        subscription,
        source,
        sync_state,
        new_count=int(new_count or 0),
        projection=projections.get(source.id),
    )


def _encode_channel_update_token(source_id: UUID, sequence: int) -> str:
    payload = json.dumps({"source_id": str(source_id), "sequence": sequence}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_channel_update_token(value: str) -> tuple[UUID, int]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("source_id"), str):
            raise ValueError("invalid source id")
        source_id = UUID(payload["source_id"])
        sequence = payload["sequence"]
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("invalid update sequence")
        return source_id, sequence
    except (
        binascii.Error,
        UnicodeDecodeError,
        KeyError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid channel update token.",
        ) from exc


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_source_subscription(
    subscription_id: str,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove only the caller's subscription; the shared source remains for other users."""
    try:
        subscription_uuid = UUID(subscription_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found."
        ) from exc

    result = await session.execute(
        delete(SourceSubscription).where(
            SourceSubscription.id == subscription_uuid,
            SourceSubscription.user_id == current_user.id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found.")
    await session.commit()
    logger.info(
        "Source subscription removed: subscription_id=%s user_id=%s",
        subscription_id,
        current_user.id,
    )
