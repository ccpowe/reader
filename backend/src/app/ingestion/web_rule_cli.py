"""Command line access to Reader's real native Web rule validator."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .web_rules import WebRuleError, execute_web_rule, inspect_web_page, web_rule_schema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reader-web-rule")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print the JSON schema accepted by Reader")
    validate = commands.add_parser(
        "execute", aliases=["validate"], help="run Reader's Web adapter and print facts"
    )
    validate.add_argument("--source-url", required=True)
    validate.add_argument("--rule", required=True, type=Path)
    inspect = commands.add_parser("inspect", help="inspect bounded page evidence with Crawl4AI")
    inspect.add_argument("--source-url", required=True)
    inspect.add_argument("--render-js", action="store_true")
    inspect.add_argument("--selector")
    inspect.add_argument("--offset", type=int, default=0)
    inspect.add_argument("--limit", type=int, default=16000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "schema":
        print(json.dumps(web_rule_schema(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    try:
        if args.command == "inspect":
            result = asyncio.run(
                inspect_web_page(
                    source_url=args.source_url,
                    render_js=args.render_js,
                    selector=args.selector,
                    offset=args.offset,
                    limit=args.limit,
                )
            )
        else:
            raw_rule = json.loads(args.rule.read_text(encoding="utf-8"))
            result = asyncio.run(execute_web_rule(source_url=args.source_url, raw_rule=raw_rule))
    except (OSError, json.JSONDecodeError, WebRuleError) as exc:
        print(f"FAIL\n{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("first_window_completed") or result["status"] == "pass" else 1
