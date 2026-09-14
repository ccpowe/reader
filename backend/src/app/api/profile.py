"""Authenticated profile read and update endpoints."""

from __future__ import annotations

from urllib.parse import urlparse
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.avatars import AVATAR_MAX_BYTES, AVATAR_TYPES, validate_avatar
from app.core.auth import AuthenticatedUser, require_current_user
from app.core.settings import Settings, get_settings
from app.storage.auth_models import ProfileAvatar
from app.storage.database import get_session
from app.storage.models import Profile
from app.storage.profiles import ensure_profile

router = APIRouter(prefix="/me", tags=["profile"])


class ProfileResponse(BaseModel):
    id: str
    email: str | None
    display_name: str | None
    avatar_url: str | None


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=2_048)

    @field_validator("display_name", mode="before")
    @classmethod
    def normalise_display_name(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalised = value.strip()
        return normalised or None

    @field_validator("avatar_url", mode="before")
    @classmethod
    def validate_avatar_url(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalised = value.strip()
        if not normalised:
            return None
        parsed = urlparse(normalised)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("avatar_url must be an absolute HTTP(S) URL")
        return normalised


def _to_response(
    profile: Profile,
    current_user: AuthenticatedUser,
    request: Request | None = None,
    settings: Settings | None = None,
) -> ProfileResponse:
    avatar_url = profile.avatar_url
    if avatar_url and avatar_url.startswith("/avatars/") and request is not None:
        base = (
            str(settings.public_api_base_url)
            if settings and settings.public_api_base_url
            else str(request.base_url)
        )
        avatar_url = base.rstrip("/") + avatar_url
    return ProfileResponse(
        id=str(profile.id),
        email=current_user.email,
        display_name=profile.display_name,
        avatar_url=avatar_url,
    )


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(
    request: Request,
    settings: Settings = Depends(get_settings),
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProfileResponse:
    profile = await session.get(Profile, current_user.id)
    if profile is None:
        await ensure_profile(session, current_user.id)
        await session.commit()
        profile = await session.get(Profile, current_user.id)
    if profile is None:
        # Registration or the idempotent upsert above always
        # creates this row. Keep a stable response if a legacy database delays
        # the insert until its next transaction.
        profile = Profile(id=current_user.id)
    return _to_response(profile, current_user, request, settings)


@router.patch("/profile", response_model=ProfileResponse)
async def update_profile(
    payload: UpdateProfileRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProfileResponse:
    await ensure_profile(session, current_user.id)
    profile = await session.get(Profile, current_user.id)
    if profile is None:
        profile = Profile(id=current_user.id)
        session.add(profile)
        await session.flush()

    if "display_name" in payload.model_fields_set:
        profile.display_name = payload.display_name
    if "avatar_url" in payload.model_fields_set:
        profile.avatar_url = payload.avatar_url
    await session.commit()
    return _to_response(profile, current_user, request, settings)


@router.put(
    "/profile/avatar",
    response_model=ProfileResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                mime: {"schema": {"type": "string", "format": "binary"}}
                for mime in AVATAR_TYPES.values()
            },
        }
    },
)
async def upload_avatar(
    request: Request,
    current_user: AuthenticatedUser = Depends(require_current_user),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ProfileResponse:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in AVATAR_TYPES.values():
        raise HTTPException(status_code=415, detail="Use a JPEG, PNG, or WebP image.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > AVATAR_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Avatar exceeds 5 MiB.")
        data.extend(chunk)
    payload = bytes(data)
    await anyio.to_thread.run_sync(
        validate_avatar, payload, content_type, limiter=request.app.state.avatar_limiter
    )
    await ensure_profile(session, current_user.id)
    version = uuid4()
    await session.execute(
        pg_insert(ProfileAvatar)
        .values(
            user_id=current_user.id,
            version=version,
            content_type=content_type,
            data=payload,
        )
        .on_conflict_do_update(
            index_elements=[ProfileAvatar.user_id],
            set_={
                "version": version,
                "content_type": content_type,
                "data": payload,
            },
        )
    )
    profile = await session.get(Profile, current_user.id)
    assert profile is not None
    profile.avatar_url = f"/avatars/{current_user.id}/{version}"
    await session.commit()
    return _to_response(profile, current_user, request, settings)
