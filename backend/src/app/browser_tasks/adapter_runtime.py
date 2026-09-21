"""Task-local adapter for controlled CLI exploration and rendered page crawls."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
from collections import OrderedDict
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError

from app.ingestion.models import SourceScanError
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.url_safety import PublicAsyncClient, validate_http_url
from app.ingestion.web_crawl import crawl_page
from app.web_rule_agent.cli_bridge import CDPBridge
from app.web_rule_agent.cli_browser import check_args, native_report

from .auth import capability_hash
from .protocol import (
    CLICommandRequest,
    CLIReport,
    ErrorEnvelope,
    ErrorKind,
    HealthResponse,
    RenderedPageResponse,
    RenderPageRequest,
    RuntimeIdentity,
    TaskPurpose,
)

_CONTROL_ROOT = Path("/run/reader-browser-task")
_LP_SOCKET = _CONTROL_ROOT / "browser" / "lightpanda.sock"
_CLI_SOCKET = _CONTROL_ROOT / "browser" / "cli.sock"
_BRIDGE_SOCKET = _CONTROL_ROOT / "adapter" / "bridge.sock"
_CLI_RESPONSE_LIMIT = 4 * 1024 * 1024
_REQUEST_CACHE_SIZE = 128
_CLI_COMMAND_TIMEOUT = 28.0


@dataclass(frozen=True)
class AdapterConfiguration:
    task_id: UUID
    purpose: TaskPurpose
    source_url: str
    capability_sha256: str = field(repr=False)
    runtime_identity: RuntimeIdentity
    max_response_bytes: int = 20 * 1024 * 1024
    control_root: Path = _CONTROL_ROOT

    @classmethod
    def from_environment(cls) -> AdapterConfiguration:
        try:
            identity = RuntimeIdentity.model_validate_json(
                os.environ["READER_BROWSER_RUNTIME_IDENTITY"]
            )
            configuration = cls(
                task_id=UUID(os.environ["READER_BROWSER_TASK_ID"]),
                purpose=TaskPurpose(os.environ["READER_BROWSER_TASK_PURPOSE"]),
                source_url=validate_http_url(os.environ["READER_BROWSER_SOURCE_URL"]),
                capability_sha256=os.environ["READER_BROWSER_CAPABILITY_SHA256"],
                runtime_identity=identity,
                max_response_bytes=int(
                    os.environ.get("READER_BROWSER_MAX_RESPONSE_BYTES", 20 * 1024 * 1024)
                ),
                control_root=Path(
                    os.environ.get("READER_BROWSER_CONTROL_ROOT", str(_CONTROL_ROOT))
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceScanError(
                "browser_configuration_invalid", "Browser adapter configuration is invalid."
            ) from exc
        if not re.fullmatch(r"[0-9a-f]{64}", configuration.capability_sha256):
            raise SourceScanError(
                "browser_configuration_invalid", "Browser adapter capability hash is invalid."
            )
        if not 256 * 1024 <= configuration.max_response_bytes <= 20 * 1024 * 1024:
            raise SourceScanError(
                "browser_configuration_invalid", "Browser adapter response limit is invalid."
            )
        return configuration

    @property
    def lightpanda_socket(self) -> Path:
        return self.control_root / "browser" / "lightpanda.sock"

    @property
    def cli_socket(self) -> Path:
        return self.control_root / "browser" / "cli.sock"

    @property
    def bridge_socket(self) -> Path:
        return self.control_root / "adapter" / "bridge.sock"

    def verify_capability(self, provided: str | None) -> None:
        candidate = capability_hash(provided) if provided else ""
        if not hmac.compare_digest(candidate, self.capability_sha256):
            raise AdapterProtocolError(
                ErrorEnvelope(
                    code="unauthorized",
                    message="Task capability is missing or invalid.",
                    kind=ErrorKind.PROTOCOL,
                )
            )


class AdapterProtocolError(SourceScanError):
    def __init__(self, error: ErrorEnvelope):
        super().__init__(
            error.code,
            error.message,
            retry_after_seconds=error.retry_after_seconds,
            long_lived=error.long_lived,
            evidence=error.evidence,
        )
        self.error = error


class FixedRelay:
    """A fixed-endpoint byte relay; callers can never choose its destination."""

    def __init__(self) -> None:
        self.server: asyncio.AbstractServer | None = None
        self.tasks: set[asyncio.Task] = set()
        self.socket_path: Path | None = None

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while chunk := await reader.read(64 * 1024):
                writer.write(chunk)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _serve_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        connector,
    ) -> None:
        try:
            upstream_reader, upstream_writer = await connector()
        except (OSError, TimeoutError):
            writer.close()
            await writer.wait_closed()
            return
        tasks = {
            asyncio.create_task(self._pipe(reader, upstream_writer)),
            asyncio.create_task(self._pipe(upstream_reader, writer)),
        }
        self.tasks.update(tasks)
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.tasks.difference_update(tasks)

    def _schedule_connection(self, reader, writer, connector) -> None:
        task = asyncio.create_task(self._serve_connection(reader, writer, connector))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def tcp_to_unix(self, socket_path: Path) -> int:
        async def connector():
            return await asyncio.open_unix_connection(socket_path)

        self.server = await asyncio.start_server(
            lambda reader, writer: self._schedule_connection(reader, writer, connector),
            "127.0.0.1",
            0,
        )
        return int(self.server.sockets[0].getsockname()[1])

    async def unix_to_tcp(self, socket_path: Path, port: int) -> None:
        if socket_path.exists() or socket_path.is_symlink():
            raise SourceScanError(
                "browser_unavailable", "Browser adapter control socket already exists."
            )

        async def connector():
            return await asyncio.open_connection("127.0.0.1", port)

        self.socket_path = socket_path
        self.server = await asyncio.start_unix_server(
            lambda reader, writer: self._schedule_connection(reader, writer, connector),
            path=socket_path,
        )
        socket_path.chmod(0o660)

    async def aclose(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if self.socket_path is not None:
            self.socket_path.unlink(missing_ok=True)


@dataclass
class RelayedBrowserEndpoint:
    url: str
    relay: FixedRelay
    _stop_task: asyncio.Task | None = field(default=None, init=False, repr=False)

    async def stop(self) -> None:
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self.relay.aclose())
        await asyncio.shield(self._stop_task)


@asynccontextmanager
async def relayed_browser_endpoint(socket_path: Path):
    relay = FixedRelay()
    try:
        port = await relay.tcp_to_unix(socket_path)
        endpoint = RelayedBrowserEndpoint(f"ws://127.0.0.1:{port}", relay)
        yield endpoint
    finally:
        await relay.aclose()


async def _wait_browser_ready(socket_path: Path) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        try:
            async with relayed_browser_endpoint(socket_path) as endpoint:
                port = urlsplit(endpoint.url).port
                async with httpx.AsyncClient(trust_env=False) as client:
                    response = await client.get(
                        f"http://127.0.0.1:{port}/json/version", timeout=0.5
                    )
                    if response.status_code == 200 and len(response.content) <= 8192:
                        return
        except (OSError, httpx.HTTPError):
            pass
        if asyncio.get_running_loop().time() >= deadline:
            raise SourceScanError(
                "browser_unavailable", "Task Lightpanda endpoint did not become ready."
            )
        await asyncio.sleep(0.1)


class UnixCLIClient:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            try:
                self.reader, self.writer = await asyncio.open_unix_connection(
                    self.socket_path, limit=_CLI_RESPONSE_LIMIT
                )
                return
            except OSError:
                if asyncio.get_running_loop().time() >= deadline:
                    raise SourceScanError(
                        "browser_unavailable", "Task CLI endpoint did not become ready."
                    )
                await asyncio.sleep(0.1)

    async def command(self, argv: list[str], *, timeout: float = _CLI_COMMAND_TIMEOUT) -> dict:
        async with self.lock:
            if self.reader is None or self.writer is None or self.writer.is_closing():
                raise SourceScanError("browser_session_lost", "Task CLI session is unavailable.")
            try:
                message = json.dumps(
                    {"argv": argv, "timeout": timeout}, ensure_ascii=False, separators=(",", ":")
                ).encode()
                if len(message) > 96 * 1024:
                    raise ValueError("CLI request exceeded its transport limit")
                self.writer.write(message + b"\n")
                await self.writer.drain()
                async with asyncio.timeout(timeout + 3):
                    line = await self.reader.readline()
                if not line or len(line) >= _CLI_RESPONSE_LIMIT:
                    raise ValueError("Task CLI returned an invalid response")
                result = json.loads(line)
                if not isinstance(result, dict) or result.get("runner_error"):
                    raise ValueError(str(result.get("runner_error") or "invalid CLI response"))
                return result
            except (OSError, ValueError, TimeoutError, json.JSONDecodeError) as exc:
                raise SourceScanError("browser_session_lost", str(exc)) from exc

    async def aclose(self) -> None:
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()


class AdapterCLISession:
    """Persistent explore session with all untrusted processes outside Worker."""

    def __init__(self, configuration: AdapterConfiguration) -> None:
        self.configuration = configuration
        self.source_url = urldefrag(configuration.source_url)[0]
        self.stack: AsyncExitStack | None = None
        self.endpoint = None
        self.bridge: CDPBridge | None = None
        self.bridge_relay: FixedRelay | None = None
        self.cli = UnixCLIClient(configuration.cli_socket)
        self.session = "reader-" + uuid4().hex[:12]
        self.url: str | None = None
        self.lock = asyncio.Lock()
        self._close_task: asyncio.Task | None = None

    async def start(self) -> None:
        self.stack = AsyncExitStack()
        try:
            client = await self.stack.enter_async_context(PublicAsyncClient(follow_redirects=True))
            self.endpoint = await self.stack.enter_async_context(
                relayed_browser_endpoint(self.configuration.lightpanda_socket)
            )
            self.bridge = CDPBridge(
                self.endpoint,
                client,
                self.source_url,
                maximum_bytes=self.configuration.max_response_bytes,
            )
            await self.bridge.start()
            self.bridge_relay = FixedRelay()
            await self.bridge_relay.unix_to_tcp(self.configuration.bridge_socket, self.bridge.port)
            await self.cli.start()
            version = await self.cli.command(["/reader/agent-browser", "--version"], timeout=5)
            if version.get("exit_code") != 0 or version.get("stdout", "").strip() != (
                "agent-browser 0.37.1"
            ):
                raise SourceScanError("browser_unavailable", "CLI version must be exactly 0.37.1.")
            self.base = [
                "/reader/agent-browser",
                "--config",
                "/reader/agent-browser.json",
                "--session",
                self.session,
                "--namespace",
                self.session,
                "--cdp",
                "9333",
                "--no-webmcp",
                "--json",
                "--max-output",
                "64000",
                "--idle-timeout",
                "0",
            ]
        except BaseException:
            await self.aclose()
            raise

    async def command(self, argv: list[str], *, observed_urls=()) -> dict[str, Any]:
        async with self.lock:
            try:
                check_args(argv)
                if argv[0] == "open" and len(argv) >= 2:
                    target = urldefrag(argv[1])[0]
                    parsed, source = urlsplit(target), urlsplit(self.source_url)
                    allowed = {self.source_url, *(urldefrag(str(url))[0] for url in observed_urls)}
                    if (parsed.scheme, parsed.netloc.lower()) != (
                        source.scheme,
                        source.netloc.lower(),
                    ) or target not in allowed:
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
                    "truncated_fields": [],
                    "error": {
                        "source": "wrapper",
                        "exit_code": None,
                        "message": str(exc),
                    },
                    "stderr": "",
                    "command_timed_out": False,
                    "cli_warning": None,
                    "network_errors": [],
                }
            if self.bridge is None:
                raise SourceScanError("browser_session_lost", "Explore bridge is unavailable.")
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

    async def _close(self) -> None:
        try:
            if hasattr(self, "base"):
                try:
                    async with asyncio.timeout(4):
                        await self.cli.command(self.base + ["close"], timeout=3)
                except Exception:
                    pass
        finally:
            try:
                await self.cli.aclose()
            finally:
                try:
                    if self.bridge_relay is not None:
                        await self.bridge_relay.aclose()
                finally:
                    try:
                        if self.bridge is not None:
                            await self.bridge.aclose()
                    finally:
                        if self.stack is not None:
                            await self.stack.aclose()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)


def _request_fingerprint(request) -> str:
    payload = request.model_dump_json(exclude={"request_id"})
    return hashlib.sha256(payload.encode()).hexdigest()


class AdapterRuntime:
    def __init__(self, configuration: AdapterConfiguration) -> None:
        self.configuration = configuration
        self.cli_session: AdapterCLISession | None = None
        self.health = HealthResponse(status="starting")
        self.lock = asyncio.Lock()
        self.responses: OrderedDict[UUID, tuple[str, Any]] = OrderedDict()
        self.render_request_id: UUID | None = None
        self.render_request_fingerprint: str | None = None

    async def start(self) -> None:
        try:
            await _wait_browser_ready(self.configuration.lightpanda_socket)
            if self.configuration.purpose == TaskPurpose.EXPLORE:
                self.cli_session = AdapterCLISession(self.configuration)
                await self.cli_session.start()
            self.health = HealthResponse(status="ready")
        except BaseException:
            self.health = HealthResponse(status="failed")
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self.cli_session is not None:
            await self.cli_session.aclose()

    def authorize(self, *, capability: str | None, task_id: UUID, purpose: TaskPurpose) -> None:
        self.configuration.verify_capability(capability)
        if task_id != self.configuration.task_id:
            raise AdapterProtocolError(
                ErrorEnvelope(
                    code="task_mismatch",
                    message="Request task does not match this adapter.",
                    kind=ErrorKind.PROTOCOL,
                )
            )
        if purpose != self.configuration.purpose:
            raise AdapterProtocolError(
                ErrorEnvelope(
                    code="purpose_mismatch",
                    message="Operation is not available for this task purpose.",
                    kind=ErrorKind.PROTOCOL,
                )
            )
        if self.health.status != "ready":
            raise AdapterProtocolError(
                ErrorEnvelope(
                    code="browser_session_lost",
                    message="Browser adapter is not ready.",
                    kind=ErrorKind.LIFECYCLE,
                    retryable=True,
                )
            )

    def _cached(self, request) -> Any | None:
        cached = self.responses.get(request.request_id)
        if cached is None:
            return None
        fingerprint, response = cached
        if not hmac.compare_digest(fingerprint, _request_fingerprint(request)):
            raise AdapterProtocolError(
                ErrorEnvelope(
                    code="request_conflict",
                    message="Request ID was reused with a different body.",
                    kind=ErrorKind.PROTOCOL,
                )
            )
        self.responses.move_to_end(request.request_id)
        return response

    def _remember(self, request, response: Any) -> None:
        self.responses[request.request_id] = (_request_fingerprint(request), response)
        while len(self.responses) > _REQUEST_CACHE_SIZE:
            self.responses.popitem(last=False)

    async def cli_command(self, request: CLICommandRequest, capability: str | None) -> CLIReport:
        self.authorize(capability=capability, task_id=request.task_id, purpose=TaskPurpose.EXPLORE)
        async with self.lock:
            if cached := self._cached(request):
                return cached
            if self.cli_session is None:
                raise AdapterProtocolError(
                    ErrorEnvelope(
                        code="browser_session_lost",
                        message="Explore session is unavailable.",
                        kind=ErrorKind.LIFECYCLE,
                        retryable=True,
                    )
                )
            raw = await self.cli_session.command(
                request.argv, observed_urls=[str(url) for url in request.observed_urls]
            )
            response = CLIReport.model_validate(raw)
            self._remember(request, response)
            return response

    async def render_page(
        self, request: RenderPageRequest, capability: str | None
    ) -> RenderedPageResponse:
        self.authorize(capability=capability, task_id=request.task_id, purpose=TaskPurpose.RENDER)
        async with self.lock:
            if cached := self._cached(request):
                return cached
            if self.render_request_id is not None:
                if self.render_request_id == request.request_id:
                    if not hmac.compare_digest(
                        self.render_request_fingerprint or "", _request_fingerprint(request)
                    ):
                        raise AdapterProtocolError(
                            ErrorEnvelope(
                                code="request_conflict",
                                message="Request ID was reused with a different body.",
                                kind=ErrorKind.PROTOCOL,
                            )
                        )
                    # A previous attempt failed before producing a cacheable response.
                    # Reusing the same id may repeat a public GET, but can never run a
                    # different render operation in this one-shot task.
                    pass
                else:
                    raise AdapterProtocolError(
                        ErrorEnvelope(
                            code="render_already_used",
                            message="A render task accepts exactly one logical request.",
                            kind=ErrorKind.PROTOCOL,
                        )
                    )
            source_origin = urlsplit(self.configuration.source_url)
            target_origin = urlsplit(str(request.url))
            if (source_origin.scheme, source_origin.netloc.lower()) != (
                target_origin.scheme,
                target_origin.netloc.lower(),
            ):
                raise AdapterProtocolError(
                    ErrorEnvelope(
                        code="invalid_render_url",
                        message="Render URL must use the task source origin.",
                        kind=ErrorKind.PROTOCOL,
                    )
                )
            self.render_request_id = request.request_id
            self.render_request_fingerprint = _request_fingerprint(request)
            maximum_bytes = min(request.max_response_bytes, self.configuration.max_response_bytes)
            async with PublicAsyncClient(follow_redirects=True) as client:
                source = WebBlogSourceAdapter(
                    WebBlogSourceConfig(url=self.configuration.source_url), client
                )

                def endpoint_factory(*_args, **_kwargs):
                    return relayed_browser_endpoint(self.configuration.lightpanda_socket)

                page = await crawl_page(
                    client,
                    str(request.url),
                    request.recipe,
                    maximum_bytes=maximum_bytes,
                    robots_allows=source._robots_allows,
                    browser_engine="lightpanda",
                    lightpanda_executable_path="/reader/lightpanda",
                    browser_endpoint_factory=endpoint_factory,
                )
            headers = {
                key: value
                for key, value in page.response.headers.items()
                if key.lower()
                not in {
                    "connection",
                    "content-encoding",
                    "content-length",
                    "set-cookie",
                    "transfer-encoding",
                }
            }
            try:
                response = RenderedPageResponse(
                    final_url=str(page.response.url),
                    status_code=page.response.status_code,
                    safe_headers=headers,
                    rendered_html=page.response.text,
                    rows=page.rows,
                )
            except ValidationError as exc:
                raise SourceScanError(
                    "response_too_large", "Rendered page metadata exceeded its protocol limit."
                ) from exc
            if len(response.model_dump_json().encode()) > maximum_bytes * 2 + 256 * 1024:
                raise SourceScanError(
                    "response_too_large", "Rendered page protocol response exceeded its limit."
                )
            self._remember(request, response)
            return response
