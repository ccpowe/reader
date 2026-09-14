"""Configuration checks return names only, never secret values."""

from app.core.settings import Settings


def reader_configuration_missing(settings: Settings) -> list[str]:
    missing: list[str] = []
    if not settings.server_id:
        missing.append("APP_SERVER_ID")
    if (
        not settings.server_access_token
        or not settings.server_access_token.get_secret_value().strip()
    ):
        missing.append("APP_SERVER_ACCESS_TOKEN")
    if not settings.auth_jwt_secret or len(settings.auth_jwt_secret.get_secret_value()) < 32:
        missing.append("APP_AUTH_JWT_SECRET")
    elif settings.server_access_token and (
        settings.auth_jwt_secret.get_secret_value()
        == settings.server_access_token.get_secret_value()
    ):
        missing.append("APP_AUTH_JWT_SECRET")
    return missing


def runtime_configuration_missing(settings: Settings) -> list[str]:
    missing = reader_configuration_missing(settings)
    if not settings.database_url:
        missing.append("APP_DATABASE_URL")
    return missing
