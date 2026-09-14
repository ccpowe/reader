"""Bound memory-hard password work off the event loop."""

import secrets

import anyio
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher


class PasswordService:
    def __init__(self) -> None:
        self._hash = PasswordHash(
            (
                Argon2Hasher(time_cost=2, memory_cost=19456, parallelism=1),
                BcryptHasher(),
            )
        )
        self._limiter = anyio.CapacityLimiter(2)
        self._dummy_hash: str | None = None

    async def hash(self, password: str) -> str:
        return await anyio.to_thread.run_sync(self._hash.hash, password, limiter=self._limiter)

    async def verify(self, password: str, hashed: str | None) -> tuple[bool, str | None]:
        # Unknown email follows the same expensive path, without exposing account existence.
        if hashed is None:
            if self._dummy_hash is None:
                self._dummy_hash = await self.hash(secrets.token_urlsafe(32))
            await self._verify(password, self._dummy_hash)
            return False, None
        return await self._verify(password, hashed)

    async def _verify(self, password: str, hashed: str) -> tuple[bool, str | None]:
        def verify() -> tuple[bool, str | None]:
            try:
                return self._hash.verify_and_update(password, hashed)
            except (ValueError, TypeError, UnknownHashError):
                return False, None

        return await anyio.to_thread.run_sync(verify, limiter=self._limiter)
