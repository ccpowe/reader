"""Control-plane authentication and stable per-task capabilities."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import stat
import tempfile
from pathlib import Path

CAPABILITY_KEY_BYTES = 32
CAPABILITY_NONCE_BYTES = 32
_CAPABILITY_DOMAIN = b"reader-browser-task-capability-v1\0"


class AuthenticationError(ValueError):
    """A control-plane credential is absent or invalid."""


def read_secret(path: str | Path, *, minimum_bytes: int = 32) -> bytes:
    """Read a runtime secret without accepting blank or weak values."""
    value = Path(path).read_bytes().strip()
    if len(value) < minimum_bytes:
        raise AuthenticationError(f"secret must contain at least {minimum_bytes} bytes")
    return value


def verify_bearer(provided: str | None, expected: bytes) -> None:
    """Validate the stable Worker-to-manager bearer in constant time."""
    if not provided or not provided.startswith("Bearer "):
        raise AuthenticationError("missing bearer token")
    candidate = provided.removeprefix("Bearer ").encode()
    if not hmac.compare_digest(candidate, expected):
        raise AuthenticationError("invalid bearer token")


def decode_capability_nonce(value: str) -> bytes:
    """Decode an unpadded base64url 32-byte idempotency nonce."""
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise AuthenticationError("invalid capability nonce") from exc
    if len(raw) != CAPABILITY_NONCE_BYTES:
        raise AuthenticationError("capability nonce must contain 32 bytes")
    return raw


def generate_capability_nonce() -> str:
    return (
        base64.urlsafe_b64encode(secrets.token_bytes(CAPABILITY_NONCE_BYTES)).rstrip(b"=").decode()
    )


def load_or_create_capability_key(path: str | Path) -> bytes:
    """Atomically initialize the controller-only HMAC key on its state volume."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = target.read_bytes()
    except FileNotFoundError:
        value = secrets.token_bytes(CAPABILITY_KEY_BYTES)
        descriptor, temporary = tempfile.mkstemp(prefix=".capability-key-", dir=target.parent)
        temporary_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, value)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                value = target.read_bytes()
            else:
                directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
    if len(value) != CAPABILITY_KEY_BYTES:
        raise AuthenticationError("capability key has an invalid length")
    metadata = target.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise AuthenticationError("capability key must be a controller-only regular file")
    return value


def remove_stale_capability_key_temps(directory: str | Path) -> None:
    """Remove controller-owned atomic-write leftovers while holding its singleton lock."""
    for candidate in Path(directory).glob(".capability-key-*"):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and not metadata.st_mode & 0o077
        ):
            candidate.unlink()


def derive_task_capability(
    key: bytes,
    *,
    task_id: str,
    request_id: str,
    owner_job_id: str,
    capability_nonce: str,
) -> str:
    """Derive a stable opaque capability without storing it in SQLite."""
    nonce = decode_capability_nonce(capability_nonce)
    fields = (task_id, request_id, owner_job_id)
    encoded = b"\0".join(field.encode() for field in fields)
    digest = hmac.digest(key, _CAPABILITY_DOMAIN + encoded + b"\0" + nonce, "sha256")
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def capability_hash(capability: str) -> str:
    """Return the non-secret value injected into the task adapter."""
    return hashlib.sha256(capability.encode()).hexdigest()


def verify_task_capability(provided: str | None, expected: str) -> None:
    if not provided or not hmac.compare_digest(provided, expected):
        raise AuthenticationError("invalid task capability")
