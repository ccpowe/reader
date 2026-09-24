"""The ``reader-admin`` operational CLI (no admin HTTP surface)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID

from app.core.settings import get_settings
from app.llm.codex_auth import CodexAuthenticationError, CodexRefreshError
from app.services.web_rule_jobs import JobGuardError
from app.storage.database import build_engine, build_session_factory

from .codex_auth import configure_parser as configure_codex_auth_parser
from .codex_auth import run_codex_auth
from .translation_quota import TranslationQuotaAdminError, TranslationQuotaAdminService
from .users import configure_parser as configure_user_parser
from .users import run_user_admin
from .web_rules import configure_parser as configure_web_rule_parser
from .web_rules import run_web_rule_admin


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reader-admin", description="Reader operations CLI.")
    commands = parser.add_subparsers(dest="command", required=True)
    configure_web_rule_parser(commands)
    configure_user_parser(commands)
    configure_codex_auth_parser(commands)
    quota = commands.add_parser(
        "translation-quota",
        help="show or change per-user translation quota overrides",
    )
    actions = quota.add_subparsers(dest="action", required=True)
    show = actions.add_parser("show", help="show one user's override and current usage")
    _user_argument(show)
    listing = actions.add_parser("list", help="list configured overrides")
    listing.add_argument(
        "--include-disabled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="include disabled users (default: true)",
    )
    set_command = actions.add_parser("set", help="set one or both finite user limits")
    _user_argument(set_command)
    _mutation_arguments(set_command)
    set_command.add_argument("--requests", type=_nonnegative_int)
    set_command.add_argument("--actual-miss-chars", type=_nonnegative_int)
    for action in ("disable", "enable", "delete"):
        command = actions.add_parser(action, help=f"{action} a user's quota override")
        _user_argument(command)
        _mutation_arguments(command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (
        TranslationQuotaAdminError,
        JobGuardError,
        ValueError,
        CodexAuthenticationError,
        CodexRefreshError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


async def _run(args: argparse.Namespace) -> int:
    if args.command == "codex-auth":
        result = await run_codex_auth(get_settings(), args)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["available"] else 2
    if args.command not in {"translation-quota", "web-rules", "users"}:
        raise TranslationQuotaAdminError("unknown command")
    settings = get_settings()
    if not settings.database_url:
        raise TranslationQuotaAdminError("APP_DATABASE_URL is required")
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        if args.command == "users":
            result = await run_user_admin(session_factory, args)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "web-rules":
            result = await run_web_rule_admin(session_factory, args)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
            return 0
        service = TranslationQuotaAdminService(session_factory)
        if args.action == "list":
            result = await service.list(include_disabled=args.include_disabled)
        else:
            user_id = _resolve_user_id(args)
            if args.action == "show":
                result = await service.show(user_id)
            elif args.action == "set":
                result = await service.set(
                    user_id,
                    requests=args.requests,
                    actual_miss_chars=args.actual_miss_chars,
                    actor=args.actor,
                    reason=args.reason,
                    reference=args.reference,
                )
            elif args.action == "disable":
                result = await service.disable(
                    user_id,
                    actor=args.actor,
                    reason=args.reason,
                    reference=args.reference,
                )
            elif args.action == "enable":
                result = await service.enable(
                    user_id,
                    actor=args.actor,
                    reason=args.reason,
                    reference=args.reference,
                )
            elif args.action == "delete":
                result = await service.delete(
                    user_id,
                    actor=args.actor,
                    reason=args.reason,
                    reference=args.reference,
                )
            else:
                raise TranslationQuotaAdminError("unknown translation-quota action")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    finally:
        await engine.dispose()


def _user_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("user_id", nargs="?", type=_uuid_value, help="Reader user UUID")
    parser.add_argument(
        "--user-id",
        dest="user_id_option",
        type=_uuid_value,
        help=argparse.SUPPRESS,
    )


def _mutation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor", required=True, help="operator identity recorded in the audit")
    parser.add_argument("--reason", required=True, help="required operational reason")
    parser.add_argument(
        "--reference",
        help="optional safe incident/change reference recorded in the audit",
    )


def _resolve_user_id(args: argparse.Namespace) -> UUID:
    user_id = args.user_id_option or args.user_id
    if not isinstance(user_id, UUID):
        raise TranslationQuotaAdminError("user_id must be a UUID")
    return user_id


def _uuid_value(value: str) -> UUID:
    try:
        return UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a UUID") from exc


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
