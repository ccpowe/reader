"""Persistent agent-browser CLI adapter; production tool wiring is separate."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from collections.abc import Collection
from contextlib import AsyncExitStack
from urllib.parse import urldefrag, urljoin, urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup

from app.ingestion.browser_runtime import _settle, browser_endpoint
from app.ingestion.models import SourceScanError
from app.ingestion.url_safety import PublicAsyncClient
from app.web_rule_agent.cli_bridge import CDPBridge
from app.web_rule_agent.cli_process import OwnedCLIProcess

MAX_RESULT_BYTES = 20 * 1024
COMMANDS = {"open", "snapshot", "get", "eval", "click", "wait", "scroll", "find", "back", "forward"}
_FLAGS = {
    "snapshot": {
        "-i",
        "-u",
        "-c",
        "-d",
        "-s",
        "--interactive",
        "--compact",
        "--depth",
        "--selector",
    },
    "wait": {"--load", "--text", "--url", "--fn"},
    "find": {"--name", "--exact"},
}


def check_args(argv: list[str]) -> None:
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise ValueError("argv must be a nonempty array of strings")
    if len(argv) > 16 or sum(len(arg) for arg in argv) > 12000 or any("\0" in arg for arg in argv):
        raise ValueError("argv allows at most 16 strings, 12000 characters total, and no NUL bytes")
    if argv[0] not in COMMANDS:
        raise ValueError("Available commands: " + ", ".join(sorted(COMMANDS)))
    if any(arg.startswith("-") and arg not in _FLAGS.get(argv[0], set()) for arg in argv[1:]):
        raise ValueError(
            "Only documented command flags are available; "
            "browser identity and transport are host-controlled"
        )


def _size(value: dict) -> int:
    # Default json.dumps separators are deliberately included in the byte limit.
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def finalize_cli_report(
    report: dict, *, metadata: dict | None = None, maximum_bytes: int = MAX_RESULT_BYTES
) -> dict:
    """The only model-output clipping pass, after the caller adds IDs and metadata.

    `truncated` describes Reader capture/output clipping, never completeness of a
    webpage. Native CLI truncation text/warnings remain in output/cli_warning.
    The caller must not subsequently run a generic content-dropping compactor.
    """
    result = copy.deepcopy(report)
    result.update(metadata or {})
    result.setdefault("evidence_id", None)
    result.setdefault("repeated", False)
    result.setdefault("truncated", False)
    clipped = result.setdefault("truncated_fields", [])
    if len(result.get("stderr", "")) > 1000:
        result["stderr"] = result["stderr"][:1000]
        clipped.append("stderr")
    if len(result.get("network_errors", [])) > 3:
        result["network_errors"] = result["network_errors"][:3]
        clipped.append("network_errors")
    if clipped:
        result["truncated"] = True
    if _size(result) <= maximum_bytes:
        return result
    result["truncated"] = True

    # Clip payload text, not IDs, URL, revision, exit code or native error source.
    fields = [
        (result, "output", "output"),
        (result, "stderr", "stderr"),
        (result, "cli_warning", "cli_warning"),
    ]
    for index, error in enumerate(result.get("network_errors", [])):
        fields.extend((error, key, f"network_errors[{index}].{key}") for key in ("message", "url"))
    if isinstance(result.get("error"), dict):
        fields.append((result["error"], "message", "error.message"))
    for owner, key, label in fields:
        text = owner.get(key)
        if not isinstance(text, str) or not text:
            continue
        if label not in clipped:
            clipped.append(label)
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            owner[key] = text[:middle]
            if _size(result) <= maximum_bytes:
                low = middle
            else:
                high = middle - 1
        owner[key] = text[:low]
        if _size(result) <= maximum_bytes:
            return result
    raise ValueError("CLI result metadata alone exceeds the tool result byte limit")


def native_report(argv: list[str], raw: dict, *, url: str | None, revision: str | None) -> dict:
    report = {
        "status": "ok",
        "url": url,
        "page_revision": revision,
        "format": "text",
        "output": "",
        "truncated": bool(raw.get("capture_truncated")),
        "truncated_fields": [name + "_capture" for name in raw.get("capture_truncated", [])],
        "error": None,
        "stderr": raw.get("stderr", ""),
    }
    if raw.get("timed_out"):
        report["command_timed_out"] = True
    try:
        native = json.loads(raw.get("stdout", ""))
        if not isinstance(native, dict) or not isinstance(native.get("success"), bool):
            raise ValueError("No native CLI response envelope")
    except ValueError:
        report.update(
            status="error",
            output=raw.get("stdout", ""),
            error={
                "source": "cli",
                "exit_code": raw.get("exit_code"),
                "message": None,
            },
        )
        return report
    if native.get("warning"):
        report["cli_warning"] = native["warning"]
    if raw.get("exit_code") != 0 or native["success"] is False or raw.get("timed_out"):
        report.update(
            status="error",
            error={
                "source": "cli",
                "exit_code": raw.get("exit_code"),
                "message": native.get("error"),
            },
        )
        if native.get("code") or native.get("type"):
            report["error"]["code"] = native.get("code") or native["type"]
    payload = native.get("data")
    explicit_value = False
    if isinstance(payload, dict):
        payload = {
            key: value for key, value in payload.items() if key not in {"lifecycle", "targetId"}
        }
        if argv[0] == "snapshot" and isinstance(payload.get("snapshot"), str):
            report.update(format="snapshot", output=payload["snapshot"])
            return report
        if argv[0] == "eval" and "result" in payload:
            payload = payload["result"]
            explicit_value = True
        elif argv[0] == "get" and len(argv) > 1 and argv[1] in payload:
            payload = payload[argv[1]]
            explicit_value = True
    if isinstance(payload, str):
        report.update(format="html" if argv[:2] == ["get", "html"] else "text", output=payload)
    elif payload is not None or explicit_value:
        report.update(format="json", output=json.dumps(payload, ensure_ascii=False))
    return report


def visible_links(report: dict) -> list[str]:
    """URLs visible in the final output only; unseen DOM never grants navigation."""
    output, url = report.get("output", ""), report.get("url")
    if not url:
        return []
    hrefs = []
    if report.get("format") == "snapshot":
        hrefs = re.findall(r"\[ref=[^\]\n]*?, url=([^\n]+)\](?:\s*)$", output, re.M)
    elif report.get("format") == "html":
        # Do not grant an anchor whose native HTML was cut before its closing tag.
        for anchor in re.findall(r"<a\b[^>]*>.*?</a\s*>", output, re.I | re.S):
            node = BeautifulSoup(anchor, "html.parser").find("a", href=True)
            if node:
                hrefs.append(node["href"])
    try:
        origin = urlsplit(url)
    except ValueError:
        return []
    result = []
    for href in hrefs:
        try:
            target = urldefrag(urljoin(url, href))[0]
            parsed = urlsplit(target)
        except ValueError:
            continue
        if (parsed.scheme, parsed.netloc.lower()) == (origin.scheme, origin.netloc.lower()):
            if target not in result:
                result.append(target)
    return result


class CLIBrowserSession:
    """One exploration session, retained until its owning task closes it.

    `command` returns a report for the context to identify/store. Call
    `finalize_cli_report` once with all final metadata before model delivery.
    """

    def __init__(
        self,
        settings,
        source_url: str,
        *,
        cli_executable: str,
        browser_identity: dict | None = None,
        cli_identity: dict | None = None,
    ):
        self.settings = settings
        self.source_url = urldefrag(source_url)[0]
        self.browser_identity = browser_identity
        self.cli = OwnedCLIProcess(cli_executable, expected_identity=cli_identity)
        self.stack = None
        self.bridge = None
        self.endpoint = None
        self.url = None
        self.session = "reader-" + uuid4().hex[:12]
        self._closed = False
        self._close_task = None
        self._lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> None:
        try:
            async with self._lifecycle_lock:
                if self._closed:
                    raise SourceScanError(
                        "browser_session_lost", "Exploration session already ended."
                    )
                if self.stack is not None:
                    return
                self.stack = AsyncExitStack()
                client = await self.stack.enter_async_context(
                    PublicAsyncClient(follow_redirects=True)
                )
                self.endpoint = await self.stack.enter_async_context(
                    browser_endpoint(
                        self.settings.ingestion_browser_engine,
                        self.settings.ingestion_lightpanda_executable_path,
                        expected_identity=self.browser_identity,
                    )
                )
                self.bridge = CDPBridge(
                    self.endpoint,
                    client,
                    self.source_url,
                    maximum_bytes=self.settings.ingestion_max_response_bytes,
                )
                await self.bridge.start()
                await self.cli.start()
                self.base = [
                    "/reader/agent-browser",
                    "--config",
                    "/run/reader-cli/config.json",
                    "--session",
                    self.session,
                    "--namespace",
                    self.session,
                    "--cdp",
                    str(self.bridge.port),
                    "--no-webmcp",
                    "--json",
                    "--max-output",
                    "64000",
                    "--idle-timeout",
                    "0",
                ]
        except BaseException:
            # Release the lifecycle lock before joining cleanup. A concurrent
            # close must await all in-flight acquisitions before it can finish.
            await _settle(asyncio.create_task(self.aclose()), propagate_cancel=False)
            raise

    async def command(self, argv: list[str], *, observed_urls: Collection[str] = ()) -> dict:
        async with self._lock:
            try:
                check_args(argv)
                if argv[0] == "open" and len(argv) >= 2:
                    target = urldefrag(argv[1])[0]
                    parsed, source = urlsplit(target), urlsplit(self.source_url)
                    if (parsed.scheme, parsed.netloc.lower()) != (
                        source.scheme,
                        source.netloc.lower(),
                    ) or target not in {self.source_url, *observed_urls}:
                        raise ValueError(
                            "open requires the source URL or an observed same-origin URL"
                        )
            except ValueError as exc:
                return {
                    "status": "error",
                    "url": self.url,
                    "page_revision": None,
                    "format": "text",
                    "output": "",
                    "truncated": False,
                    "error": {"source": "wrapper", "exit_code": None, "message": str(exc)},
                }
            try:
                await self.start()
                self.bridge.reset_errors()
                raw = await self.cli.command(self.base + argv)
                revision = None
                dom_error = None
                try:
                    async with asyncio.timeout(4):
                        state = await self.bridge.dom()
                    self.url = state["url"]
                    revision = hashlib.sha256(state["html"].encode()).hexdigest()
                except SourceScanError as exc:
                    self.url = None
                    dom_error = exc
                except (RuntimeError, TimeoutError, ValueError, KeyError):
                    # A CLI error is not a DOM version. A successful DOM read
                    # after a failing mutation still supplies the real revision.
                    self.url = None
                report = native_report(argv, raw, url=self.url, revision=revision)
                if dom_error is not None and report["error"] is None:
                    report.update(
                        status="error",
                        error={
                            "source": "wrapper",
                            "exit_code": raw.get("exit_code"),
                            "code": dom_error.code,
                            "message": str(dom_error),
                        },
                    )
                report["network_errors"] = list(self.bridge.network_errors)
                if self.bridge.network_error_count > len(report["network_errors"]):
                    report["truncated_fields"].append("network_errors")
                    report["truncated"] = True
                return report
            except asyncio.CancelledError:
                await _settle(asyncio.create_task(self.aclose()), propagate_cancel=False)
                raise

    async def _close(self) -> None:
        self._closed = True
        async with self._lifecycle_lock:
            try:
                if hasattr(self, "base") and self.cli.process is not None:
                    try:
                        async with asyncio.timeout(4):
                            await self.cli.command(self.base + ["close"], timeout=3)
                    except Exception:
                        pass  # The namespace is always forcibly reaped below.
            finally:
                try:
                    await self.cli.aclose()
                finally:
                    try:
                        if self.endpoint is not None:
                            await self.endpoint.stop()
                    finally:
                        try:
                            if self.bridge is not None:
                                await self.bridge.aclose()
                        finally:
                            if self.stack is not None:
                                await self.stack.aclose()

    async def aclose(self, reason=None) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await _settle(self._close_task)
