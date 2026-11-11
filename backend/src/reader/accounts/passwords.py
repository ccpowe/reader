"""Argon2 password hashing, run off the event loop with bounded concurrency."""

from __future__ import annotations

import secrets

import anyio
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

_HASHER = PasswordHash((Argon2Hasher(time_cost=2, memory_cost=19456, parallelism=1),))
_limiter: anyio.CapacityLimiter | None = None
_dummy_hash: str | None = None


def _get_limiter() -> anyio.CapacityLimiter:
    # Created lazily: a limiter must be made inside a running event loop.
    global _limiter
    if _limiter is None:
        _limiter = anyio.CapacityLimiter(2)
    return _limiter


async def hash_password(password: str) -> str:
    return await anyio.to_thread.run_sync(_HASHER.hash, password, limiter=_get_limiter())


async def verify_password(password: str, hashed: str | None) -> bool:
    """Constant-cost check: an unknown account still pays for one verification."""
    global _dummy_hash
    if hashed is None:
        if _dummy_hash is None:
            _dummy_hash = await hash_password(secrets.token_urlsafe(32))
        await _verify(password, _dummy_hash)
        return False
    return await _verify(password, hashed)


async def _verify(password: str, hashed: str) -> bool:
    def verify() -> bool:
        try:
            return _HASHER.verify(password, hashed)
        except (ValueError, TypeError, UnknownHashError):
            return False

    return await anyio.to_thread.run_sync(verify, limiter=_get_limiter())
