"""Local-only Codex credential administration; never prints credential material."""

import asyncio
import sys
from pathlib import Path

from app.core.settings import Settings
from app.llm.codex_auth import (
    MAX_AUTH_BYTES,
    CodexAuthenticationError,
    import_subscription_auth,
    subscription_auth_status,
    subscription_headers,
)


def configure_parser(commands):
    parser = commands.add_parser("codex-auth", help="manage Reader's Codex subscription login")
    actions = parser.add_subparsers(dest="action", required=True)
    importing = actions.add_parser("import", help="import a dedicated Codex ChatGPT login once")
    importing.add_argument("--source", required=True, help="auth.json path, or - for stdin")
    importing.add_argument("--replace", action="store_true", help="replace a previous Reader login")
    actions.add_parser("status", help="show local availability, without contacting OpenAI")
    actions.add_parser("refresh", help="ensure a current token, refreshing if near expiry")


async def run_codex_auth(settings: Settings, args) -> dict:
    path = settings.codex_subscription_auth_file
    if args.action == "import":
        try:
            if args.source == "-":
                data = sys.stdin.buffer.read(MAX_AUTH_BYTES + 1)
            else:
                source = Path(args.source).expanduser()
                if path is not None and source.resolve() == path.expanduser().resolve():
                    raise CodexAuthenticationError(
                        "codex_subscription_import_requires_separate_store"
                    )
                with source.open("rb") as stream:
                    data = stream.read(MAX_AUTH_BYTES + 1)
        except OSError:
            raise CodexAuthenticationError("invalid_codex_subscription_auth") from None
        await asyncio.to_thread(import_subscription_auth, path, data, replace=args.replace)
    elif args.action == "refresh":
        await asyncio.to_thread(subscription_headers, path)
    return subscription_auth_status(path)
