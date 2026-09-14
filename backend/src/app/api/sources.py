"""Endpoints for a user's source subscriptions."""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_current_user
from app.core.settings import get_settings
from app.domain.enums import SourceKind, SourceStatus, SyncPhase
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
from app.ingestion.web_feed import configured_web_feed_url, web_feed_probe_required
from app.ingestion.youtube import resolve_youtube_channel_id
from app.services.ranking_snapshots import ensure_reddit_snapshots
from app.services.subscriptions import (
    SourceSubscriptionLimitExceeded,
    enforce_reddit_subscription_limit,
    subscribe_to_shared_source,
    web_source_needs_rule,
)
from app.storage.database import get_session
from app.storage.models import (
    FeedSource,
    SourceSubscription,
    SourceSyncState,
    WebRuleAgentRuntime,
    WebRuleJob,
)
from app.translation.engines import engine_descriptor
from app.web_rule_agent.configuration import rule_agent_snapshot

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

    source, subscription, sync_state, created_source = await subscribe_to_shared_source(
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

    source, subscription, sync_state, created_source = await subscribe_to_shared_source(
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
    # The canonical key is the authority for every downstream identity. Do not
    # re-parse raw user input after canonicalisation or Snapshot keys diverge.
    subreddit = identity.canonical_key.removeprefix("reddit:")
    try:
        await enforce_reddit_subscription_limit(
            session,
            user_id=current_user.id,
            canonical_key=identity.canonical_key,
            limit=get_settings().reddit_subscription_limit_per_user,
        )
        source, subscription, sync_state, created_source = await subscribe_to_shared_source(
            session,
            user_id=current_user.id,
            identity=identity,
            display_name=f"r/{subreddit}",
            folder_name=_normalise_folder_name(payload.folder_name),
            source_config={"subreddit": subreddit},
            commit=False,
        )
        await ensure_reddit_snapshots(session, subreddit, commit=False)
        await session.commit()
        await session.refresh(source)
        await session.refresh(subscription)
        await session.refresh(sync_state)
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
    source, subscription, sync_state, created_source = await subscribe_to_shared_source(
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
    source, subscription, sync_state, created_source = await subscribe_to_shared_source(
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
    result = await session.execute(
        select(SourceSubscription, FeedSource, SourceSyncState)
        .join(FeedSource, SourceSubscription.source_id == FeedSource.id)
        .join(SourceSyncState, SourceSyncState.source_id == FeedSource.id)
        .where(SourceSubscription.user_id == current_user.id)
        .order_by(SourceSubscription.created_at.desc())
    )
    rows = list(result)
    projections = await _web_rule_health_projections(
        session, [(source, state) for _, source, state in rows]
    )
    return [
        SourceListItemResponse(
            source_id=str(source.id),
            subscription_id=str(subscription.id),
            kind=source.kind,
            canonical_url=source.canonical_url,
            display_name=subscription.custom_name or source.display_name,
            avatar_url=source.avatar_url,
            folder_name=subscription.folder_name,
            status=source.status,
            sync_phase=projections.get(source.id, (sync_state.phase, None))[0],
            last_complete_at=sync_state.last_complete_at,
            next_scan_at=sync_state.next_scan_at,
            gap_detected=sync_state.gap_detected,
            backlog_cycles=sync_state.consecutive_limit_runs,
            last_error_code=projections.get(source.id, (None, sync_state.last_error_code))[1],
        )
        for subscription, source, sync_state in rows
    ]


async def _web_rule_health_projections(
    session, sources
) -> dict[UUID, tuple[SyncPhase, str | None]]:
    """Load only current input/episode jobs once, without changing scan evidence."""
    feed_sources = {
        source.id: (source, state)
        for source, state in sources
        if source.kind == SourceKind.WEB
        and source.status != SourceStatus.PAUSED
        and (
            configured_web_feed_url(source.canonical_url, source.config)
            or web_feed_probe_required(source.canonical_url, source.config)
        )
    }
    projections = {
        source_id: _project_web_rule_health(source, state, None, agent_unavailable=False)
        for source_id, (source, state) in feed_sources.items()
    }
    waiting = [
        (source, state)
        for source, state in sources
        if source.kind == SourceKind.WEB
        and source.status != SourceStatus.PAUSED
        and source.id not in feed_sources
        and (web_source_needs_rule(source) or state.web_rule_failure_episode_id is not None)
    ]
    if not waiting:
        return projections
    conditions = []
    for source, state in waiting:
        missing = web_source_needs_rule(source)
        conditions.append(
            and_(
                WebRuleJob.source_id == source.id,
                WebRuleJob.base_rule_revision
                == int((source.config or {}).get("web_rule_revision") or 0),
                WebRuleJob.reason == ("author" if missing else "repair"),
                WebRuleJob.failure_episode_id.is_(None)
                if missing
                else (WebRuleJob.failure_episode_id == state.web_rule_failure_episode_id),
            )
        )
    jobs = list(
        await session.scalars(
            select(WebRuleJob)
            .where(or_(*conditions))
            .distinct(WebRuleJob.source_id)
            .order_by(WebRuleJob.source_id, WebRuleJob.created_at.desc(), WebRuleJob.id.desc())
        )
    )
    by_source = {job.source_id: job for job in jobs}
    runtime = await session.get(WebRuleAgentRuntime, "default")
    settings = get_settings()
    engine_id = settings.web_rule_agent_engine_id
    descriptor = engine_descriptor(settings, engine_id) if engine_id != "disabled" else None
    unavailable = (
        descriptor is None
        or not descriptor.available
        or bool(
            runtime is not None
            and runtime.paused_code
            and (runtime.resume_at is None or runtime.resume_at > datetime.now(UTC))
            and runtime.engine_snapshot == rule_agent_snapshot(settings, descriptor)
        )
    )
    projections.update(
        {
            source.id: _project_web_rule_health(
                source,
                state,
                by_source.get(source.id),
                agent_unavailable=unavailable,
            )
            for source, state in waiting
        }
    )
    return projections


def _project_web_rule_health(source, state, job, *, agent_unavailable: bool):
    baseline = (state.phase, state.last_error_code)
    if source.status == SourceStatus.PAUSED or source.kind != SourceKind.WEB:
        return baseline
    if configured_web_feed_url(source.canonical_url, source.config):
        if state.last_error_code == "web_feed_discovering" or str(
            state.last_error_code or ""
        ).startswith("web_rule_"):
            return (
                SyncPhase.IDLE if state.phase == SyncPhase.DEGRADED else state.phase,
                None,
            )
        return baseline
    if web_feed_probe_required(source.canonical_url, source.config):
        if state.last_error_code is None or str(state.last_error_code).startswith("web_rule_"):
            return state.phase, "web_feed_discovering"
        return baseline
    missing = web_source_needs_rule(source)
    episode = state.web_rule_failure_episode_id
    if not missing and episode is None:
        return baseline
    # Keep the pure projection fenced too: callers cannot project a historical
    # task merely because it was once the most recent job for this source.
    if job is not None and (
        job.base_rule_revision != int((source.config or {}).get("web_rule_revision") or 0)
        or job.reason != ("author" if missing else "repair")
        or job.failure_episode_id != (None if missing else episode)
    ):
        job = None
    access_error_codes = {
        "web_http_401",
        "web_http_403",
        "web_challenge_required",
        "robots_disallowed",
    }
    if state.last_error_code in access_error_codes:
        # A later scan can be denied while a previous structural repair episode
        # is still open. Its latest access failure takes priority over repair UI.
        return baseline
    if (
        job is not None
        and job.status == "failed"
        and getattr(job, "last_error_code", None) in access_error_codes
    ):
        # An unavailable authoring engine cannot explain or repair an upstream
        # access restriction. Keep the actual current task's cause visible.
        return SyncPhase.DEGRADED, job.last_error_code
    if agent_unavailable:
        return SyncPhase.DEGRADED, "web_rule_agent_unavailable"
    if job is None:
        return (SyncPhase.DEGRADED, "web_rule_authoring") if missing else baseline
    if job.status in {"queued", "running", "retry_wait"}:
        return SyncPhase.DEGRADED, "web_rule_authoring" if missing else "web_rule_repairing"
    if job.status == "blocked":
        return SyncPhase.DEGRADED, "web_rule_agent_unavailable"
    if job.status == "failed":
        return SyncPhase.DEGRADED, "web_rule_authoring_failed"
    return baseline


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
    """Read a bounded upstream body without materialising an unlimited response."""
    content = bytearray()
    async for chunk in upstream.aiter_raw(chunk_size=64 * 1024):
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
    subscription.folder_name = _normalise_folder_name(payload.folder_name)
    await session.commit()
    await session.refresh(subscription)
    source = await session.get(FeedSource, subscription.source_id)
    if source is None:  # Defensive; the foreign key normally makes this impossible.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    sync_state = await session.get(SourceSyncState, source.id)
    if sync_state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync state not found.")
    projections = await _web_rule_health_projections(session, [(source, sync_state)])
    phase, error_code = projections.get(source.id, (sync_state.phase, sync_state.last_error_code))
    return SourceListItemResponse(
        source_id=str(source.id),
        subscription_id=str(subscription.id),
        kind=source.kind,
        canonical_url=source.canonical_url,
        display_name=subscription.custom_name or source.display_name,
        avatar_url=source.avatar_url,
        folder_name=subscription.folder_name,
        status=source.status,
        sync_phase=phase,
        last_complete_at=sync_state.last_complete_at,
        next_scan_at=sync_state.next_scan_at,
        gap_detected=sync_state.gap_detected,
        backlog_cycles=sync_state.consecutive_limit_runs,
        last_error_code=error_code,
    )


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
