"""Account use cases: registration, sessions, credentials, profile and avatar.

Locks are always taken in the order user → session → refresh token, so
concurrent logins, refreshes and password changes cannot deadlock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from reader.accounts.passwords import hash_password, verify_password
from reader.accounts.tokens import issue_access_token, new_refresh_token, refresh_token_hash
from reader.core.errors import AppError
from reader.core.settings import Settings
from reader.models import RefreshToken, Session, User


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int
    expires_at: int
    user_id: UUID
    email: str


@dataclass(frozen=True)
class Avatar:
    data: bytes
    content_type: str


def _invalid_login() -> AppError:
    return AppError(401, "invalid_credentials", "Email or password is incorrect.")


def _invalid_refresh() -> AppError:
    return AppError(401, "invalid_refresh_token", "Invalid or expired refresh token.")


def _wrong_password() -> AppError:
    # 403, not 401: the session itself is fine, so the client must not re-login.
    return AppError(403, "wrong_password", "The current password is incorrect.")


def _email_taken() -> AppError:
    return AppError(409, "email_taken", "This email is already registered.")


def _issue(db: AsyncSession, settings: Settings, user: User, session: Session) -> Tokens:
    access, expires_at = issue_access_token(settings, user.id, session.id)
    refresh = new_refresh_token()
    db.add(RefreshToken(token_hash=refresh_token_hash(refresh), session_id=session.id))
    return Tokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.auth_access_token_seconds,
        expires_at=expires_at,
        user_id=user.id,
        email=user.email,
    )


async def _new_session(db: AsyncSession, settings: Settings, user: User) -> Tokens:
    session = Session(
        id=uuid4(),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.auth_refresh_token_days),
    )
    db.add(session)
    await db.flush()
    return _issue(db, settings, user, session)


async def _lock_user(db: AsyncSession, **where) -> User | None:
    column, value = next(iter(where.items()))
    return await db.scalar(select(User).where(getattr(User, column) == value).with_for_update())


async def _revoke_all_sessions(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def register(
    db: AsyncSession, settings: Settings, email: str, password: str, display_name: str | None
) -> Tokens:
    user = User(id=uuid4(), email=email, password_hash=await hash_password(password))
    user.display_name = display_name
    try:
        db.add(user)
        await db.flush()
        tokens = await _new_session(db, settings, user)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _email_taken() from None
    return tokens


async def login(db: AsyncSession, settings: Settings, email: str, password: str) -> Tokens:
    # Locking the user keeps an old-password login from racing a password change.
    user = await _lock_user(db, email=email)
    if not await verify_password(password, user.password_hash if user else None) or user is None:
        raise _invalid_login()
    tokens = await _new_session(db, settings, user)
    await db.commit()
    return tokens


async def _lock_refresh(db: AsyncSession, token: str) -> tuple[User, Session, RefreshToken] | None:
    digest = refresh_token_hash(token)
    owner = (
        await db.execute(
            select(Session.user_id, Session.id)
            .join(RefreshToken, RefreshToken.session_id == Session.id)
            .where(RefreshToken.token_hash == digest)
        )
    ).one_or_none()
    if owner is None:
        return None
    user = await _lock_user(db, id=owner.user_id)
    session = await db.scalar(select(Session).where(Session.id == owner.id).with_for_update())
    stored = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == digest).with_for_update()
    )
    if user is None or session is None or stored is None:
        return None
    return user, session, stored


async def refresh(db: AsyncSession, settings: Settings, token: str) -> Tokens:
    """Swap a refresh token for new tokens; a reused token ends its session."""
    locked = await _lock_refresh(db, token)
    if locked is None:
        raise _invalid_refresh()
    user, session, stored = locked
    now = datetime.now(UTC)
    if session.revoked_at is not None or session.expires_at <= now:
        raise _invalid_refresh()
    if stored.used_at is not None:
        session.revoked_at = now
        # Commit before raising: a rollback would leave a stolen token family usable.
        await db.commit()
        raise AppError(401, "refresh_token_reused", "Refresh token reuse detected. Sign in again.")
    stored.used_at = now
    tokens = _issue(db, settings, user, session)
    await db.commit()
    return tokens


async def logout(db: AsyncSession, token: str) -> None:
    locked = await _lock_refresh(db, token)
    if locked is not None:
        locked[1].revoked_at = datetime.now(UTC)
        await db.commit()


async def change_credentials(
    db: AsyncSession,
    settings: Settings,
    user_id: UUID,
    current_password: str,
    *,
    new_password: str | None = None,
    new_email: str | None = None,
) -> Tokens:
    """Change email or password; every existing session ends and a new one starts."""
    user = await _lock_user(db, id=user_id)
    if user is None or not await verify_password(current_password, user.password_hash):
        raise _wrong_password()
    if new_password is not None:
        user.password_hash = await hash_password(new_password)
    if new_email is not None:
        user.email = new_email
    try:
        await _revoke_all_sessions(db, user.id)
        tokens = await _new_session(db, settings, user)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _email_taken() from None
    return tokens


async def reset_password(db: AsyncSession, email: str, new_password: str) -> UUID:
    """Operator recovery: set a password and end every session."""
    user = await _lock_user(db, email=email)
    if user is None:
        raise AppError(404, "user_not_found", "No user with this email.")
    user.password_hash = await hash_password(new_password)
    await _revoke_all_sessions(db, user.id)
    await db.commit()
    return user.id


async def get_user(db: AsyncSession, user_id: UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise AppError(401, "not_authenticated", "The account no longer exists.")
    return user


async def update_profile(db: AsyncSession, user_id: UUID, display_name: str | None) -> User:
    user = await get_user(db, user_id)
    user.display_name = display_name
    await db.commit()
    return user


async def set_avatar(db: AsyncSession, user_id: UUID, data: bytes, content_type: str) -> User:
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise AppError(401, "not_authenticated", "The account no longer exists.")
    user.avatar_data = data
    user.avatar_content_type = content_type
    user.avatar_version += 1
    await db.commit()
    return user


async def get_avatar(db: AsyncSession, user_id: UUID, version: int) -> Avatar:
    user = await db.scalar(
        select(User)
        .options(undefer(User.avatar_data))
        .where(User.id == user_id, User.avatar_version == version)
    )
    if user is None or user.avatar_data is None or user.avatar_content_type is None:
        raise AppError(404, "avatar_not_found", "No such avatar.")
    return Avatar(data=user.avatar_data, content_type=user.avatar_content_type)


async def delete_ended_sessions(db: AsyncSession) -> int:
    """Remove sessions that expired or were revoked over a day ago (tokens cascade)."""
    cutoff = datetime.now(UTC) - timedelta(days=1)
    result = await db.execute(
        delete(Session).where((Session.expires_at < cutoff) | (Session.revoked_at < cutoff))
    )
    await db.commit()
    return result.rowcount
