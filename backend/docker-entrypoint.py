#!/usr/bin/env python3
"""Load only explicitly granted file secrets, then exec a Reader process."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from urllib.parse import quote


def _read_secret(variable: str, *, allow_empty: bool = False) -> str | None:
    path_value = os.environ.pop(f"{variable}_FILE", None)
    if path_value is None:
        return None
    path = Path(path_value)
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
            raise RuntimeError(f"{variable}_FILE must be a non-writable regular file")
        value = os.read(descriptor, 1024 * 1024).decode("utf-8").strip()
    finally:
        os.close(descriptor)
    if not value and not allow_empty:
        raise RuntimeError(f"{variable}_FILE must not be empty")
    return value


def _export(variable: str, *, allow_empty: bool = False) -> None:
    if variable in os.environ:
        raise RuntimeError(f"{variable} and {variable}_FILE are mutually exclusive")
    value = _read_secret(variable, allow_empty=allow_empty)
    if value:
        os.environ[variable] = value


def _configure_database() -> None:
    password = _read_secret("APP_DATABASE_PASSWORD")
    if password is None:
        return
    if "APP_DATABASE_URL" in os.environ:
        raise RuntimeError("APP_DATABASE_URL and APP_DATABASE_PASSWORD_FILE are mutually exclusive")
    user = os.environ.pop("APP_DATABASE_USER", "reader")
    host = os.environ.pop("APP_DATABASE_HOST", "postgres")
    port = os.environ.pop("APP_DATABASE_PORT", "5432")
    name = os.environ.pop("APP_DATABASE_NAME", "reader")
    os.environ["APP_DATABASE_URL"] = (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@"
        f"{host}:{port}/{quote(name, safe='')}"
    )


def main() -> None:
    _configure_database()
    _export("APP_SERVER_ACCESS_TOKEN")
    _export("APP_AUTH_JWT_SECRET")
    _export("APP_DEEPSEEK_API_KEY", allow_empty=True)
    _export("APP_OPENROUTER_API_KEY", allow_empty=True)
    _export("APP_YOUTUBE_DATA_API_KEY", allow_empty=True)
    _export("APP_SCWEET_SERVICE_TOKEN")
    if len(sys.argv) < 2:
        raise RuntimeError("Reader container command is required")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
