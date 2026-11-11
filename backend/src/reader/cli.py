"""reader-admin: operator commands that run on the server, never over HTTP.

reader-admin users reset-password EMAIL

Commands not yet ported (codex-auth, translation-quota) are handed to the old
`app.admin.cli` until their modules move to `reader`.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from reader.accounts import service as accounts
from reader.accounts.api import normalize_email
from reader.core.db import build_engine, build_session_factory
from reader.core.errors import AppError
from reader.core.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reader-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    users = commands.add_parser("users", help="manage accounts")
    actions = users.add_subparsers(dest="action", required=True)
    reset = actions.add_parser("reset-password", help="set a password and end all sessions")
    reset.add_argument("email")
    return parser


def _read_new_password() -> str:
    # Passwords never appear in command-line arguments or shell history.
    password = getpass.getpass("New password (8–128 characters): ")
    if password != getpass.getpass("Confirm new password: "):
        raise ValueError("Passwords do not match.")
    if not 8 <= len(password) <= 128:
        raise ValueError("Password must contain 8–128 characters.")
    return password


async def _reset_password(email: str, password: str) -> dict:
    engine = build_engine(get_settings())
    try:
        async with build_session_factory(engine)() as db:
            user_id = await accounts.reset_password(db, email, password)
    finally:
        await engine.dispose()
    return {"user_id": str(user_id), "status": "password_reset", "sessions_revoked": True}


_NOT_YET_PORTED = frozenset({"codex-auth", "translation-quota"})


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] in _NOT_YET_PORTED:
        from app.admin.cli import main as old_main

        return old_main(arguments)
    args = build_parser().parse_args(arguments)
    try:
        if args.command == "users" and args.action == "reset-password":
            email = normalize_email(args.email)
            result = asyncio.run(_reset_password(email, _read_new_password()))
        else:  # pragma: no cover - argparse rejects unknown commands
            raise ValueError("Unknown command.")
    except (ValueError, AppError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
