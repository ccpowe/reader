"""Password registration/login without email verification or recovery mail."""

from typing import Literal

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_current_user, reserve_auth_attempt
from app.services.auth import AuthService
from app.storage.database import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


def normalize_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise ValueError("Enter a valid email address.") from None


class AuthUserResponse(BaseModel):
    id: str
    email: str


class AuthSessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"]
    expires_in: int
    expires_at: int
    user: AuthUserResponse


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=128)

    _normalize_email = field_validator("email")(normalize_email)


class RegisterRequest(LoginRequest):
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("display_name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=32, max_length=512)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=128)


class ChangeEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    email: str = Field(max_length=320)

    _normalize_email = field_validator("email")(normalize_email)


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


async def password_attempt(request: Request) -> None:
    # Charge successful attempts too: registration/login all consume costly hash work.
    reserve_auth_attempt(request, password=True)


@router.post(
    "/register", response_model=AuthSessionResponse, dependencies=[Depends(password_attempt)]
)
async def register(
    payload: RegisterRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_session),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await service.register(db, payload.email, payload.password, payload.display_name)


@router.post("/login", response_model=AuthSessionResponse, dependencies=[Depends(password_attempt)])
async def login(
    payload: LoginRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_session),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await service.login(db, payload.email, payload.password)


@router.post(
    "/refresh", response_model=AuthSessionResponse, dependencies=[Depends(password_attempt)]
)
async def refresh(
    payload: RefreshRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_session),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await service.refresh(db, payload.refresh_token)


@router.post("/logout", status_code=204)
async def logout(
    payload: RefreshRequest,
    service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await service.logout(db, payload.refresh_token)
    return Response(status_code=204)


@router.get("/user", response_model=AuthUserResponse)
async def user(current: AuthenticatedUser = Depends(require_current_user)) -> AuthUserResponse:
    return AuthUserResponse(id=str(current.id), email=current.email or "")


@router.patch(
    "/password", response_model=AuthSessionResponse, dependencies=[Depends(password_attempt)]
)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    current: AuthenticatedUser = Depends(require_current_user),
    service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_session),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await service.change_credentials(
        db, current.id, payload.current_password, password=payload.password
    )


@router.patch(
    "/email", response_model=AuthSessionResponse, dependencies=[Depends(password_attempt)]
)
async def change_email(
    payload: ChangeEmailRequest,
    response: Response,
    current: AuthenticatedUser = Depends(require_current_user),
    service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_session),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await service.change_credentials(
        db, current.id, payload.current_password, email=payload.email
    )
