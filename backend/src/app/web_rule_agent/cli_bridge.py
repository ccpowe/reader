"""Relay one task's CLI CDP connection through Reader's public HTTP transport."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from app.ingestion.browser_runtime import _ENGINE_FLAGS, _settle
from app.ingestion.http import raise_for_provider_status
from app.ingestion.models import SourceScanError
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.url_safety import safe_get

CDP_MAX_MESSAGE_BYTES = int(_ENGINE_FLAGS[_ENGINE_FLAGS.index("--cdp-max-message-size") + 1])
_USER_AGENT = "ReaderAggregator/0.2 (+https://example.invalid)"


@dataclass
class PageTransfer:
    requests: int = 0
    bytes: int = 0
    reserved: int = 0
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)

    async def reserve(self, maximum_bytes: int) -> int:
        capacity = min(20 * 1024 * 1024, maximum_bytes * 4)
        async with self._condition:
            # Do not shrink a response limit because another request might
            # return its unused reservation. Keep normal two-request concurrency
            # while full response allowances are available.
            while self.reserved and capacity - self.bytes - self.reserved < maximum_bytes:
                await self._condition.wait()
            remaining = capacity - self.bytes - self.reserved
            if remaining <= 0:
                raise SourceScanError(
                    "response_too_large", "Page exhausted its total transfer limit."
                )
            allowance = min(maximum_bytes, remaining)
            self.reserved += allowance
            return allowance

    async def settle(self, allowance: int, consumed: int) -> None:
        async with self._condition:
            self.reserved -= allowance
            self.bytes += consumed
            self._condition.notify_all()


class CDPBridge:
    def __init__(self, endpoint, client, source_url: str, *, maximum_bytes: int):
        self.endpoint = endpoint
        self.client = client
        self.maximum_bytes = maximum_bytes
        self.adapter = WebBlogSourceAdapter(WebBlogSourceConfig(url=source_url), client)
        parsed = urlsplit(source_url)
        self.origin = parsed.scheme, parsed.netloc.lower()
        self.pending = {}
        self.tasks = set()
        self.connections = set()
        self.next_id = -1
        self.upstream = None
        self.session_id = None
        self.main_frame = None
        self.transfer = PageTransfer()
        self.network_slots = asyncio.Semaphore(2)
        self.network_errors = []
        self.network_error_count = 0
        self.closing = False
        self.http = None
        self.runner = None

    async def start(self) -> None:
        self.http = ClientSession(timeout=ClientTimeout(total=15), trust_env=False)
        app = web.Application()
        app.router.add_get("/{path:.*}", self.handle)
        self.runner = web.AppRunner(app, shutdown_timeout=2)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    def reset_errors(self) -> None:
        self.network_errors.clear()
        self.network_error_count = 0

    async def request(self, method, params=None, session_id=None):
        if self.upstream is None or self.closing:
            raise RuntimeError("CLI CDP connection is not ready")
        key = self.next_id
        self.next_id -= 1
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        message = {"id": key, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        try:
            await self.upstream.send_json(message)
            async with asyncio.timeout(15):
                response = await future
            if response.get("error"):
                raise RuntimeError("CDP command failed: " + method)
            return response.get("result", {})
        finally:
            self.pending.pop(key, None)

    async def handle(self, request):
        port = urlsplit(self.endpoint.url).port
        if request.headers.get("Upgrade", "").lower() != "websocket":
            if request.path not in {"/json/version", "/json/list", "/json"}:
                raise web.HTTPNotFound()
            async with self.http.get(f"http://127.0.0.1:{port}{request.path}") as response:
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body) > 64 * 1024:
                        raise web.HTTPBadGateway()
                return web.Response(
                    text=body.decode().replace(f"127.0.0.1:{port}", f"127.0.0.1:{self.port}"),
                    status=response.status,
                    content_type="application/json",
                )
        downstream = web.WebSocketResponse(timeout=2, max_msg_size=CDP_MAX_MESSAGE_BYTES)
        await downstream.prepare(request)
        if self.upstream is not None or self.closing:
            await downstream.close()
            return downstream
        self.connections.add(downstream)
        try:
            async with self.http.ws_connect(
                f"ws://127.0.0.1:{port}{request.path}", max_msg_size=CDP_MAX_MESSAGE_BYTES
            ) as upstream:
                self.upstream = upstream

                async def receive():
                    try:
                        async for frame in upstream:
                            if frame.type != WSMsgType.TEXT:
                                continue
                            message = json.loads(frame.data)
                            key = message.get("id")
                            if key in self.pending:
                                if not self.pending[key].done():
                                    self.pending[key].set_result(message)
                            elif message.get("method") == "Fetch.requestPaused":
                                # Assign each request its page budget before starting
                                # async fetches; late previous-page responses keep theirs.
                                transfer = self._request_transfer(message)
                                task = asyncio.create_task(self.fulfill(message, transfer))
                                self.tasks.add(task)
                                task.add_done_callback(self.tasks.discard)
                            else:
                                await downstream.send_str(frame.data)
                    finally:
                        for future in self.pending.values():
                            if not future.done():
                                future.set_exception(RuntimeError("CLI CDP connection closed"))
                        await downstream.close()

                receiving = asyncio.create_task(receive())
                try:
                    async for frame in downstream:
                        if frame.type != WSMsgType.TEXT:
                            continue
                        message = json.loads(frame.data)
                        method, session = message.get("method"), message.get("sessionId")
                        if method == "Page.navigate" and self.session_id is None:
                            tree = await self.request("Page.getFrameTree", session_id=session)
                            self.main_frame = tree["frameTree"]["frame"]["id"]
                            self.session_id = session
                            await self.request(
                                "Fetch.enable",
                                {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
                                session,
                            )
                        if method == "Fetch.disable":
                            await downstream.send_json(
                                {
                                    "id": message["id"],
                                    "result": {},
                                    **({"sessionId": session} if session else {}),
                                }
                            )
                        else:
                            await upstream.send_str(frame.data)
                finally:
                    receiving.cancel()
                    await asyncio.gather(receiving, return_exceptions=True)
                    self.upstream = None
        finally:
            self.connections.discard(downstream)
        return downstream

    def _top_document(self, message: dict) -> bool:
        params = message["params"]
        parsed = urlsplit(params["request"]["url"])
        return (
            params.get("resourceType") == "Document"
            and self.main_frame is not None
            and params.get("frameId") == self.main_frame
            and message.get("sessionId") == self.session_id
            and (parsed.scheme, parsed.netloc.lower()) == self.origin
            and params["request"]["method"] == "GET"
        )

    def _request_transfer(self, message: dict) -> PageTransfer:
        # Top-level Fetch events cover open, click, back/forward and JS
        # navigations. DOM changes, XHR and subframes never replenish a budget.
        if self._top_document(message):
            self.transfer = PageTransfer()
        return self.transfer

    async def fulfill(self, message: dict, transfer: PageTransfer) -> None:
        params = message["params"]
        request, session = params["request"], message.get("sessionId")
        url, resource = request["url"], params.get("resourceType")
        parsed = urlsplit(url)

        async def abort():
            await self.request(
                "Fetch.failRequest",
                {"requestId": params["requestId"], "errorReason": "BlockedByClient"},
                session,
            )

        try:
            if self.closing:
                return
            if (
                request["method"] != "GET"
                or parsed.scheme not in {"http", "https"}
                or resource in {"Image", "Media", "Font"}
                or (resource == "Document" and not self._top_document(message))
            ):
                await abort()
                return
            transfer.requests += 1
            if transfer.requests > 80:
                raise SourceScanError("web_request_limit", "Page exceeded 80 requests.")
            async with self.network_slots:
                if self.closing:
                    return
                allowance = await transfer.reserve(self.maximum_bytes)
                consumed = 0
                try:
                    if not await self.adapter._robots_allows(url, self.maximum_bytes):
                        raise SourceScanError("robots_disallowed", "Request disallowed.")
                    # A failed/cancelled safe_get may already have read bytes.
                    # Charge its allowance conservatively, but always release
                    # the reservation so waiters cannot be stranded.
                    consumed = allowance
                    response = await safe_get(
                        self.client,
                        url,
                        headers={"User-Agent": _USER_AGENT},
                        timeout=15,
                        max_response_bytes=allowance,
                    )
                    consumed = len(response.content)
                finally:
                    await _settle(asyncio.create_task(transfer.settle(allowance, consumed)))
                if str(response.url) != url:
                    raise SourceScanError(
                        "browser_redirect_unsupported",
                        "Lightpanda cannot safely replay this redirect.",
                    )
                # The unchanged URL already passed robots before safe_get.
                # A changed URL cannot be replayed, so never fetch its robots.
                if resource == "Document":
                    raise_for_provider_status(response, "web")
                headers = [
                    {"name": key, "value": value}
                    for key, value in response.headers.items()
                    if key
                    not in {
                        "content-encoding",
                        "content-length",
                        "transfer-encoding",
                        "set-cookie",
                        "connection",
                    }
                ]
                await self.request(
                    "Fetch.fulfillRequest",
                    {
                        "requestId": params["requestId"],
                        "responseCode": response.status_code,
                        "responseHeaders": headers,
                        "body": base64.b64encode(response.content).decode(),
                    },
                    session,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.network_error_count += 1
            if len(self.network_errors) < 3:
                self.network_errors.append(
                    {
                        "url": url,
                        "resource_type": resource,
                        "code": getattr(exc, "code", type(exc).__name__),
                        "message": str(exc),
                    }
                )
            try:
                await abort()
            except (RuntimeError, TimeoutError, OSError):
                pass

    async def dom(self) -> dict:
        result = await self.request(
            "Runtime.evaluate",
            {
                "expression": (
                    "JSON.stringify({url:location.href,html:document.documentElement.outerHTML})"
                ),
                "returnByValue": True,
            },
            self.session_id,
        )
        state = json.loads(result["result"]["value"])
        if len(state["html"].encode()) > self.maximum_bytes:
            raise SourceScanError("response_too_large", "Page DOM exceeded the response limit.")
        return state

    async def aclose(self) -> None:
        self.closing = True
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        for connection in tuple(self.connections):
            await connection.close()
        if self.http is not None:
            await self.http.close()
        if self.runner is not None:
            await self.runner.cleanup()
