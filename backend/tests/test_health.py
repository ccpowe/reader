import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app
from app.storage.schema import RuntimeReadinessProbe, runtime_readiness_failures


def test_health_check_is_available_without_supabase(monkeypatch) -> None:
    monkeypatch.setattr("app.main.get_settings", lambda: Settings(_env_file=None))

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_protected_route_fails_closed_without_server_access_configuration(monkeypatch) -> None:
    monkeypatch.setattr("app.main.get_settings", lambda: Settings(_env_file=None))

    with TestClient(create_app()) as client:
        response = client.get("/v1/me")

    assert response.status_code == 503


def test_readiness_rejects_missing_runtime_configuration(monkeypatch) -> None:
    monkeypatch.setattr("app.main.get_settings", lambda: Settings(_env_file=None))

    with TestClient(create_app()) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "not_ready",
        "failures": [
            "missing_server_id",
            "missing_server_access_token",
            "invalid_auth_jwt_secret",
            "missing_database_url",
        ],
    }


def test_worker_readiness_reports_missing_database_without_hiding_it(monkeypatch) -> None:
    monkeypatch.setattr("app.main.get_settings", lambda: Settings(_env_file=None))

    with TestClient(create_app()) as client:
        response = client.get("/worker-ready")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "not_ready",
        "failures": ["database_unavailable"],
    }


def test_lifespan_primes_a_cold_database_before_short_readiness_checks(monkeypatch) -> None:
    class Result:
        def scalar_one(self) -> bool:
            return True

    class Connection:
        async def execute(self, _statement, *_args, **_kwargs) -> Result:
            return Result()

    class ConnectionContext:
        def __init__(self, delay_seconds: float) -> None:
            self._delay_seconds = delay_seconds

        async def __aenter__(self) -> Connection:
            await asyncio.sleep(self._delay_seconds)
            return Connection()

        async def __aexit__(self, *_args) -> None:
            return None

    class ColdEngine:
        def __init__(self) -> None:
            self.connect_calls = 0

        def connect(self) -> ConnectionContext:
            self.connect_calls += 1
            delay_seconds = 0.6 if self.connect_calls == 1 else 0
            return ConnectionContext(delay_seconds)

        async def dispose(self) -> None:
            return None

    engine = ColdEngine()
    settings = Settings(
        _env_file=None,
        server_id="reader-test",
        database_url="postgresql://reader:test@example.com/reader",
        server_access_token="test-connection",
        auth_jwt_secret="test-jwt-signing-secret-at-least-32-characters",
        readiness_timeout_seconds=0.5,
        readiness_startup_timeout_seconds=1.0,
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.build_engine", lambda _settings: engine)
    monkeypatch.setattr("app.main.build_session_factory", lambda _engine: object())

    with TestClient(create_app()) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert engine.connect_calls == 1


@pytest.mark.asyncio
async def test_readiness_rejects_a_database_without_the_required_contract() -> None:
    statements: list[str] = []

    class Result:
        def scalar_one(self) -> bool:
            return False

    class Connection:
        async def execute(self, statement, *_args):
            statements.append(str(statement))
            return Result()

    class ConnectionContext:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_args) -> None:
            return None

    class Engine:
        def connect(self) -> ConnectionContext:
            return ConnectionContext()

    settings = Settings(
        _env_file=None,
        server_id="reader-test",
        database_url="postgresql://reader:test@example.com/reader",
        server_access_token="test-connection",
        auth_jwt_secret="test-jwt-signing-secret-at-least-32-characters",
    )

    failures = await runtime_readiness_failures(settings, Engine())  # type: ignore[arg-type]

    assert failures == ["schema_contract_missing"]
    assert len(statements) == 1
    assert "app_schema_contracts" in statements[0]


@pytest.mark.asyncio
async def test_readiness_probe_single_flights_and_caches_database_checks(monkeypatch) -> None:
    calls = 0

    async def check(*_args) -> list[str]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return []

    monkeypatch.setattr("app.storage.schema.runtime_readiness_failures", check)
    probe = RuntimeReadinessProbe(cache_seconds=5, timeout_seconds=2)

    first, second = await asyncio.gather(
        probe.check(Settings(_env_file=None), None),
        probe.check(Settings(_env_file=None), None),
    )

    assert first == second == []
    assert calls == 1


@pytest.mark.asyncio
async def test_readiness_timeout_does_not_cancel_the_inflight_recovery(monkeypatch) -> None:
    calls = 0
    cancelled = False
    release = asyncio.Event()

    async def check(*_args) -> list[str]:
        nonlocal calls, cancelled
        calls += 1
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise
        return []

    monkeypatch.setattr("app.storage.schema.runtime_readiness_failures", check)
    probe = RuntimeReadinessProbe(cache_seconds=5, timeout_seconds=0.01)

    first = await probe.check(Settings(_env_file=None), None)
    release.set()
    second = await probe.check(Settings(_env_file=None), None)

    assert first == ["database_readiness_timeout"]
    assert second == []
    assert calls == 1
    assert cancelled is False


@pytest.mark.asyncio
async def test_readiness_prime_retries_a_transient_database_failure(monkeypatch) -> None:
    calls = 0

    async def check(*_args) -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ["database_or_schema_unavailable"]
        return []

    monkeypatch.setattr("app.storage.schema.runtime_readiness_failures", check)
    probe = RuntimeReadinessProbe(cache_seconds=5, timeout_seconds=0.5)

    failures = await probe.prime(
        Settings(_env_file=None),
        object(),  # type: ignore[arg-type]
        timeout_seconds=1,
    )

    assert failures == []
    assert calls == 2
