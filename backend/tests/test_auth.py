import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.auth import AuthRateLimiter, ReaderJWTVerifier, require_current_user
from app.core.passwords import PasswordService
from app.core.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "service_error",
    [
        HTTPException(status_code=503, detail="JWKS unavailable"),
        RuntimeError("internal verifier failure"),
        asyncio.CancelledError(),
    ],
)
async def test_non_authentication_failures_refund_the_failure_budget(service_error) -> None:
    limiter = AuthRateLimiter(requests=1, window_seconds=60)
    verifier = type("Verifier", (), {})()

    async def fail(_token: str):
        raise service_error

    verifier.verify = fail
    request = type(
        "Request",
        (),
        {
            "client": type("Client", (), {"host": "203.0.113.9"})(),
            "app": type(
                "App",
                (),
                {
                    "state": type(
                        "State",
                        (),
                        {"auth_rate_limiter": limiter, "jwt_verifier": verifier},
                    )()
                },
            )(),
        },
    )()
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")

    with pytest.raises(type(service_error)):
        await require_current_user(request, credentials)

    async def invalid(_token: str):
        raise HTTPException(status_code=401, detail="invalid")

    verifier.verify = invalid
    with pytest.raises(HTTPException) as raised:
        await require_current_user(request, credentials)
    assert raised.value.status_code == 401


def test_auth_rate_limiter_bounds_unverified_requests_per_client() -> None:
    limiter = AuthRateLimiter(requests=2, window_seconds=60)

    assert limiter.retry_after("203.0.113.9", now=10.0) is None
    assert limiter.retry_after("203.0.113.9", now=11.0) is None
    retry_after = limiter.retry_after("203.0.113.9", now=12.0)

    assert retry_after is not None
    assert 0 < retry_after <= 60
    assert limiter.retry_after("203.0.113.10", now=12.0) is None


def test_successful_authentication_refund_does_not_consume_failure_budget() -> None:
    limiter = AuthRateLimiter(requests=2, window_seconds=60)

    for now in range(10):
        assert limiter.retry_after("203.0.113.9", now=float(now)) is None
        limiter.refund("203.0.113.9", now=float(now))

    assert limiter.retry_after("203.0.113.9", now=10.0) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired", "audience", "issuer", "missing_sid", "algorithm"])
async def test_local_verifier_rejects_invalid_claims_before_database_access(change):
    settings = Settings(
        _env_file=None,
        server_id="test",
        server_access_token="connection-token",
        auth_jwt_secret="jwt-signing-secret-with-32-or-more-characters",
    )
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": "reader:test",
        "aud": "reader",
    }
    algorithm = "HS256"
    if change == "expired":
        claims["exp"] = now - timedelta(seconds=1)
    if change == "audience":
        claims["aud"] = "other"
    if change == "issuer":
        claims["iss"] = "other"
    if change == "missing_sid":
        del claims["sid"]
    if change == "algorithm":
        algorithm = "HS384"
    token = jwt.encode(claims, settings.auth_jwt_secret.get_secret_value(), algorithm=algorithm)
    factory = AsyncMock()
    with pytest.raises(HTTPException) as caught:
        await ReaderJWTVerifier(settings, factory).verify(token)
    assert caught.value.status_code == 401
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_password_hashing_uses_argon2_and_upgrades_imported_bcrypt():
    from pwdlib.hashers.bcrypt import BcryptHasher

    service = PasswordService()
    encoded = await service.hash("correct password")
    assert encoded.startswith("$argon2id$")
    assert (await service.verify("wrong password", encoded))[0] is False
    old = BcryptHasher().hash("correct password")
    valid, replacement = await service.verify("correct password", old)
    assert valid and replacement.startswith("$argon2id$")
    assert (await service.verify("correct password", None))[0] is False
