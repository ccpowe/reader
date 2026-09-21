#!/usr/bin/env python3
"""Fetch one pinned agent-browser binary from its published npm archive."""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("usage: fetch_agent_browser.py URL ARCHIVE_SHA BINARY_SHA OUTPUT")
    url, archive_sha, binary_sha, output = sys.argv[1:]
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed build ARG
        archive = response.read(64 * 1024 * 1024 + 1)
    if len(archive) > 64 * 1024 * 1024:
        raise SystemExit("agent-browser archive exceeds the build limit")
    if hashlib.sha256(archive).hexdigest() != archive_sha:
        raise SystemExit("agent-browser archive checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        member = package.getmember("package/bin/agent-browser-linux-x64")
        if not member.isfile() or member.size > 64 * 1024 * 1024:
            raise SystemExit("agent-browser binary member is invalid")
        stream = package.extractfile(member)
        if stream is None:
            raise SystemExit("agent-browser binary member is missing")
        binary = stream.read(64 * 1024 * 1024 + 1)
    if hashlib.sha256(binary).hexdigest() != binary_sha:
        raise SystemExit("agent-browser binary checksum mismatch")
    Path(output).write_bytes(binary)


if __name__ == "__main__":
    main()
