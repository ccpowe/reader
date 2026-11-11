"""The generated contract must describe what the server actually does."""

import json

import pytest

from reader.main import create_app


@pytest.fixture
def spec() -> dict:
    return create_app().openapi()


def _operations(spec: dict):
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            yield path, method, operation


def test_no_operation_uses_fastapis_default_validation_error(spec) -> None:
    assert "HTTPValidationError" not in json.dumps(spec)


def test_every_error_response_uses_the_error_shape(spec) -> None:
    for path, method, operation in _operations(spec):
        for status, response in operation["responses"].items():
            if status.startswith(("4", "5")):
                schema = response["content"]["application/json"]["schema"]
                assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, (path, method)


def test_signed_in_routes_require_both_tokens_together(spec) -> None:
    assert spec["paths"]["/me"]["get"]["security"] == [{"ReaderServerToken": [], "HTTPBearer": []}]


def test_discovery_requires_the_server_token(spec) -> None:
    operation = spec["paths"]["/.well-known/reader.json"]["get"]
    assert operation["security"] == [{"ReaderServerToken": []}]


def test_public_probes_declare_no_security(spec) -> None:
    for path in ("/health", "/ready", "/worker-ready"):
        assert "security" not in spec["paths"][path]["get"]


def test_avatar_version_is_bounded_to_the_database_integer(spec) -> None:
    parameters = spec["paths"]["/avatars/{user_id}/{version}"]["get"]["parameters"]
    version = next(parameter for parameter in parameters if parameter["name"] == "version")
    assert (version["schema"]["minimum"], version["schema"]["maximum"]) == (1, 2_147_483_647)
