"""Test isolation shared by every test: never inherit the developer's APP_* settings."""

import os

import pytest


@pytest.fixture(autouse=True)
def no_inherited_app_settings(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        if name.startswith("APP_"):
            monkeypatch.delenv(name)
