"""Local JWT verification and bounded authentication failure budgets."""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.configuration import reader_configuration_missing
from app.core.settings import Settings
from app.storage.auth_models import ReaderSession, ReaderUser

bearer_scheme = HTTPBearer(auto_error=False)
AUTH_ALGORITHM = "HS256"
AUTH_AUDIENCE = "reader"


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str | None
    claims: dict[str, Any]


def invalid_credentials(detail: str = "Invalid or expired access token.") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def auth_issuer(settings: Settings) -> str:
    return f"reader:{settings.server_id}"


def signing_secret(settings: Settings) -> str:
    if reader_configuration_missing(settings):
        raise HTTPException(status_code=503, detail="Authentication has not been configured.")
    assert settings.auth_jwt_secret is not None
    return settings.auth_jwt_secret.get_secret_value()


class ReaderJWTVerifier:
    def __init__(
        self, settings: Settings, session_factory: async_sessionmaker[AsyncSession] | None
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory

    async def aclose(self) -> None:
        pass

    async def verify(self, token: str) -> AuthenticatedUser:
        secret = signing_secret(self._settings)
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=[AUTH_ALGORITHM],
                audience=AUTH_AUDIENCE,
                issuer=auth_issuer(self._settings),
                options={"require": ["sub", "sid", "exp", "iat", "iss", "aud"]},
            )
            user_id = UUID(claims["sub"])
            session_id = UUID(claims["sid"])
        except (jwt.PyJWTError, KeyError, TypeError, ValueError):
            raise invalid_credentials() from None
        if self._session_factory is None:
            raise HTTPException(status_code=503, detail="Database has not been configured.")
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ReaderUser)
                    .join(ReaderSession, ReaderSession.user_id == ReaderUser.id)
                    .where(
                        ReaderSession.id == session_id,
                        ReaderSession.user_id == user_id,
                        ReaderSession.revoked_at.is_(None),
                        ReaderSession.expires_at > datetime.now(UTC),
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            raise invalid_credentials()
        return AuthenticatedUser(id=user_id, email=row.email, claims=claims)


class AuthRateLimiter:
    """Constant-space token buckets charged only for failed authentication."""

    def __init__(
        self,
        *,
        requests: int,
        window_seconds: float,
        max_clients: int = 10_000,
    ) -> None:
        if requests < 1 or window_seconds <= 0 or max_clients < 1:
            raise ValueError("Authentication rate-limit values must be positive.")
        self._requests = requests
        self._window_seconds = window_seconds
        self._refill_per_second = requests / window_seconds
        self._max_clients = max_clients
        self._buckets: OrderedDict[Hashable, _AuthTokenBucket] = OrderedDict()

    def retry_after(self, client_key: Hashable, *, now: float | None = None) -> float | None:
        current = monotonic() if now is None else now
        bucket = self._buckets.get(client_key)
        if bucket is None:
            if len(self._buckets) >= self._max_clients:
                self._buckets.popitem(last=False)
            bucket = _AuthTokenBucket(tokens=float(self._requests), updated_at=current)
            self._buckets[client_key] = bucket
        else:
            self._buckets.move_to_end(client_key)
            self._refill(bucket, current)
        if bucket.tokens < 1:
            return max((1 - bucket.tokens) / self._refill_per_second, 0.001)
        bucket.tokens -= 1
        return None

    def refund(self, client_key: Hashable, *, now: float | None = None) -> None:
        """Return a reservation after a token has authenticated successfully."""
        bucket = self._buckets.get(client_key)
        if bucket is None:
            return
        current = monotonic() if now is None else now
        self._refill(bucket, current)
        bucket.tokens = min(float(self._requests), bucket.tokens + 1)
        self._buckets.move_to_end(client_key)

    def _refill(self, bucket: _AuthTokenBucket, now: float) -> None:
        elapsed = max(now - bucket.updated_at, 0.0)
        bucket.tokens = min(
            float(self._requests),
            bucket.tokens + elapsed * self._refill_per_second,
        )
        bucket.updated_at = now


@dataclass
class _AuthTokenBucket:
    tokens: float
    updated_at: float


def get_jwt_verifier(request: Request) -> ReaderJWTVerifier:
    return request.app.state.jwt_verifier


def reserve_auth_attempt(
    request: Request, *, password: bool = False
) -> tuple[AuthRateLimiter, str]:
    limiter = (
        request.app.state.password_rate_limiter if password else request.app.state.auth_rate_limiter
    )
    client_key = request.client.host if request.client is not None else "unknown"
    retry_after = limiter.retry_after(client_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts.",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )
    return limiter, client_key


async def require_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None:
        raise invalid_credentials("Missing bearer token.")
    limiter, client_key = reserve_auth_attempt(request)
    try:
        user = await get_jwt_verifier(request).verify(credentials.credentials)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            limiter.refund(client_key)
        raise
    except BaseException:
        limiter.refund(client_key)
        raise
    else:
        limiter.refund(client_key)
        return user
