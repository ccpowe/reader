import pytest
from pydantic import ValidationError

from reader.core.settings import Settings

JWT = "j" * 40


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
    ],
)
def test_database_url_selects_the_async_driver(url: str, expected: str) -> None:
    assert Settings(database_url=url).database_url == expected


def test_missing_server_settings_names_everything_once() -> None:
    assert Settings().missing_server_settings() == [
        "APP_SERVER_ID",
        "APP_SERVER_ACCESS_TOKEN",
        "APP_AUTH_JWT_SECRET",
    ]
    complete = Settings(server_id="home", server_access_token="token", auth_jwt_secret=JWT)
    assert complete.missing_server_settings() == []


def test_jwt_secret_must_be_long_and_distinct_from_the_access_token() -> None:
    short = Settings(server_id="home", server_access_token="token", auth_jwt_secret="short")
    reused = Settings(server_id="home", server_access_token=JWT, auth_jwt_secret=JWT)

    assert short.missing_server_settings() == ["APP_AUTH_JWT_SECRET"]
    assert reused.missing_server_settings() == ["APP_AUTH_JWT_SECRET"]


def test_blank_server_id_counts_as_missing() -> None:
    assert Settings(server_id="   ").server_id is None


def test_public_base_url_rejects_credentials_and_query() -> None:
    assert str(Settings(public_api_base_url="https://reader.example/api").public_api_base_url)
    for bad in ("https://user:pw@reader.example", "https://reader.example/?x=1"):
        with pytest.raises(ValidationError):
            Settings(public_api_base_url=bad)


def test_scan_lease_must_outlive_the_scan_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(scan_timeout_seconds=120, scan_lease_seconds=140)
