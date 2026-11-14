"""Access tokens (15-minute JWTs) and refresh-token hashing."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from reader.core.errors import AppError
from reader.core.settings import Settings

ALGORITHM = "HS256"
AUDIENCE = "reader"


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    session_id: UUID


def _secret(settings: Settings) -> str:
    if settings.missing_server_settings():
        raise AppError(503, "server_not_configured", "Authentication is not configured.")
    assert settings.auth_jwt_secret is not None
    return settings.auth_jwt_secret.get_secret_value()


def _issuer(settings: Settings) -> str:
    return f"reader:{settings.server_id}"


def issue_access_token(settings: Settings, user_id: UUID, session_id: UUID) -> tuple[str, int]:
    """Return the token and its expiry as a Unix timestamp."""
    now = datetime.now(UTC)
    expires_at = int((now + timedelta(seconds=settings.auth_access_token_seconds)).timestamp())
    token = jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "iat": int(now.timestamp()),
            "exp": expires_at,
            "iss": _issuer(settings),
            "aud": AUDIENCE,
        },
        _secret(settings),
        algorithm=ALGORITHM,
    )
    return token, expires_at


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=_issuer(settings),
            options={"require": ["sub", "sid", "exp", "iat", "iss", "aud"]},
        )
        return AccessClaims(user_id=UUID(claims["sub"]), session_id=UUID(claims["sid"]))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise AppError(401, "not_authenticated", "Invalid or expired access token.") from None


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def refresh_token_hash(token: str) -> str:
    """Only this digest is stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
