"""Operator-only password recovery; passwords never appear in CLI arguments."""

import argparse
import getpass
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.api.auth import normalize_email
from app.core.passwords import PasswordService
from app.storage.auth_models import ReaderSession, ReaderUser


def configure_parser(commands) -> None:
    users = commands.add_parser("users", help="manage Reader identities")
    actions = users.add_subparsers(dest="action", required=True)
    reset = actions.add_parser("reset-password", help="set a password and revoke all sessions")
    reset.add_argument("email", help="registered email address")


async def run_user_admin(session_factory, args: argparse.Namespace) -> dict:
    email = normalize_email(args.email)
    password = getpass.getpass("New password (8–128 characters): ")
    confirmation = getpass.getpass("Confirm new password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match.")
    if not 8 <= len(password) <= 128:
        raise ValueError("Password must contain 8–128 characters.")
    hashed = await PasswordService().hash(password)
    async with session_factory() as session:
        user = (
            await session.execute(
                select(ReaderUser).where(ReaderUser.email == email).with_for_update()
            )
        ).scalar_one_or_none()
        if user is None:
            raise ValueError("User not found.")
        user.password_hash = hashed
        await session.execute(
            update(ReaderSession)
            .where(ReaderSession.user_id == user.id, ReaderSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
        return {"user_id": str(user.id), "status": "password_reset", "sessions_revoked": True}
