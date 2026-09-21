import pytest
from pydantic import ValidationError

from app.core.settings import Settings
from app.storage import database


def test_web_browser_defaults_to_lightpanda_without_requiring_installation():
    settings = Settings(_env_file=None)
    assert settings.ingestion_browser_engine == "lightpanda"
    assert settings.ingestion_lightpanda_executable_path is None
    assert settings.browser_task_heartbeat_seconds == 15


@pytest.mark.parametrize("legacy", [False, True])
def test_shared_browser_environment_names_and_legacy_aliases(monkeypatch, legacy):
    prefix = "APP_WEB_RULE_AGENT" if legacy else "APP_INGESTION"
    monkeypatch.setenv(prefix + "_BROWSER_ENGINE", "lightpanda")
    monkeypatch.setenv(prefix + "_LIGHTPANDA_EXECUTABLE_PATH", "/opt/reader/lightpanda")
    settings = Settings(_env_file=None)
    assert settings.ingestion_browser_engine == "lightpanda"
    assert settings.ingestion_lightpanda_executable_path == "/opt/reader/lightpanda"


def test_new_browser_environment_names_take_precedence_over_legacy(monkeypatch):
    monkeypatch.setenv("APP_WEB_RULE_AGENT_BROWSER_ENGINE", "chromium")
    monkeypatch.setenv("APP_WEB_RULE_AGENT_LIGHTPANDA_EXECUTABLE_PATH", "/old/browser")
    monkeypatch.setenv("APP_INGESTION_BROWSER_ENGINE", "lightpanda")
    monkeypatch.setenv("APP_INGESTION_LIGHTPANDA_EXECUTABLE_PATH", "/new/browser")
    settings = Settings(_env_file=None)
    assert settings.ingestion_browser_engine == "lightpanda"
    assert settings.ingestion_lightpanda_executable_path == "/new/browser"


def test_browser_constructor_aliases_and_new_name_precedence():
    legacy = Settings(
        _env_file=None,
        web_rule_agent_browser_engine="lightpanda",
        web_rule_agent_lightpanda_executable_path="/old/browser",
    )
    assert legacy.ingestion_lightpanda_executable_path == "/old/browser"
    current = Settings(
        _env_file=None,
        ingestion_browser_engine="lightpanda",
        ingestion_lightpanda_executable_path="/new/browser",
        web_rule_agent_browser_engine="chromium",
        web_rule_agent_lightpanda_executable_path="/old/browser",
    )
    assert current.ingestion_lightpanda_executable_path == "/new/browser"


@pytest.mark.parametrize(
    "key", ["APP_INGESTION_BROWSER_ENGINE", "APP_WEB_RULE_AGENT_BROWSER_ENGINE"]
)
def test_explicit_chromium_environment_configuration_is_rejected(monkeypatch, key):
    monkeypatch.setenv(key, "chromium")
    with pytest.raises(ValidationError, match="lightpanda"):
        Settings(_env_file=None)


@pytest.mark.parametrize("key", ["ingestion_browser_engine", "web_rule_agent_browser_engine"])
def test_explicit_chromium_constructor_configuration_is_rejected(key):
    with pytest.raises(ValidationError, match="lightpanda"):
        Settings(_env_file=None, **{key: "chromium"})


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


def test_rule_agent_keeps_token_and_model_allowances_and_retires_other_job_caps() -> None:
    settings = Settings(
        _env_file=None,
        web_rule_agent_max_model_calls=1,
        web_rule_agent_max_tool_calls=1,
        web_rule_agent_max_validation_calls=1,
        web_rule_agent_max_attempts=1,
        web_rule_agent_max_goal_resumptions=0,
        web_rule_agent_timeout_seconds=30,
        web_rule_agent_max_context_bytes=4096,
        web_rule_agent_recursion_limit=8,
    )
    assert settings.web_rule_agent_max_total_tokens == 3_000_000
    assert settings.web_rule_agent_max_model_calls == 1
    assert Settings(_env_file=None).web_rule_agent_max_model_calls == 80
    for removed in (
        "max_tool_calls",
        "max_validation_calls",
        "max_attempts",
        "max_goal_resumptions",
        "timeout_seconds",
        "max_context_bytes",
        "recursion_limit",
    ):
        assert not hasattr(settings, f"web_rule_agent_{removed}")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, web_rule_agent_max_total_tokens=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, web_rule_agent_max_model_calls=0)


def test_cli_configuration_snapshot_never_reads_local_binaries(monkeypatch):
    from pathlib import Path

    from app.translation.engines import engine_descriptor
    from app.web_rule_agent.configuration import rule_agent_snapshot

    def forbidden(*args, **kwargs):
        raise AssertionError("API snapshot must not read executable contents")

    monkeypatch.setenv("APP_WEB_RULE_AGENT_CLI_EXECUTABLE_PATH", "/not-installed/agent-browser")
    settings = Settings(_env_file=None)
    assert settings.web_rule_agent_cli_executable_path == "/not-installed/agent-browser"
    descriptor = engine_descriptor(settings, "deepseek-v4-flash")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    snapshot = rule_agent_snapshot(settings, descriptor)
    assert snapshot["execution_options"]["cli_executable_path"] == "/not-installed/agent-browser"
    assert "cli_execution_identity" not in snapshot
    changed = rule_agent_snapshot(
        settings.model_copy(
            update={"web_rule_agent_cli_executable_path": "/another/agent-browser"}
        ),
        descriptor,
    )
    assert changed["config_fingerprint"] != snapshot["config_fingerprint"]


def test_remote_browser_snapshot_excludes_local_paths_and_controller_secret() -> None:
    from app.translation.engines import engine_descriptor
    from app.web_rule_agent.configuration import rule_agent_snapshot

    settings = Settings(
        _env_file=None,
        browser_controller_url="http://reader-browser-manager:8091",
        browser_controller_token="manager-secret-that-must-not-be-snapshotted",
        ingestion_lightpanda_executable_path="/local/lightpanda",
        web_rule_agent_cli_executable_path="/local/agent-browser",
    )
    snapshot = rule_agent_snapshot(settings, engine_descriptor(settings, "deepseek-v4-flash"))

    assert snapshot["browser_runtime"] == "container-task-v1"
    assert "ingestion_lightpanda_executable_path" not in snapshot
    assert "cli_executable_path" not in snapshot["execution_options"]
    assert "browser_controller_token" not in settings.model_dump()
    assert "manager-secret" not in repr(settings.model_dump())


def test_browser_controller_token_sources_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        Settings(
            _env_file=None,
            browser_controller_token="direct-secret",
            browser_controller_token_file="/run/secrets/browser-token",
        )


def test_browser_controller_token_file_is_excluded_from_settings_dump() -> None:
    settings = Settings(
        _env_file=None,
        browser_controller_token_file="/run/secrets/browser-token",
    )

    assert "browser_controller_token_file" not in settings.model_dump()
    assert "/run/secrets/browser-token" not in repr(settings.model_dump())
