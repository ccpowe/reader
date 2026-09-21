"""Download and verify the pinned Scweet source archive during image builds."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if not members:
        raise RuntimeError("Scweet source archive is empty.")
    roots: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise RuntimeError("Scweet source archive contains an unsafe path.")
        if member.issym() or member.islnk():
            raise RuntimeError("Scweet source archive contains an unsupported link.")
        roots.add(path.parts[0])
    if len(roots) != 1:
        raise RuntimeError("Scweet source archive must have exactly one root directory.")
    return members


def fetch(url: str, expected_sha256: str, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="scweet-source-") as temporary:
        temporary_path = Path(temporary)
        archive_path = temporary_path / "scweet.tar.gz"
        digest = hashlib.sha256()
        with (
            urllib.request.urlopen(url, timeout=60) as response,
            archive_path.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != expected_sha256:
            raise RuntimeError("Scweet source archive checksum does not match the pinned value.")

        extracted_path = temporary_path / "extracted"
        extracted_path.mkdir()
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = _safe_members(archive)
            archive.extractall(extracted_path, members=members, filter="data")
        source_root = extracted_path / PurePosixPath(members[0].name).parts[0]
        if destination.exists():
            raise RuntimeError(f"Destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_root), destination)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: fetch_scweet.py URL SHA256 DESTINATION")
    fetch(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
