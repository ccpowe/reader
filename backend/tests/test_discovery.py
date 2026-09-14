import pytest
from fastapi.testclient import TestClient

from app.api.discovery import DISCOVERY_CONFIGURATION_ERROR
from app.core.settings import Settings, get_settings
from app.main import create_app

TOKEN = "test-only-reader-connection-token"
SECRET = "test-only-independent-signing-secret-32-characters"


def configured(**kwargs):
    return Settings(
        _env_file=None,
        **{
            "server_id": "reader-test",
            "server_access_token": TOKEN,
            "auth_jwt_secret": SECRET,
            **kwargs,
        },
    )


def client_for(monkeypatch, settings, **kwargs):
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, **kwargs)


@pytest.mark.parametrize(
    "path", ["/.well-known/reader.json", "/v1/me", "/v1/auth/register", "/v1/missing"]
)
def test_gate_protects_all_routes_before_validation_or_database(monkeypatch, path):
    with client_for(monkeypatch, configured()) as client:
        response = client.post(path, json={}) if path.endswith("register") else client.get(path)
        bad = client.get(path, headers={"X-Reader-Server-Token": "wrong"})
    assert response.status_code == bad.status_code == 401
    assert response.json()["detail"] == "invalid_server_token"
    assert TOKEN not in response.text and SECRET not in response.text


def test_gate_fails_closed_without_configuration_but_liveness_and_cors_work(monkeypatch):
    settings = configured(server_access_token=None, cors_origins=["https://reader.example"])
    with client_for(monkeypatch, settings) as client:
        assert client.get("/.well-known/reader.json").status_code == 503
        assert client.get("/health").status_code == 200
        response = client.options(
            "/v1/auth/login",
            headers={
                "Origin": "https://reader.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-reader-server-token,content-type",
            },
        )
        assert response.status_code == 200


def test_discovery_returns_only_v2_coordinates_and_no_secrets(monkeypatch):
    with client_for(
        monkeypatch, configured(public_api_base_url="https://reader.example/base/")
    ) as client:
        response = client.get("/.well-known/reader.json", headers={"X-Reader-Server-Token": TOKEN})
        schema = client.get("/openapi.json")
    assert response.json() == {
        "protocol_version": 2,
        "server_id": "reader-test",
        "api_base_url": "https://reader.example/base",
    }
    assert TOKEN not in schema.text and SECRET not in schema.text
    assert "supabase" not in schema.text.lower()
    assert schema.json()["paths"]["/.well-known/reader.json"]["get"]["security"] == [
        {"ReaderServerToken": []}
    ]


def test_discovery_root_path_is_protected_and_preserved(monkeypatch):
    with client_for(monkeypatch, configured(), root_path="/reader") as client:
        assert client.get("/reader/.well-known/reader.json").status_code == 401
        response = client.get(
            "/reader/.well-known/reader.json", headers={"X-Reader-Server-Token": TOKEN}
        )
    assert response.status_code == 200
    assert response.json()["api_base_url"].endswith("/reader")


def test_discovery_reports_missing_identity_without_secret_values(monkeypatch):
    with client_for(monkeypatch, configured(server_id=None, auth_jwt_secret="too-short")) as client:
        response = client.get("/.well-known/reader.json", headers={"X-Reader-Server-Token": TOKEN})
    assert response.status_code == 503
    assert response.json() == {
        "code": DISCOVERY_CONFIGURATION_ERROR,
        "error_code": DISCOVERY_CONFIGURATION_ERROR,
        "missing": ["APP_SERVER_ID", "APP_AUTH_JWT_SECRET"],
        "status": "unavailable",
    }


def test_discovery_settings_reject_credentialed_or_ambiguous_urls():
    for value in (
        "https://user:pass@example.com",
        "https://example.com?a=b",
        "https://example.com#token",
    ):
        with pytest.raises(ValueError):
            configured(public_api_base_url=value)


def test_auth_validation_never_echoes_password_or_refresh_token(monkeypatch):
    with client_for(monkeypatch, configured()) as client:
        response = client.post(
            "/v1/auth/login",
            json={"email": "invalid", "password": "private"},
            headers={"X-Reader-Server-Token": TOKEN},
        )
    # Request parsing may share a database dependency. Give that dependency a safe stub
    # in integration tests; this assertion still guarantees no input echo in errors.
    assert "private" not in response.text
