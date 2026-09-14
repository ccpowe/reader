"""Keep runtime credentials and local dotenv files out of unit-test defaults."""

import os

import pytest
from pydantic import AliasChoices

from app.core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_runtime_settings(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    if (
        request.node.get_closest_marker("live_provider") is not None
        and os.environ.get("READER_RUN_LIVE_WEB_RULE_AGENT") == "1"
    ):
        # This separately opted-in test still requires the disposable PG guard.
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()
        return
    # Crawl4AI loads .env into os.environ when imported during test collection.
    # _env_file=None alone therefore does not isolate a Settings instance.
    aliases = {
        choice
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, AliasChoices)
        for choice in field.validation_alias.choices
        if isinstance(choice, str)
    }
    for name in list(os.environ):
        if name.startswith("APP_") or name in aliases:
            monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
