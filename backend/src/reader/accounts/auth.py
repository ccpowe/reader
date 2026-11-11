"""The `current_user` dependency every signed-in route uses."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from reader.accounts.tokens import decode_access_token
from reader.core.errors import AppError
from reader.core.rate_limit import RateLimiter
from reader.core.settings import Settings, get_settings
from reader.models import Session, User

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str
    session_id: UUID


def client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def take_attempt(limiter: RateLimiter, request: Request) -> str:
    """Charge one attempt to this client, or raise 429."""
    key = client_key(request)
    wait = limiter.retry_after(key)
    if wait is not None:
        raise AppError(
            429,
            "too_many_attempts",
            "Too many authentication attempts.",
            retry_after=max(1, math.ceil(wait)),
        )
    return key


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """A valid access token whose session is neither revoked nor expired.

    Only failed checks count against the client's token-attempt budget.
    """
    if credentials is None:
        raise AppError(401, "not_authenticated", "Sign in first.")
    limiter: RateLimiter = request.app.state.token_limiter
    key = take_attempt(limiter, request)
    claims = decode_access_token(settings, credentials.credentials)
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise AppError(503, "database_unavailable", "The database is not configured.")
    async with factory() as db:
        row = (
            await db.execute(
                select(User.id, User.email)
                .join(Session, Session.user_id == User.id)
                .where(
                    Session.id == claims.session_id,
                    Session.user_id == claims.user_id,
                    Session.revoked_at.is_(None),
                    Session.expires_at > datetime.now(UTC),
                )
            )
        ).one_or_none()
    if row is None:
        raise AppError(401, "not_authenticated", "The session has ended. Sign in again.")
    limiter.refund(key)
    return CurrentUser(id=row.id, email=row.email, session_id=claims.session_id)
