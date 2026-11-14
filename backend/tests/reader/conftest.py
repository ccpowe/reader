"""Test isolation for the `reader` package."""

import os

import pytest

from reader.core import settings as settings_module


@pytest.fixture(autouse=True)
def isolated_reader_settings(monkeypatch: pytest.MonkeyPatch):
    """Never read the developer's .env or APP_* variables in unit tests."""
    for name in list(os.environ):
        if name.startswith("APP_"):
            monkeypatch.delenv(name)
    monkeypatch.setitem(settings_module.Settings.model_config, "env_file", None)
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()
