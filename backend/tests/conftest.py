"""Keep runtime credentials and local dotenv files out of unit-test defaults."""

import os

import pytest
from pydantic import AliasChoices

from app.core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_runtime_settings(monkeypatch: pytest.MonkeyPatch):
    # Third-party imports may load .env into os.environ during test collection,
    # so _env_file=None alone does not isolate a Settings instance.
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
