import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from reader.core.settings import get_settings
from reader.main import create_app
from reader.models import WorkerHeartbeat
from reader.system.heartbeats import summarize

TOKEN = "server-token"


def _configure(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setenv(f"APP_{name.upper()}", value)
    get_settings.cache_clear()


async def _get(path: str, *, token: str | None = TOKEN, base: str = "http://reader.local"):
    app = create_app()
    headers = {"X-Reader-Server-Token": token} if token else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as client:
        return await client.get(path, headers=headers)


@pytest.mark.asyncio
async def test_discovery_reports_protocol_3_and_the_request_base(monkeypatch) -> None:
    _configure(monkeypatch, server_id="home", server_access_token=TOKEN, auth_jwt_secret="j" * 40)

    response = await _get("/.well-known/reader.json")

    assert response.status_code == 200
    assert response.json() == {
        "protocol_version": 3,
        "server_id": "home",
        "api_base_url": "http://reader.local",
    }


@pytest.mark.asyncio
async def test_discovery_prefers_the_configured_public_url(monkeypatch) -> None:
    _configure(
        monkeypatch,
        server_id="home",
        server_access_token=TOKEN,
        auth_jwt_secret="j" * 40,
        public_api_base_url="https://reader.example/api/",
    )

    response = await _get("/.well-known/reader.json")

    assert response.json()["api_base_url"] == "https://reader.example/api"


@pytest.mark.asyncio
async def test_discovery_names_missing_settings(monkeypatch) -> None:
    _configure(monkeypatch, server_access_token=TOKEN)

    response = await _get("/.well-known/reader.json")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "server_not_configured"
    assert "APP_SERVER_ID" in error["message"] and "APP_AUTH_JWT_SECRET" in error["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "wrong"])
async def test_every_non_public_path_needs_the_server_token(monkeypatch, token) -> None:
    _configure(monkeypatch, server_id="home", server_access_token=TOKEN, auth_jwt_secret="j" * 40)

    for path in ("/.well-known/reader.json", "/feed", "/anything"):
        response = await _get(path, token=token)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_server_token"


@pytest.mark.asyncio
async def test_unconfigured_token_closes_the_server(monkeypatch) -> None:
    response = await _get("/feed")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "server_not_configured"


@pytest.mark.asyncio
async def test_public_paths_need_no_token() -> None:
    health = await _get("/health", token=None)
    ready = await _get("/ready", token=None)

    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert ready.status_code == 503 and ready.json()["error"]["code"] == "not_ready"


@pytest.mark.asyncio
async def test_v1_prefix_is_gone(monkeypatch) -> None:
    _configure(monkeypatch, server_id="home", server_access_token=TOKEN, auth_jwt_secret="j" * 40)

    response = await _get("/v1/.well-known/reader.json")

    assert response.status_code == 404


def _row(**values) -> WorkerHeartbeat:
    now = datetime(2026, 11, 10, 12, tzinfo=UTC)
    defaults = {
        "loop_name": "source_scan",
        "expected_interval_seconds": 60,
        "last_started_at": now,
        "last_succeeded_at": now,
        "last_failed_at": None,
        "last_error_code": None,
        "updated_at": now,
    }
    return WorkerHeartbeat(**{**defaults, **values})


@pytest.mark.parametrize(
    ("values", "seconds_later", "state"),
    [
        ({}, 10, "healthy"),
        ({}, 181, "stale"),
        ({"last_succeeded_at": None}, 10, "starting"),
        ({"last_failed_at": datetime(2026, 11, 10, 12, 0, 5, tzinfo=UTC)}, 10, "failing"),
    ],
)
def test_loop_state(values, seconds_later, state) -> None:
    now = datetime(2026, 11, 10, 12, tzinfo=UTC) + timedelta(seconds=seconds_later)

    assert summarize("source_scan", _row(**values), now=now).state == state


def test_missing_loop() -> None:
    assert summarize("cleanup", None).state == "missing"


class _HangingSession:
    """A session factory whose connection never answers."""

    async def __aenter__(self):
        await asyncio.sleep(30)

    async def __aexit__(self, *_exc):
        return False


@pytest.mark.asyncio
async def test_worker_ready_gives_up_on_a_stuck_database(monkeypatch) -> None:
    _configure(monkeypatch, readiness_timeout_seconds="0.5")
    app = create_app()
    app.state.session_factory = _HangingSession
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = asyncio.get_running_loop().time()
        response = await client.get("/worker-ready")
        elapsed = asyncio.get_running_loop().time() - started

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "worker_not_ready"
    assert elapsed < 5
