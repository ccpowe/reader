import pytest
from pydantic import ValidationError

from app.core.settings import Settings
from app.storage import database


def test_product_hunt_token_is_optional_backend_only_and_excluded_from_settings_dump(monkeypatch):
    monkeypatch.delenv("APP_PRODUCT_HUNT_TOKEN", raising=False)
    assert Settings(_env_file=None).product_hunt_token is None
    monkeypatch.setenv("APP_PRODUCT_HUNT_TOKEN", "test-only-secret")
    settings = Settings(_env_file=None)
    assert settings.product_hunt_token.get_secret_value() == "test-only-secret"
    assert "product_hunt_token" not in settings.model_dump()
    assert "test-only-secret" not in repr(settings)














def test_settings_accepts_standard_supabase_postgres_url() -> None:
    settings = Settings(database_url="postgresql://postgres:password@example.com:5432/postgres")

    assert (
        settings.database_url == "postgresql+asyncpg://postgres:password@example.com:5432/postgres"
    )


def test_database_ssl_is_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("APP_DATABASE_SSL", raising=False)
    assert Settings(_env_file=None).database_ssl is True


def test_database_pool_pre_pings_by_default_and_can_opt_out(monkeypatch) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_create_engine(url, **options):
        captured.update({"url": url, **options})
        return sentinel

    monkeypatch.setattr(database, "create_async_engine", fake_create_engine)
    settings = Settings(database_url="postgresql://user:secret@example.com/database")

    assert database.build_engine(settings) is sentinel
    assert captured["pool_pre_ping"] is True
    assert captured["pool_use_lifo"] is True
    assert captured["pool_size"] == 3
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == 5.0

    captured.clear()
    settings.database_pool_pre_ping = False
    assert database.build_engine(settings) is sentinel
    assert captured["pool_pre_ping"] is False


def test_database_pool_budget_can_be_configured_without_unbounded_overflow(monkeypatch) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_create_engine(url, **options):
        captured.update({"url": url, **options})
        return sentinel

    monkeypatch.setattr(database, "create_async_engine", fake_create_engine)
    settings = Settings(
        database_url="postgresql://user:secret@example.com/database",
        database_pool_size=4,
        database_pool_max_overflow=1,
        database_pool_timeout_seconds=2.5,
    )

    assert database.build_engine(settings) is sentinel
    assert captured["pool_size"] == 4
    assert captured["max_overflow"] == 1
    assert captured["pool_timeout"] == 2.5

    captured.clear()
    assert database.build_engine(settings, autocommit=True, pool_size=2) is sentinel
    assert captured["pool_size"] == 2
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == 2.5


def test_ingestion_candidate_cap_is_backend_only_and_bounded() -> None:
    assert Settings().ingestion_max_items_per_sync == 40
    with pytest.raises(ValidationError):
        Settings(ingestion_max_items_per_sync=51)


def test_source_scan_lease_outlives_the_timeout_and_concurrency_is_bounded() -> None:
    settings = Settings()
    assert settings.ingestion_source_concurrency == 4
    assert settings.ingestion_scan_lease_seconds >= settings.ingestion_source_timeout_seconds + 30

    with pytest.raises(ValidationError, match=r"ingestion_source_timeout_seconds \+ 30"):
        Settings(
            ingestion_source_timeout_seconds=120,
            ingestion_scan_lease_seconds=149,
        )


def test_interactive_translation_timeout_stays_below_mobile_request_timeout() -> None:
    assert Settings().translation_interactive_timeout_seconds == 8.0
    assert Settings().translation_interactive_max_items_per_batch == 12
    assert Settings().translation_interactive_max_chars_per_batch == 5_000
    assert Settings().translation_interactive_max_concurrency == 2
    with pytest.raises(ValidationError):
        Settings(translation_interactive_timeout_seconds=13)


def test_translation_worker_lanes_use_provider_sized_claims() -> None:
    settings = Settings()

    assert settings.translation_foreground_batch_size == 12
    assert settings.translation_background_batch_size == 24
    assert settings.translation_provider_max_concurrency == 4
    assert settings.translation_realtime_wait_seconds == 5.0
    assert settings.translation_realtime_max_concurrency == 16
    # Realtime provider waits do not hold a database checkout, so these
    # independent concurrency budgets intentionally need not match.
    assert settings.database_pool_size == 3


def test_deepseek_is_default_and_accepts_temporary_misspelled_key_alias() -> None:
    settings = Settings(_env_file=None, DEEPSEEL_API_KEY="secret")

    assert settings.translation_default_engine_id == "deepseek-v4-flash"
    assert settings.deepseek_api_key is not None
    assert settings.deepseek_api_key.get_secret_value() == "secret"










def test_browser_controller_token_file_is_excluded_from_settings_dump() -> None:
    settings = Settings(
        _env_file=None,
        browser_controller_token_file="/run/secrets/browser-token",
    )

    assert "browser_controller_token_file" not in settings.model_dump()
    assert "/run/secrets/browser-token" not in repr(settings.model_dump())
