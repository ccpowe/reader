"""Reader identity transactions with serialized session-family rotation."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AUTH_ALGORITHM,
    AUTH_AUDIENCE,
    auth_issuer,
    invalid_credentials,
    signing_secret,
)
from app.core.passwords import PasswordService
from app.core.settings import Settings
from app.storage.auth_models import ReaderRefreshToken, ReaderSession, ReaderUser
from app.storage.models import Profile


def refresh_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, settings: Settings, passwords: PasswordService) -> None:
        self.settings = settings
        self.passwords = passwords

    def _new_tokens(self, db: AsyncSession, user: ReaderUser, family: ReaderSession) -> dict:
        now = datetime.now(UTC)
        expires_at = int(
            (now + timedelta(seconds=self.settings.auth_access_token_seconds)).timestamp()
        )
        access = jwt.encode(
            {
                "sub": str(user.id),
                "sid": str(family.id),
                "iat": int(now.timestamp()),
                "exp": expires_at,
                "iss": auth_issuer(self.settings),
                "aud": AUTH_AUDIENCE,
            },
            signing_secret(self.settings),
            algorithm=AUTH_ALGORITHM,
        )
        refresh = secrets.token_urlsafe(48)
        db.add(ReaderRefreshToken(token_hash=refresh_hash(refresh), session_id=family.id))
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_in": self.settings.auth_access_token_seconds,
            "expires_at": expires_at,
            "user": {"id": str(user.id), "email": user.email},
        }

    async def _new_session(self, db: AsyncSession, user: ReaderUser) -> dict:
        family = ReaderSession(
            id=uuid4(),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=self.settings.auth_refresh_token_days),
        )
        db.add(family)
        await db.flush()
        return self._new_tokens(db, user, family)

    async def register(
        self, db: AsyncSession, email: str, password: str, display_name: str | None
    ) -> dict:
        signing_secret(self.settings)
        hashed = await self.passwords.hash(password)
        user = ReaderUser(id=uuid4(), email=email, password_hash=hashed)
        try:
            db.add(Profile(id=user.id, display_name=display_name))
            await db.flush()
            db.add(user)
            await db.flush()
            result = await self._new_session(db, user)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Email is already registered.") from None
        return result

    async def login(self, db: AsyncSession, email: str, password: str) -> dict:
        signing_secret(self.settings)
        # All identity/session mutations lock the user first. This prevents an in-flight
        # old-password login from creating a session after a password reset has committed.
        user = (
            await db.execute(select(ReaderUser).where(ReaderUser.email == email).with_for_update())
        ).scalar_one_or_none()
        valid, replacement = await self.passwords.verify(
            password, user.password_hash if user else None
        )
        if not valid or user is None:
            raise invalid_credentials("Email or password is incorrect.")
        if replacement:
            user.password_hash = replacement
        result = await self._new_session(db, user)
        await db.commit()
        return result

    async def _lock_refresh(
        self, db: AsyncSession, token: str
    ) -> tuple[ReaderUser, ReaderSession, ReaderRefreshToken] | None:
        # Only the digest is persisted. Lookup gives immutable ownership; locks always
        # follow user -> family -> token to avoid deadlocks with password changes.
        ownership = (
            await db.execute(
                select(ReaderSession.user_id, ReaderSession.id)
                .join(ReaderRefreshToken, ReaderRefreshToken.session_id == ReaderSession.id)
                .where(ReaderRefreshToken.token_hash == refresh_hash(token))
            )
        ).one_or_none()
        if ownership is None:
            return None
        user = (
            await db.execute(
                select(ReaderUser).where(ReaderUser.id == ownership.user_id).with_for_update()
            )
        ).scalar_one_or_none()
        family = (
            await db.execute(
                select(ReaderSession).where(ReaderSession.id == ownership.id).with_for_update()
            )
        ).scalar_one_or_none()
        stored = (
            await db.execute(
                select(ReaderRefreshToken)
                .where(ReaderRefreshToken.token_hash == refresh_hash(token))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if user is None or family is None or stored is None:
            return None
        return user, family, stored

    async def refresh(self, db: AsyncSession, token: str) -> dict:
        signing_secret(self.settings)
        locked = await self._lock_refresh(db, token)
        if locked is None:
            raise invalid_credentials("Invalid or expired refresh token.")
        user, family, stored = locked
        now = datetime.now(UTC)
        if family.revoked_at is not None or family.expires_at <= now:
            raise invalid_credentials("Invalid or expired refresh token.")
        if stored.used_at is not None:
            family.revoked_at = now
            # Commit revocation before raising; rolling back here would leave stolen
            # descendants usable after replay detection.
            await db.commit()
            raise invalid_credentials("Refresh token reuse detected. Sign in again.")
        stored.used_at = now
        result = self._new_tokens(db, user, family)
        await db.commit()
        return result

    async def logout(self, db: AsyncSession, token: str) -> None:
        locked = await self._lock_refresh(db, token)
        if locked is not None:
            _, family, _ = locked
            family.revoked_at = datetime.now(UTC)
            await db.commit()

    async def change_credentials(
        self,
        db: AsyncSession,
        user_id: UUID,
        current_password: str,
        *,
        password: str | None = None,
        email: str | None = None,
    ) -> dict:
        signing_secret(self.settings)
        user = (
            await db.execute(select(ReaderUser).where(ReaderUser.id == user_id).with_for_update())
        ).scalar_one_or_none()
        valid, replacement = await self.passwords.verify(
            current_password, user.password_hash if user else None
        )
        if not valid or user is None:
            raise invalid_credentials("Current password is incorrect.")
        if password is not None:
            user.password_hash = await self.passwords.hash(password)
        elif replacement:
            user.password_hash = replacement
        if email is not None:
            user.email = email
        try:
            await db.execute(
                update(ReaderSession)
                .where(ReaderSession.user_id == user_id, ReaderSession.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )
            result = await self._new_session(db, user)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Email is already registered.") from None
        return result
