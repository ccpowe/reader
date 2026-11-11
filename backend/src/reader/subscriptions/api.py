"""Subscription routes and the source icon relay."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from reader.accounts.auth import CurrentUser, current_user
from reader.core.db import get_session
from reader.core.errors import AppError, error_responses
from reader.core.server_access import document_server_token
from reader.core.settings import Settings, get_settings
from reader.subscriptions import service
from reader.subscriptions.icons import fetch_icon
from reader.subscriptions.service import SubscriptionType, SubscriptionView

router = APIRouter(
    prefix="/subscriptions", tags=["subscriptions"], dependencies=[Depends(document_server_token)]
)
sources_router = APIRouter(
    prefix="/sources", tags=["subscriptions"], dependencies=[Depends(document_server_token)]
)


def _trim(value: str | None) -> str | None:
    return (value.strip() or None) if value is not None else None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubscribeRequest(Strict):
    type: SubscriptionType
    # A feed or website URL, a YouTube channel URL / @handle / ID, an X handle, or a subreddit.
    value: str = Field(min_length=1, max_length=4096)
    group: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=300)
    _trimmed = field_validator("group", "name")(_trim)


class UpdateSubscriptionRequest(Strict):
    """Send only the fields to change; `null` clears group or name."""

    group: str | None = Field(default=None, max_length=120)
    name: str | None = Field(default=None, max_length=300)
    include_in_home: bool | None = None
    is_enabled: bool | None = None
    _trimmed = field_validator("group", "name")(_trim)


class MarkViewedRequest(Strict):
    # The `latest_sequence` the client last loaded.
    sequence: int = Field(ge=0)


class SubscriptionResponse(BaseModel):
    id: str
    source_id: str
    type: str
    name: str | None
    url: str
    icon_url: str | None
    group: str | None
    include_in_home: bool
    is_enabled: bool
    new_count: int
    latest_sequence: int
    status: str
    last_success_at: datetime | None
    next_scan_at: datetime | None
    last_error_code: str | None
    may_have_gap: bool
    created_at: datetime


def _response(view: SubscriptionView, request: Request, settings: Settings) -> SubscriptionResponse:
    subscription, source = view.subscription, view.source
    icon_url = None
    if source.icon_url:
        base = str(settings.public_api_base_url or request.base_url).rstrip("/")
        icon_url = f"{base}/sources/{source.id}/icon"
    return SubscriptionResponse(
        id=str(subscription.id),
        source_id=str(source.id),
        type=source.kind,
        name=subscription.custom_name or source.display_name,
        url=source.url,
        icon_url=icon_url,
        group=subscription.group_name,
        include_in_home=subscription.include_in_home,
        is_enabled=subscription.is_enabled,
        new_count=view.new_count,
        latest_sequence=source.latest_sequence,
        status=source.status,
        last_success_at=source.last_success_at,
        next_scan_at=source.next_scan_at,
        last_error_code=source.last_error_code,
        may_have_gap=source.may_have_gap,
        created_at=subscription.created_at,
    )


_NOT_FOUND = (404, "subscription_not_found")


@router.get("", response_model=list[SubscriptionResponse], responses=error_responses())
async def list_subscriptions(
    request: Request,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    views = await service.list_subscriptions(db, user.id)
    return [_response(view, request, settings) for view in views]


@router.post(
    "",
    response_model=SubscriptionResponse,
    status_code=201,
    responses=error_responses(
        (422, "invalid_rss"),
        (422, "invalid_website"),
        (422, "invalid_youtube"),
        (422, "invalid_x"),
        (422, "invalid_reddit"),
        (422, "unsafe_url"),
        (422, "feed_not_found"),
        (502, "source_unreachable"),
    ),
)
async def subscribe(
    body: SubscribeRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    """Follow a source. Following the same source again returns the existing subscription."""
    resolved = await service.resolve_source(body.type, body.value)
    view = await service.subscribe(db, user.id, resolved, group=body.group, custom_name=body.name)
    return _response(view, request, settings)


@router.patch(
    "/{subscription_id}",
    response_model=SubscriptionResponse,
    responses=error_responses(_NOT_FOUND),
)
async def update_subscription(
    subscription_id: UUID,
    body: UpdateSubscriptionRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    changes: dict = {}
    if "group" in body.model_fields_set:
        changes["group"] = body.group
    if "name" in body.model_fields_set:
        changes["custom_name"] = body.name
    view = await service.update_subscription(
        db,
        user.id,
        subscription_id,
        include_in_home=body.include_in_home,
        is_enabled=body.is_enabled,
        **changes,
    )
    return _response(view, request, settings)


@router.delete("/{subscription_id}", status_code=204, responses=error_responses(_NOT_FOUND))
async def unsubscribe(
    subscription_id: UUID,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await service.unsubscribe(db, user.id, subscription_id)
    return Response(status_code=204)


@router.post(
    "/{subscription_id}/viewed",
    response_model=SubscriptionResponse,
    responses=error_responses(_NOT_FOUND),
)
async def mark_viewed(
    subscription_id: UUID,
    body: MarkViewedRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    """Clear the new-content badge up to the sequence the client has shown."""
    view = await service.mark_viewed(db, user.id, subscription_id, body.sequence)
    return _response(view, request, settings)


@sources_router.get(
    "/{source_id}/icon",
    response_class=Response,
    responses={
        200: {"content": {"image/*": {}}},
        **error_responses((404, "icon_not_found"), (502, "icon_unavailable")),
    },
)
async def source_icon(
    source_id: UUID,
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """The icon of a source you follow, relayed so the app never contacts its host."""
    url = await service.followed_source_icon(db, user.id, source_id)
    if url is None:
        raise AppError(404, "icon_not_found", "This source has no icon.")
    # Authorization is done; release the database connection before the upstream fetch.
    await db.rollback()
    icon = await fetch_icon(source_id, url)
    etag = f'"{sha256(icon.content).hexdigest()}"'
    headers = {"Cache-Control": "private, max-age=86400", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=icon.content, media_type=icon.content_type, headers=headers)
