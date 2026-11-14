"""reader-admin: operator commands that run on the server, never over HTTP.

reader-admin users reset-password EMAIL
reader-admin codex-auth import --source PATH|- [--replace]   (a dedicated Codex login)
reader-admin codex-auth status | refresh
reader-admin translation usage                                 (characters used today)
reader-admin mcp-token create|revoke EMAIL                     (personal token for /mcp)

Output is JSON and never contains credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path

from reader.accounts import service as accounts
from reader.accounts.api import normalize_email
from reader.core.db import build_engine, build_session_factory
from reader.core.errors import AppError
from reader.core.settings import Settings, get_settings
from reader.translation import budget
from reader.translation.codex_auth import (
    MAX_AUTH_BYTES,
    CodexAuthenticationError,
    CodexRefreshError,
    import_subscription_auth,
    subscription_auth_status,
    subscription_headers,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reader-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    users = commands.add_parser("users", help="manage accounts")
    actions = users.add_subparsers(dest="action", required=True)
    reset = actions.add_parser("reset-password", help="set a password and end all sessions")
    reset.add_argument("email")

    mcp = commands.add_parser("mcp-token", help="the personal token an agent uses for /mcp")
    mcp_actions = mcp.add_subparsers(dest="action", required=True)
    create = mcp_actions.add_parser("create", help="issue a token (replaces the old one)")
    create.add_argument("email")
    revoke = mcp_actions.add_parser("revoke", help="remove the token")
    revoke.add_argument("email")

    codex = commands.add_parser("codex-auth", help="manage Reader's Codex subscription login")
    codex_actions = codex.add_subparsers(dest="action", required=True)
    importing = codex_actions.add_parser("import", help="import a dedicated Codex login once")
    importing.add_argument("--source", required=True, help="auth.json path, or - for stdin")
    importing.add_argument("--replace", action="store_true", help="replace an earlier login")
    codex_actions.add_parser("status", help="show availability without contacting OpenAI")
    codex_actions.add_parser("refresh", help="refresh the token if it is near expiry")

    translation = commands.add_parser("translation", help="translation budget")
    translation_actions = translation.add_subparsers(dest="action", required=True)
    translation_actions.add_parser("usage", help="characters used this minute and today")
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


def _codex_auth(settings: Settings, action: str, source: str | None, replace: bool) -> dict:
    """Runs without the database, so it works before the server is set up."""
    path = settings.codex_subscription_auth_file
    if action == "import":
        try:
            if source == "-":
                data = sys.stdin.buffer.read(MAX_AUTH_BYTES + 1)
            else:
                origin = Path(source).expanduser()
                # Reader rotates its tokens; sharing Codex CLI's live file would break one of them.
                if path is not None and origin.resolve() == path.expanduser().resolve():
                    raise CodexAuthenticationError(
                        "codex_subscription_import_requires_separate_store"
                    )
                with origin.open("rb") as stream:
                    data = stream.read(MAX_AUTH_BYTES + 1)
        except OSError:
            raise CodexAuthenticationError("invalid_codex_subscription_auth") from None
        import_subscription_auth(path, data, replace=replace)
    elif action == "refresh":
        subscription_headers(path)
    return subscription_auth_status(path)


async def _mcp_token(settings: Settings, action: str, email: str) -> dict:
    engine = build_engine(settings)
    try:
        async with build_session_factory(engine)() as db:
            if action == "create":
                token = await accounts.create_mcp_token(db, email)
                # The only time the token is shown; it is stored as a hash.
                return {"status": "created", "token": token}
            await accounts.revoke_mcp_token(db, email)
            return {"status": "revoked"}
    finally:
        await engine.dispose()


async def _translation_usage(settings: Settings) -> dict:
    engine = build_engine(settings)
    try:
        return await budget.usage(build_session_factory(engine), settings)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    settings = get_settings()
    try:
        if args.command == "users" and args.action == "reset-password":
            email = normalize_email(args.email)
            result = asyncio.run(_reset_password(email, _read_new_password()))
        elif args.command == "mcp-token":
            result = asyncio.run(_mcp_token(settings, args.action, normalize_email(args.email)))
        elif args.command == "codex-auth":
            result = _codex_auth(
                settings,
                args.action,
                getattr(args, "source", None),
                getattr(args, "replace", False),
            )
        elif args.command == "translation" and args.action == "usage":
            result = asyncio.run(_translation_usage(settings))
        else:  # pragma: no cover - argparse rejects unknown commands
            raise ValueError("Unknown command.")
    except (CodexAuthenticationError, CodexRefreshError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (ValueError, AppError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
