"""Real PostgreSQL identity, migration, avatar and concurrent revocation contracts."""

import asyncio
from io import BytesIO
from uuid import uuid4

import asyncpg
import httpx
import pytest
from PIL import Image
from pwdlib.hashers.bcrypt import BcryptHasher
from test_postgres_migrations import (
    _assert_disposable_database,
    _run_alembic,
    _test_dsn,
    _test_guard_token,
)

from app.core.settings import Settings, get_settings
from app.main import create_app
from app.storage.schema import REQUIRED_SCHEMA_CONTRACT

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
TOKEN = "integration-only-connection-token"
SECRET = "integration-only-independent-jwt-secret-32-characters"


async def guarded_connection():
    connection = await asyncpg.connect(_test_dsn())
    await _assert_disposable_database(connection, _test_guard_token())
    return connection


async def reset_public(connection):
    await connection.execute(
        "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; "
        "DROP SCHEMA IF EXISTS auth CASCADE"
    )


async def test_fresh_install_needs_only_bare_postgres():
    connection = await guarded_connection()
    try:
        await reset_public(connection)
        _run_alembic(_test_dsn(), "upgrade", "head")
        assert await connection.fetchval("SELECT to_regclass('auth.users')") is None
        assert await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM app_schema_contracts WHERE contract=$1)",
            REQUIRED_SCHEMA_CONTRACT,
        )
        for name in ("reader_users", "reader_sessions", "reader_refresh_tokens", "profile_avatars"):
            assert await connection.fetchval("SELECT to_regclass($1)", name) is not None
    finally:
        await connection.close()


async def test_existing_profiles_and_bcrypt_survive_legacy_auth_detachment():
    connection = await guarded_connection()
    user_id, banned_id = uuid4(), uuid4()
    old_hash = BcryptHasher().hash("legacy")
    try:
        await reset_public(connection)
        _run_alembic(_test_dsn(), "upgrade", "20260910_27")
        await connection.execute("""
            CREATE SCHEMA auth;
            CREATE TABLE auth.users (id uuid PRIMARY KEY, email text, encrypted_password text,
                                     banned_until timestamptz, deleted_at timestamptz);
            ALTER TABLE profiles ADD CONSTRAINT legacy_auth_user_fk
                FOREIGN KEY(id) REFERENCES auth.users(id);
            CREATE FUNCTION public.handle_new_user() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN INSERT INTO profiles(id) VALUES(NEW.id); RETURN NEW; END $$;
            CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users
                FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
        """)
        await connection.execute(
            "INSERT INTO auth.users(id,email,encrypted_password) "
            "VALUES($1,'  Legacy@Example.com  ',$2)",
            user_id,
            old_hash,
        )
        await connection.execute(
            "INSERT INTO auth.users(id,email,encrypted_password,banned_until) "
            "VALUES($1,'banned@example.com',$2,now()+interval '1 year')",
            banned_id,
            old_hash,
        )
        await connection.execute(
            "UPDATE profiles SET display_name='Preserved' WHERE id=$1", user_id
        )
        _run_alembic(_test_dsn(), "upgrade", "head")
        assert (
            await connection.fetchval("SELECT password_hash FROM reader_users WHERE id=$1", user_id)
            == old_hash
        )
        assert (
            await connection.fetchval("SELECT email FROM reader_users WHERE id=$1", user_id)
            == "legacy@example.com"
        )
        assert (
            await connection.fetchval("SELECT id FROM reader_users WHERE id=$1", banned_id) is None
        )
        await connection.execute("DROP SCHEMA auth CASCADE")
        assert (
            await connection.fetchval("SELECT display_name FROM profiles WHERE id=$1", user_id)
            == "Preserved"
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid='profiles'::regclass AND contype='f'"
            )
            == 0
        )
    finally:
        await connection.close()


@pytest.fixture
async def api(monkeypatch):
    connection = await guarded_connection()
    try:
        if await connection.fetchval("SELECT to_regclass('reader_users')") is None:
            _run_alembic(_test_dsn(), "upgrade", "head")
    finally:
        await connection.close()
    settings = Settings(
        _env_file=None,
        server_id="integration",
        server_access_token=TOKEN,
        auth_jwt_secret=SECRET,
        database_url=_test_dsn(),
        database_ssl=False,
        translation_default_engine_id="disabled",
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Reader-Server-Token": TOKEN},
        ) as client:
            yield client


