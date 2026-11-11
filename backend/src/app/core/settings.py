"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Values intentionally remain optional at process construction time so the
    API can expose `/health` before PostgreSQL is configured. Endpoints validate
    the complete set of settings they need. In
    particular, the public discovery document reports every missing Reader
    identity setting together instead of failing on the first one.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        populate_by_name=True,
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=list)

    # Reader server identity is deliberately stable across restarts.  It has
    # no generated default: deployments must provide APP_SERVER_ID and the
    # discovery route returns a stable configuration error when it is absent.
    server_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S(?:.*\S)?$",
    )
    public_api_base_url: AnyHttpUrl | None = None
    server_access_token: SecretStr | None = None
    auth_jwt_secret: SecretStr | None = None
    auth_access_token_seconds: int = Field(default=900, ge=60, le=3600)
    auth_refresh_token_days: int = Field(default=30, ge=1, le=90)
    auth_password_attempts_per_minute: int = Field(default=20, ge=1, le=100)
    database_url: str | None = None
    database_ssl: bool = True
    database_pool_pre_ping: bool = True
    database_pool_size: int = Field(default=3, ge=1, le=50)
    database_pool_max_overflow: int = Field(default=0, ge=0, le=50)
    database_pool_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    trust_proxy_fake_ip: bool = False
    auth_rate_limit_requests_per_minute: int = Field(default=300, ge=10, le=10_000)
    readiness_startup_timeout_seconds: float = Field(default=30.0, ge=0.5, le=30.0)
    readiness_timeout_seconds: float = Field(default=2.0, ge=0.5, le=5.0)
    readiness_cache_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    worker_poll_interval_seconds: int = Field(default=60, ge=10, le=3600)
    ingestion_max_items_per_sync: int = Field(default=40, ge=1, le=50)
    ingestion_raw_items_per_sync: int = Field(default=120, ge=10, le=500)
    ingestion_max_pages_per_sync: int = Field(default=5, ge=1, le=20)
    ingestion_max_response_bytes: int = Field(
        default=5 * 1024 * 1024, ge=256 * 1024, le=20 * 1024 * 1024
    )
    ingestion_dns_timeout_seconds: float = Field(default=3.0, ge=0.5, le=10.0)
    ingestion_connect_attempt_timeout_seconds: float = Field(default=1.5, ge=0.25, le=5.0)
    ingestion_source_timeout_seconds: int = Field(default=120, ge=10, le=600)
    ingestion_source_concurrency: int = Field(default=4, ge=1, le=20)
    ingestion_candidate_batch_size: int = Field(default=50, ge=1, le=100)
    ingestion_scan_lease_seconds: int = Field(default=180, ge=30, le=900)
    ingestion_candidate_lease_seconds: int = Field(default=180, ge=30, le=900)
    ranking_refresh_lease_seconds: int = Field(default=240, ge=180, le=600)
    ranking_manual_refresh_cooldown_seconds: int = Field(default=60, ge=10, le=3600)
    ranking_snapshot_inactive_ttl_days: int = Field(default=7, ge=1, le=90)
    reddit_subscription_limit_per_user: int = Field(default=20, ge=1, le=100)
    ranking_provider_refreshes_per_minute: int = Field(default=30, ge=1, le=300)
    reddit_ranking_refreshes_per_minute: int = Field(default=1, ge=1, le=60)
    ranking_worker_concurrency: int = Field(default=4, ge=1, le=32)
    ranking_worker_cycle_timeout_seconds: int = Field(default=120, ge=10, le=900)
    translation_default_engine_id: str = Field(
        default="deepseek-v4-flash", min_length=1, max_length=64
    )
    translation_default_target_locale: str = Field(default="zh-CN", min_length=2, max_length=16)
    translation_prompt_version: str = "v2-caption-context"
    # Kept for the bounded ``run_once`` command. Long-running workers use the
    # provider-sized lane limits below so a large claim cannot hide new demand.
    translation_batch_size: int = Field(default=50, ge=1, le=100)
    translation_foreground_batch_size: int = Field(default=12, ge=1, le=24)
    translation_background_batch_size: int = Field(default=24, ge=1, le=50)
    translation_lease_seconds: int = Field(default=120, ge=30, le=900)
    translation_max_attempts: int = Field(default=8, ge=1, le=20)
    translation_context_cache_seconds: int = Field(default=30, ge=0, le=300)
    translation_interactive_timeout_seconds: float = Field(default=8.0, ge=1.0, le=12.0)
    translation_interactive_max_items_per_batch: int = Field(default=12, ge=1, le=24)
    translation_interactive_max_chars_per_batch: int = Field(default=5_000, ge=1_000, le=12_000)
    translation_interactive_max_concurrency: int = Field(default=2, ge=1, le=4)
    translation_realtime_wait_seconds: float = Field(default=5.0, ge=1.0, le=12.0)
    translation_realtime_max_concurrency: int = Field(default=16, ge=1, le=64)
    translation_idle_poll_seconds: int = Field(default=30, ge=5, le=300)
    translation_listener_fallback_poll_seconds: int = Field(default=2, ge=1, le=30)
    translation_cleanup_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    translation_llm_max_items_per_request: int = Field(default=24, ge=1, le=100)
    translation_llm_max_chars_per_request: int = Field(default=12_000, ge=1_000, le=100_000)
    translation_provider_max_concurrency: int = Field(default=4, ge=1, le=16)
    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "APP_DEEPSEEK_API_KEY",
            "DEEPSEEK_API_KEY",
            # Temporary compatibility for the misspelled development variable.
            "DEEPSEEL_API_KEY",
        ),
    )
    deepseek_api_base: AnyHttpUrl = "https://api.deepseek.com"
    deepseek_model: str = Field(
        default="deepseek-flash",
        min_length=1,
        max_length=160,
        validation_alias=AliasChoices("APP_DEEPSEEK_MODEL", "DEEPSEEK_MODEL"),
    )
    deepseek_request_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )
    openrouter_api_base: AnyHttpUrl = "https://openrouter.ai/api/v1"
    openrouter_model: str = Field(
        default="minimax/minimax-m3",
        min_length=1,
        max_length=160,
        validation_alias=AliasChoices("APP_OPENROUTER_MODEL", "OPENROUTER_MODEL"),
    )
    openrouter_request_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)

    # Explicit opt-in: never consume a developer's local subscription implicitly.
    codex_subscription_auth_file: Path | None = None
    codex_subscription_model: str = Field(default="gpt-6-luna", min_length=1, max_length=160)
    codex_subscription_request_timeout_seconds: float = Field(default=60.0, ge=1.0, le=120.0)

    @field_validator(
        "deepseek_model", "openrouter_model", "codex_subscription_model", mode="before"
    )
    @classmethod
    def normalize_translation_model(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    scweet_service_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_SCWEET_SERVICE_URL", "SCWEET_SERVICE_URL"),
    )
    scweet_service_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_SCWEET_SERVICE_TOKEN", "SCWEET_SERVICE_TOKEN"),
    )
    youtube_data_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_YOUTUBE_DATA_API_KEY", "YOUTUBE_DATA_API_KEY"),
    )
    youtube_daily_quota_soft_limit: int = Field(default=8_000, ge=0, le=10_000)

    # Backend-only GraphQL bearer credential; never expose through API settings.
    product_hunt_token: SecretStr | None = Field(default=None, exclude=True)

    # Translation limits are durable, cross-process PostgreSQL budgets. Keep
    # the names explicit so operators can distinguish the per-user miss
    # budget from the provider-wide budget. A zero value is useful for a
    # deliberate maintenance lock; there is no ``unlimited`` setting.
    translation_quota_requests_per_minute: int = Field(
        default=30,
        ge=0,
        le=1_000_000,
        validation_alias=AliasChoices(
            "APP_TRANSLATION_QUOTA_REQUESTS_PER_MINUTE",
            "APP_TRANSLATION_REQUESTS_PER_MINUTE",
        ),
    )
    translation_quota_user_miss_chars_per_minute: int = Field(
        default=120_000,
        ge=0,
        le=100_000_000,
        validation_alias=AliasChoices(
            "APP_TRANSLATION_QUOTA_USER_MISS_CHARS_PER_MINUTE",
            "APP_TRANSLATION_USER_MISS_CHARS_PER_MINUTE",
        ),
    )
    translation_quota_global_miss_chars_per_minute: int = Field(
        default=500_000,
        ge=0,
        le=1_000_000_000,
        validation_alias=AliasChoices(
            "APP_TRANSLATION_QUOTA_GLOBAL_MISS_CHARS_PER_MINUTE",
            "APP_TRANSLATION_GLOBAL_MISS_CHARS_PER_MINUTE",
        ),
    )

    @field_validator("database_url")
    @classmethod
    def normalise_async_database_url(cls, value: str | None) -> str | None:
        """Accept a standard PostgreSQL URL and select SQLAlchemy's async driver."""
        if value is None:
            return None
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
        return value

    @field_validator("server_id", mode="before")
    @classmethod
    def normalise_server_id(cls, value: str | None) -> str | None:
        """Trim deployment whitespace while letting blank IDs report 503."""
        if value is None:
            return None
        if isinstance(value, str):
            normalised = value.strip()
            return normalised or None
        return value

    @field_validator("public_api_base_url")
    @classmethod
    def validate_public_http_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        """Keep discovery URLs as origin/base URLs, never credentialed links.

        A path prefix is valid (for example ``https://reader.example/api``),
        while query and fragment components would make route resolution
        ambiguous and userinfo would put credentials into a public contract.
        """
        if value is None:
            return None
        if value.username or value.password:
            raise ValueError("public discovery URLs must not contain userinfo")
        if value.query or value.fragment:
            raise ValueError("public discovery URLs must not contain query or fragment")
        return value

    @model_validator(mode="after")
    def validate_source_scan_lease_budget(self) -> Settings:
        minimum_lease = self.ingestion_source_timeout_seconds + 30
        if self.ingestion_scan_lease_seconds < minimum_lease:
            raise ValueError(
                "ingestion_scan_lease_seconds must be at least "
                "ingestion_source_timeout_seconds + 30"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for the current process."""
    return Settings()
