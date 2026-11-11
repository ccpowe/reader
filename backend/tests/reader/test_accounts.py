"""Account pieces that need no database."""

from io import BytesIO
from uuid import uuid4

import httpx
import jwt
import pytest
from PIL import Image

from reader.accounts.avatars import validate_avatar
from reader.accounts.passwords import hash_password, verify_password
from reader.accounts.tokens import decode_access_token, issue_access_token, refresh_token_hash
from reader.core.errors import AppError
from reader.core.settings import Settings, get_settings
from reader.main import create_app

SETTINGS = Settings(server_id="home", server_access_token="token", auth_jwt_secret="j" * 40)


def test_access_token_round_trip() -> None:
    user, session = uuid4(), uuid4()

    token, expires_at = issue_access_token(SETTINGS, user, session)
    claims = decode_access_token(SETTINGS, token)

    assert (claims.user_id, claims.session_id) == (user, session)
    assert expires_at > 0


@pytest.mark.parametrize(
    "other",
    [
        Settings(server_id="home", server_access_token="token", auth_jwt_secret="k" * 40),
        Settings(server_id="elsewhere", server_access_token="token", auth_jwt_secret="j" * 40),
    ],
)
def test_token_from_another_secret_or_server_is_rejected(other: Settings) -> None:
    token, _ = issue_access_token(other, uuid4(), uuid4())

    with pytest.raises(AppError) as raised:
        decode_access_token(SETTINGS, token)

    assert (raised.value.status, raised.value.code) == (401, "not_authenticated")


def test_expired_token_is_rejected() -> None:
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "iat": 1,
            "exp": 2,
            "iss": "reader:home",
            "aud": "reader",
        },
        "j" * 40,
        algorithm="HS256",
    )

    with pytest.raises(AppError):
        decode_access_token(SETTINGS, expired)


def test_tokens_need_a_configured_server() -> None:
    with pytest.raises(AppError) as raised:
        issue_access_token(Settings(), uuid4(), uuid4())

    assert raised.value.code == "server_not_configured"


def test_refresh_tokens_are_stored_as_sha256() -> None:
    assert len(refresh_token_hash("abc")) == 64
    assert refresh_token_hash("abc") != refresh_token_hash("abd")


@pytest.mark.asyncio
async def test_password_hash_verifies_only_the_right_password() -> None:
    hashed = await hash_password("correct horse")

    assert hashed.startswith("$argon2")
    assert await verify_password("correct horse", hashed)
    assert not await verify_password("wrong", hashed)
    assert not await verify_password("anything", None)
    assert not await verify_password("anything", "not-a-hash")


def _image(fmt: str, size=(4, 4), **save) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "red").save(buffer, format=fmt, **save)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("fmt", "mime"), [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_supported_avatar_formats(fmt: str, mime: str) -> None:
    validate_avatar(_image(fmt), mime)


def _png_with_broken_checksum() -> bytes:
    data = bytearray(_image("PNG"))
    data[data.index(b"IDAT") + 6] ^= 0xFF  # flip a compressed byte; the CRC no longer matches
    return bytes(data)


def test_png_with_broken_checksum_is_rejected_not_a_server_error() -> None:
    with pytest.raises(AppError) as raised:
        validate_avatar(_png_with_broken_checksum(), "image/png")

    assert raised.value.code == "invalid_avatar"


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"not an image", "image/png"),
        (_image("PNG"), "image/jpeg"),  # declared type does not match
        (_image("GIF"), "image/png"),  # unsupported format
        (_image("PNG")[:40], "image/png"),  # truncated
    ],
)
def test_invalid_avatars_are_rejected(data: bytes, mime: str) -> None:
    with pytest.raises(AppError) as raised:
        validate_avatar(data, mime)

    assert (raised.value.status, raised.value.code) == (422, "invalid_avatar")


class _NoDatabase:
    """Stands in for a session factory: may be opened, must never be queried."""

    async def __aenter__(self):
        return object()  # any query on it would raise AttributeError

    async def __aexit__(self, *_exc):
        return False


async def _post(path: str, body: dict) -> httpx.Response:
    app = create_app()
    app.state.session_factory = _NoDatabase
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Reader-Server-Token": "token"},
    ) as client:
        return await client.post(path, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"email": "me@example.com", "password": "short"},
        {"email": "not-an-email", "password": "long enough password"},
        {"email": "me@example.com", "password": "long enough password", "admin": True},
    ],
)
async def test_register_rejects_bad_input_before_any_query(monkeypatch, body) -> None:
    monkeypatch.setenv("APP_SERVER_ACCESS_TOKEN", "token")
    get_settings.cache_clear()

    response = await _post("/auth/register", body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "long enough password" not in response.text


@pytest.mark.asyncio
async def test_me_without_bearer_token_is_not_authenticated(monkeypatch) -> None:
    monkeypatch.setenv("APP_SERVER_ACCESS_TOKEN", "token")
    get_settings.cache_clear()
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Reader-Server-Token": "token"},
    ) as client:
        response = await client.get("/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