async def register(api, email=None):
    response = await api.post(
        "/v1/auth/register",
        json={
            "email": email or f"{uuid4()}@example.com",
            "password": "old password",
            "display_name": "Reader",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["access_token"] and result["refresh_token"]
    assert response.headers["cache-control"] == "no-store"
    return result


def bearer(session):
    return {"Authorization": f"Bearer {session['access_token']}"}


async def test_register_normalization_profile_and_password_policy(api):
    email = f"Case-{uuid4()}@Example.COM"
    first = await register(api, f" {email} ")
    assert first["user"]["email"] == email.lower()
    duplicate = await api.post(
        "/v1/auth/register", json={"email": email.lower(), "password": "old password"}
    )
    assert duplicate.status_code == 409
    assert (await api.get("/v1/auth/user", headers=bearer(first))).json() == first["user"]
    profile = await api.get("/v1/me/profile", headers=bearer(first))
    assert profile.json()["display_name"] == "Reader"
    too_short = await api.post(
        "/v1/auth/register", json={"email": "short@example.com", "password": "secret"}
    )
    assert too_short.status_code == 422
    assert "secret" not in too_short.text
    wrong = await api.post("/v1/auth/login", json={"email": email, "password": "wrong"})
    assert wrong.status_code == 401


async def test_legacy_six_character_bcrypt_can_login_and_rehash(api):
    connection = await guarded_connection()
    user_id = uuid4()
    email = f"legacy-{uuid4()}@example.com"
    old_hash = BcryptHasher().hash("legacy")
    try:
        await connection.execute("INSERT INTO profiles(id) VALUES($1)", user_id)
        await connection.execute(
            "INSERT INTO reader_users(id,email,password_hash) VALUES($1,$2,$3)",
            user_id,
            email,
            old_hash,
        )
        response = await api.post("/v1/auth/login", json={"email": email, "password": "legacy"})
        assert response.status_code == 200, response.text
        encoded = await connection.fetchval(
            "SELECT password_hash FROM reader_users WHERE id=$1", user_id
        )
        assert encoded.startswith("$argon2id$")
    finally:
        await connection.close()


async def test_refresh_rotation_replay_revokes_access_and_descendants(api):
    first = await register(api)
    second_response = await api.post(
        "/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert second_response.status_code == 200
    second = second_response.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert (await api.get("/v1/auth/user", headers=bearer(second))).status_code == 200
    replay = await api.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    assert (await api.get("/v1/auth/user", headers=bearer(second))).status_code == 401
    assert (
        await api.post("/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    ).status_code == 401
    connection = await guarded_connection()
    try:
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM reader_refresh_tokens WHERE token_hash=$1",
                first["refresh_token"],
            )
            == 0
        )
    finally:
        await connection.close()


async def test_simultaneous_refresh_serializes_and_detects_replay(api):
    first = await register(api)
    responses = await asyncio.gather(
        *[
            api.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
            for _ in range(2)
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 401]
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert (await api.get("/v1/auth/user", headers=bearer(winner))).status_code == 401


async def test_password_email_and_logout_revoke_sessions_and_preserve_profile(api):
    first = await register(api)
    login = await api.post(
        "/v1/auth/login", json={"email": first["user"]["email"], "password": "old password"}
    )
    other = login.json()
    changed = await api.patch(
        "/v1/auth/password",
        headers=bearer(first),
        json={
            "current_password": "old password",
            "password": "new password",
        },
    )
    assert changed.status_code == 200
    second = changed.json()
    for old in (first, other):
        assert (await api.get("/v1/auth/user", headers=bearer(old))).status_code == 401
        assert (
            await api.post("/v1/auth/refresh", json={"refresh_token": old["refresh_token"]})
        ).status_code == 401
    new_email = f"changed-{uuid4()}@example.com"
    changed_email = await api.patch(
        "/v1/auth/email",
        headers=bearer(second),
        json={
            "current_password": "new password",
            "email": new_email,
        },
    )
    assert changed_email.status_code == 200
    third = changed_email.json()
    assert third["user"] == {"id": first["user"]["id"], "email": new_email}
    assert (await api.get("/v1/auth/user", headers=bearer(second))).status_code == 401
    assert (await api.get("/v1/me/profile", headers=bearer(third))).json()[
        "display_name"
    ] == "Reader"
    assert (
        await api.post("/v1/auth/logout", json={"refresh_token": third["refresh_token"]})
    ).status_code == 204
    assert (await api.get("/v1/auth/user", headers=bearer(third))).status_code == 401


async def test_avatar_persists_and_public_random_url_needs_no_credentials(api):
    account = await register(api)
    output = BytesIO()
    Image.new("RGB", (10, 10), "red").save(output, format="PNG")
    data = output.getvalue()
    uploaded = await api.put(
        "/v1/me/profile/avatar",
        content=data,
        headers={**bearer(account), "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 200, uploaded.text
    avatar_url = uploaded.json()["avatar_url"]
    assert "/avatars/" in avatar_url and TOKEN not in avatar_url
    # Override client defaults with no connection header; the random URL remains public.
    request = api.build_request("GET", avatar_url)
    del request.headers["X-Reader-Server-Token"]
    image = await api.send(request)
    assert image.status_code == 200 and image.content == data
    assert image.headers["x-content-type-options"] == "nosniff"
    bad = await api.put(
        "/v1/me/profile/avatar",
        content=b"not image",
        headers={**bearer(account), "Content-Type": "image/png"},
    )
    assert bad.status_code == 422
    large = await api.put(
        "/v1/me/profile/avatar",
        content=b"x" * (5 * 1024 * 1024 + 1),
        headers={**bearer(account), "Content-Type": "image/png"},
    )
    assert large.status_code == 413


async def test_admin_reset_revokes_all_sessions_without_password_arguments(api, monkeypatch):
    from app.admin.cli import build_parser
    from app.admin.users import run_user_admin

    account = await register(api)
    values = iter(["admin changed password", "admin changed password"])
    monkeypatch.setattr("app.admin.users.getpass.getpass", lambda _prompt: next(values))
    args = build_parser().parse_args(["users", "reset-password", account["user"]["email"]])
    app = api._transport.app
    result = await run_user_admin(app.state.session_factory, args)
    assert result["sessions_revoked"] is True
    assert "password" not in vars(args)
    assert (await api.get("/v1/auth/user", headers=bearer(account))).status_code == 401
    login = await api.post(
        "/v1/auth/login",
        json={
            "email": account["user"]["email"],
            "password": "admin changed password",
        },
    )
    assert login.status_code == 200


async def test_duplicate_email_change_rolls_back_and_keeps_original_session(api):
    account = await register(api)
    other = await register(api)
    result = await api.patch(
        "/v1/auth/email",
        headers=bearer(account),
        json={
            "current_password": "old password",
            "email": other["user"]["email"],
        },
    )
    assert result.status_code == 409
    assert (await api.get("/v1/auth/user", headers=bearer(account))).json() == account["user"]
