"""/mcp: the MCP Streamable HTTP endpoint, stateless, inside the API process.

The deployment gate (X-Reader-Server-Token) is checked by ServerAccessMiddleware
like every other route. Here the Bearer token picks the user; failed checks count
against the same per-client budget as failed access tokens.
"""

from __future__ import annotations

import json
import math
from contextlib import AbstractAsyncContextManager

from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from starlette.types import Receive, Scope, Send

from reader.accounts.service import user_for_mcp_token
from reader.core.settings import get_settings
from reader.mcp.server import CALLER, Caller, create_server
from reader.mcp.transcripts import TranscriptReader


class McpEndpoint:
    """An ASGI app for the /mcp route; `running()` must wrap the app's lifetime."""

    def __init__(self) -> None:
        self._server = create_server()
        # Builds the session manager; stateless + JSON replies suit plain request/response
        # tools and any number of API processes. host="0.0.0.0" skips the SDK's
        # localhost-only Host check: access is guarded by the two tokens instead.
        self._server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
        self._handler = StreamableHTTPASGIApp(self._server.session_manager)
        self.transcripts = TranscriptReader()

    def running(self) -> AbstractAsyncContextManager:
        return self._server.session_manager.run()

    async def aclose(self) -> None:
        await self.transcripts.aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        app = scope["app"]
        headers = {key.lower(): value for key, value in scope["headers"]}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
        client = scope.get("client")
        # The same key as accounts.auth.client_key: one budget for every failed token.
        key = client[0] if client else "unknown"
        limiter = app.state.token_limiter
        wait = limiter.retry_after(key)
        if wait is not None:
            await _error(
                send,
                429,
                "too_many_attempts",
                "Too many authentication attempts.",
                {"retry-after": str(max(1, math.ceil(wait)))},
            )
            return
        factory = app.state.session_factory
        if factory is None:
            await _error(send, 503, "database_unavailable", "The database is not configured.")
            return
        async with factory() as db:
            user_id = await user_for_mcp_token(db, token) if token else None
        if user_id is None:
            await _error(
                send,
                401,
                "invalid_mcp_token",
                "Send Authorization: Bearer <MCP token> (reader-admin mcp-token create).",
                {"www-authenticate": "Bearer"},
            )
            return
        limiter.refund(key)
        # Tasks the SDK starts for this request copy the context, so tools see the caller.
        CALLER.set(Caller(user_id, factory, get_settings(), app, self.transcripts))
        await self._handler(scope, receive, send)


async def _error(
    send: Send, status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    raw += [(name.encode(), value.encode()) for name, value in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})
