"""Runtime configuration, read from APP_* environment variables.

Every value is optional at construction time so `/health` works before the
deployment is fully configured; features check what they need when used.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
    )

    # Server identity and access
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=list)
    server_id: str | None = Field(default=None, min_length=1, max_length=128)
    public_api_base_url: AnyHttpUrl | None = None
    server_access_token: SecretStr | None = None

    # Accounts
    auth_jwt_secret: SecretStr | None = None
    auth_access_token_seconds: int = Field(default=900, ge=60, le=3600)
    auth_refresh_token_days: int = Field(default=30, ge=1, le=90)
    auth_password_attempts_per_minute: int = Field(default=20, ge=1, le=100)
    auth_rate_limit_requests_per_minute: int = Field(default=300, ge=10, le=10_000)

    # Database
    database_url: str | None = None
    database_ssl: bool = True
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_pool_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    readiness_timeout_seconds: float = Field(default=2.0, ge=0.5, le=10.0)

    # Source scanning
    worker_poll_interval_seconds: int = Field(default=60, ge=10, le=3600)
    scan_max_items: int = Field(default=120, ge=10, le=500)
    scan_initial_items: int = Field(default=10, ge=1, le=50)
    scan_max_response_bytes: int = Field(
        default=5 * 1024 * 1024, ge=256 * 1024, le=20 * 1024 * 1024
    )
    scan_timeout_seconds: int = Field(default=120, ge=10, le=600)
    scan_lease_seconds: int = Field(default=180, ge=30, le=900)
    scan_concurrency: int = Field(default=4, ge=1, le=20)
    scan_dns_timeout_seconds: float = Field(default=3.0, ge=0.5, le=10.0)
    scan_connect_timeout_seconds: float = Field(default=1.5, ge=0.25, le=5.0)
    articles_kept_per_source: int = Field(default=500, ge=50, le=10_000)
    scan_runs_kept_per_source: int = Field(default=20, ge=1, le=200)
    # Only for a local Mihomo/Clash fake-IP proxy; never in production.
    trust_proxy_fake_ip: bool = False

    # Boards (rankings)
    board_fetch_wait_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
    board_manual_refresh_cooldown_seconds: int = Field(default=60, ge=10, le=3600)
    board_snapshot_keep_days: int = Field(default=7, ge=1, le=90)
    board_items: int = Field(default=30, ge=10, le=100)

    # Translation
    translation_default_engine_id: str = Field(default="deepseek-v4-flash", max_length=64)
    translation_default_target_locale: str = Field(default="zh-CN", min_length=2, max_length=16)
    translation_prompt_version: str = "v3"
    translation_wait_seconds: float = Field(default=5.0, ge=1.0, le=15.0)
    translation_max_items_per_request: int = Field(default=24, ge=1, le=100)
    translation_max_chars_per_request: int = Field(default=12_000, ge=1_000, le=100_000)
    translation_max_concurrency: int = Field(default=4, ge=1, le=16)
    translation_chars_per_minute: int = Field(default=500_000, ge=0)
    translation_chars_per_day: int = Field(default=2_000_000, ge=0)
    translation_unused_keep_days: int = Field(default=30, ge=1, le=365)

    # Model providers
    deepseek_api_key: SecretStr | None = None
    deepseek_api_base: AnyHttpUrl = "https://api.deepseek.com"
    deepseek_model: str = Field(default="deepseek-flash", min_length=1, max_length=160)
    deepseek_request_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    openrouter_api_key: SecretStr | None = None
    openrouter_api_base: AnyHttpUrl = "https://openrouter.ai/api/v1"
    openrouter_model: str = Field(default="minimax/minimax-m3", min_length=1, max_length=160)
    openrouter_request_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    codex_subscription_auth_file: Path | None = None
    codex_subscription_model: str = Field(default="gpt-6-luna", min_length=1, max_length=160)
    codex_subscription_request_timeout_seconds: float = Field(default=60.0, ge=1.0, le=120.0)

    # Content providers
    scweet_service_url: AnyHttpUrl | None = None
    scweet_service_token: SecretStr | None = None
    youtube_data_api_key: SecretStr | None = None
    youtube_daily_quota: int = Field(default=8_000, ge=0, le=10_000)
    product_hunt_token: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, value: str | None) -> str | None:
        """Accept a standard PostgreSQL URL and select the asyncpg driver."""
        if value is None:
            return None
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value.removeprefix(prefix)
        return value

    @field_validator("server_id", mode="before")
    @classmethod
    def blank_server_id_is_missing(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("public_api_base_url")
    @classmethod
    def plain_base_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        """A discovery URL may carry a path prefix, never credentials or a query."""
        if value is not None and (
            value.username or value.password or value.query or value.fragment
        ):
            raise ValueError("public_api_base_url must be a plain base URL")
        return value

    @model_validator(mode="after")
    def lease_outlives_scan(self) -> Settings:
        if self.scan_lease_seconds < self.scan_timeout_seconds + 30:
            raise ValueError("scan_lease_seconds must be at least scan_timeout_seconds + 30")
        return self

    def missing_server_settings(self) -> list[str]:
        """Names (never values) of settings the API needs to accept clients."""
        missing = []
        if not self.server_id:
            missing.append("APP_SERVER_ID")
        token = self.server_access_token.get_secret_value() if self.server_access_token else ""
        if not token.strip():
            missing.append("APP_SERVER_ACCESS_TOKEN")
        secret = self.auth_jwt_secret.get_secret_value() if self.auth_jwt_secret else ""
        if len(secret) < 32 or secret == token:
            missing.append("APP_AUTH_JWT_SECRET")
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
