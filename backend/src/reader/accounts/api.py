"""Account routes: /auth/*, /me/* and public avatar images."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import anyio
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from reader.accounts import service
from reader.accounts.auth import CurrentUser, current_user, take_attempt
from reader.accounts.avatars import AVATAR_MAX_BYTES, AVATAR_TYPES, validate_avatar
from reader.core.db import get_session
from reader.core.errors import DATABASE_UNAVAILABLE, AppError, error_responses
from reader.core.server_access import document_server_token
from reader.core.settings import Settings, get_settings
from reader.models import User

auth_router = APIRouter(
    prefix="/auth", tags=["accounts"], dependencies=[Depends(document_server_token)]
)
me_router = APIRouter(
    prefix="/me", tags=["accounts"], dependencies=[Depends(document_server_token)]
)
avatar_router = APIRouter(tags=["accounts"])


# --- Request and response bodies ----------------------------------------------


def normalize_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise ValueError("Enter a valid email address.") from None


def _trim(value: str | None) -> str | None:
    return (value.strip() or None) if value is not None else None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(Strict):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=128)
    _email = field_validator("email")(normalize_email)


class RegisterRequest(LoginRequest):
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)
    _name = field_validator("display_name")(_trim)


class RefreshRequest(Strict):
    refresh_token: str = Field(min_length=32, max_length=512)


class ChangePasswordRequest(Strict):
    current_password: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=128)


class ChangeEmailRequest(Strict):
    current_password: str = Field(min_length=1, max_length=128)
    email: str = Field(max_length=320)
    _email = field_validator("email")(normalize_email)


class UpdateMeRequest(Strict):
    display_name: str | None = Field(max_length=120)
    _name = field_validator("display_name")(_trim)


class SessionUser(BaseModel):
    id: str
    email: str


class TokensResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    expires_at: int
    user: SessionUser


class MeResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    avatar_url: str | None


def _tokens(tokens: service.Tokens, response: Response) -> TokensResponse:
    response.headers["Cache-Control"] = "no-store"
    return TokensResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        expires_at=tokens.expires_at,
        user=SessionUser(id=str(tokens.user_id), email=tokens.email),
    )


def _me(user: User, request: Request, settings: Settings) -> MeResponse:
    avatar_url = None
    if user.avatar_content_type is not None:
        base = str(settings.public_api_base_url or request.base_url).rstrip("/")
        avatar_url = f"{base}/avatars/{user.id}/{user.avatar_version}"
    return MeResponse(
        id=str(user.id), email=user.email, display_name=user.display_name, avatar_url=avatar_url
    )


async def password_attempt(request: Request) -> None:
    """Every password check costs Argon2 work, so successes are charged too."""
    take_attempt(request.app.state.password_limiter, request)


_ATTEMPTS = (429, "too_many_attempts")
_NOT_CONFIGURED = (503, "server_not_configured")


# --- /auth ----------------------------------------------------------------------


@auth_router.post(
    "/register",
    response_model=TokensResponse,
    dependencies=[Depends(password_attempt)],
    responses=error_responses(
        (409, "email_taken"), _ATTEMPTS, _NOT_CONFIGURED, DATABASE_UNAVAILABLE, access="server"
    ),
)
async def register(
    body: RegisterRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    tokens = await service.register(db, settings, body.email, body.password, body.display_name)
    return _tokens(tokens, response)


@auth_router.post(
    "/login",
    response_model=TokensResponse,
    dependencies=[Depends(password_attempt)],
    responses=error_responses(
        (401, "invalid_credentials"),
        _ATTEMPTS,
        _NOT_CONFIGURED,
        DATABASE_UNAVAILABLE,
        access="server",
    ),
)
async def login(
    body: LoginRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    return _tokens(await service.login(db, settings, body.email, body.password), response)


@auth_router.post(
    "/refresh",
    response_model=TokensResponse,
    dependencies=[Depends(password_attempt)],
    responses=error_responses(
        (401, "invalid_refresh_token"),
        (401, "refresh_token_reused"),
        _ATTEMPTS,
        _NOT_CONFIGURED,
        DATABASE_UNAVAILABLE,
        access="server",
    ),
)
async def refresh(
    body: RefreshRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    return _tokens(await service.refresh(db, settings, body.refresh_token), response)


@auth_router.post(
    "/logout", status_code=204, responses=error_responses(DATABASE_UNAVAILABLE, access="server")
)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_session)) -> Response:
    await service.logout(db, body.refresh_token)
    return Response(status_code=204)


# --- /me ------------------------------------------------------------------------


@me_router.get("", response_model=MeResponse, responses=error_responses())
async def get_me(
    request: Request,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    return _me(await service.get_user(db, user.id), request, settings)


@me_router.patch("", response_model=MeResponse, responses=error_responses())
async def update_me(
    body: UpdateMeRequest,
    request: Request,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    return _me(await service.update_profile(db, user.id, body.display_name), request, settings)


@me_router.patch(
    "/password",
    response_model=TokensResponse,
    dependencies=[Depends(password_attempt)],
    responses=error_responses((403, "wrong_password"), _ATTEMPTS),
)
async def change_password(
    body: ChangePasswordRequest,
    response: Response,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    tokens = await service.change_credentials(
        db, settings, user.id, body.current_password, new_password=body.password
    )
    return _tokens(tokens, response)


@me_router.patch(
    "/email",
    response_model=TokensResponse,
    dependencies=[Depends(password_attempt)],
    responses=error_responses((403, "wrong_password"), (409, "email_taken"), _ATTEMPTS),
)
async def change_email(
    body: ChangeEmailRequest,
    response: Response,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    tokens = await service.change_credentials(
        db, settings, user.id, body.current_password, new_email=body.email
    )
    return _tokens(tokens, response)


@me_router.put(
    "/avatar",
    response_model=MeResponse,
    responses=error_responses(
        (413, "avatar_too_large"),
        (415, "unsupported_avatar_type"),
        (422, "invalid_avatar"),
    ),
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
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    """Send the raw image bytes with a JPEG, PNG or WebP Content-Type."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in AVATAR_TYPES.values():
        raise AppError(415, "unsupported_avatar_type", "Use a JPEG, PNG or WebP image.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > AVATAR_MAX_BYTES:
            raise AppError(413, "avatar_too_large", "The image exceeds 5 MiB.")
        data.extend(chunk)
    payload = bytes(data)
    await anyio.to_thread.run_sync(validate_avatar, payload, content_type)
    return _me(await service.set_avatar(db, user.id, payload, content_type), request, settings)


@me_router.delete("/avatar", response_model=MeResponse, responses=error_responses())
async def remove_avatar(
    request: Request,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    """Remove the avatar (no avatar is not an error)."""
    return _me(await service.remove_avatar(db, user.id), request, settings)


# --- Public avatar images ---------------------------------------------------------


@avatar_router.get(
    "/avatars/{user_id}/{version}",
    response_class=Response,
    responses={
        200: {"content": {mime: {} for mime in AVATAR_TYPES.values()}},
        **error_responses((404, "avatar_not_found"), DATABASE_UNAVAILABLE, access="public"),
    },
)
async def get_avatar(
    user_id: UUID,
    version: int = Path(ge=1, le=2_147_483_647),
    db: AsyncSession = Depends(get_session),
):
    """Versioned URL: a new upload gets a new URL, so images can be cached forever."""
    avatar = await service.get_avatar(db, user_id, version)
    return Response(
        content=avatar.data,
        media_type=avatar.content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
