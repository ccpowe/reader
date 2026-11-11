"""The account flow end to end against a real, freshly migrated database."""

from io import BytesIO

import pytest
from PIL import Image

from reader.accounts import service
from reader.core.errors import AppError
from tests.reader.pg import api_client, postgres_dsn

pytestmark = pytest.mark.postgres

EMAIL = "me@example.com"
PASSWORD = "correct horse battery"


def _bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _register(client, email=EMAIL, password=PASSWORD, **extra) -> dict:
    response = await client.post(
        "/auth/register", json={"email": email, "password": password, **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _code(response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_register_login_and_me(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        registered = await _register(client, email=" Me@Example.COM ", display_name="  Me  ")
        duplicate = await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
        wrong = await client.post("/auth/login", json={"email": EMAIL, "password": "nope"})
        unknown = await client.post(
            "/auth/login", json={"email": "who@example.com", "password": PASSWORD}
        )
        login = await client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        me = await client.get("/me", headers=_bearer(login.json()))
        renamed = await client.patch(
            "/me", json={"display_name": "Reader"}, headers=_bearer(login.json())
        )

    assert registered["user"]["email"] == EMAIL
    assert registered["token_type"] == "bearer" and registered["expires_in"] == 900
    assert _code(duplicate) == (409, "email_taken")
    assert _code(wrong) == (401, "invalid_credentials")
    assert _code(unknown) == (401, "invalid_credentials")
    assert login.status_code == 200
    assert me.json() == {
        "id": registered["user"]["id"],
        "email": EMAIL,
        "display_name": "Me",
        "avatar_url": None,
    }
    assert renamed.json()["display_name"] == "Reader"


@pytest.mark.asyncio
async def test_refresh_rotates_and_reuse_ends_the_session(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        first = await _register(client)
        second = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
        still_valid = await client.get("/me", headers=_bearer(second.json()))
        replay = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
        after_replay = await client.get("/me", headers=_bearer(second.json()))
        newest = await client.post(
            "/auth/refresh", json={"refresh_token": second.json()["refresh_token"]}
        )

    assert second.status_code == 200
    assert second.json()["refresh_token"] != first["refresh_token"]
    assert still_valid.status_code == 200
    assert _code(replay) == (401, "refresh_token_reused")
    # Reuse means the token may be stolen: the whole session ends.
    assert _code(after_replay) == (401, "not_authenticated")
    assert _code(newest) == (401, "invalid_refresh_token")


@pytest.mark.asyncio
async def test_logout_ends_only_that_session(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        phone = await _register(client)
        laptop = (
            await client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        ).json()
        logout = await client.post("/auth/logout", json={"refresh_token": phone["refresh_token"]})
        phone_me = await client.get("/me", headers=_bearer(phone))
        laptop_me = await client.get("/me", headers=_bearer(laptop))

    assert logout.status_code == 204
    assert _code(phone_me) == (401, "not_authenticated")
    assert laptop_me.status_code == 200


@pytest.mark.asyncio
async def test_changing_credentials_ends_every_other_session(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        tokens = await _register(client)
        await _register(client, email="other@example.com")
        wrong = await client.patch(
            "/me/password",
            json={"current_password": "nope", "password": "new password 123"},
            headers=_bearer(tokens),
        )
        changed = await client.patch(
            "/me/password",
            json={"current_password": PASSWORD, "password": "new password 123"},
            headers=_bearer(tokens),
        )
        old_session = await client.get("/me", headers=_bearer(tokens))
        old_password = await client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        taken = await client.patch(
            "/me/email",
            json={"current_password": "new password 123", "email": "other@example.com"},
            headers=_bearer(changed.json()),
        )
        moved = await client.patch(
            "/me/email",
            json={"current_password": "new password 123", "email": "new@example.com"},
            headers=_bearer(changed.json()),
        )

    # A wrong current password is 403, so the client does not try to re-login.
    assert _code(wrong) == (403, "wrong_password")
    assert changed.status_code == 200
    assert _code(old_session) == (401, "not_authenticated")
    assert _code(old_password) == (401, "invalid_credentials")
    assert _code(taken) == (409, "email_taken")
    assert moved.json()["user"]["email"] == "new@example.com"


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_avatar_upload_and_versioned_public_url(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        tokens = await _register(client)
        headers = _bearer(tokens)
        wrong_type = await client.put(
            "/me/avatar", content=b"x", headers={**headers, "Content-Type": "image/gif"}
        )
        too_large = await client.put(
            "/me/avatar",
            content=b"0" * (5 * 1024 * 1024 + 1),
            headers={**headers, "Content-Type": "image/png"},
        )
        first = await client.put(
            "/me/avatar", content=_png(), headers={**headers, "Content-Type": "image/png"}
        )
        second = await client.put(
            "/me/avatar", content=_png(), headers={**headers, "Content-Type": "image/png"}
        )
        url = second.json()["avatar_url"].removeprefix("http://reader.test")
        image = await client.get(url, headers={"X-Reader-Server-Token": ""})
        stale = await client.get(first.json()["avatar_url"].removeprefix("http://reader.test"))

    assert _code(wrong_type) == (415, "unsupported_avatar_type")
    assert _code(too_large) == (413, "avatar_too_large")
    assert first.json()["avatar_url"].endswith("/1")
    assert second.json()["avatar_url"].endswith("/2")
    # Public, immutable and served without the server token.
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert "immutable" in image.headers["cache-control"]
    assert _code(stale) == (404, "avatar_not_found")


@pytest.mark.asyncio
async def test_password_attempts_are_rate_limited(monkeypatch) -> None:
    async with api_client(
        postgres_dsn(), monkeypatch, auth_password_attempts_per_minute="2"
    ) as client:
        attempts = [
            await client.post("/auth/login", json={"email": EMAIL, "password": "nope"})
            for _ in range(3)
        ]

    assert [response.status_code for response in attempts] == [401, 401, 429]
    assert attempts[2].json()["error"]["code"] == "too_many_attempts"
    assert int(attempts[2].headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_operator_password_reset_ends_sessions(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        tokens = await _register(client)
        async with client.app.state.session_factory() as db:
            await service.reset_password(db, EMAIL, "reset password 1")
            with pytest.raises(AppError) as missing:
                await service.reset_password(db, "who@example.com", "reset password 1")
        me = await client.get("/me", headers=_bearer(tokens))
        login = await client.post(
            "/auth/login", json={"email": EMAIL, "password": "reset password 1"}
        )

    assert missing.value.code == "user_not_found"
    assert _code(me) == (401, "not_authenticated")
    assert login.status_code == 200
