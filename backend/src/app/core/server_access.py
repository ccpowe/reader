"""Deployment gate that executes before discovery and all versioned API routing."""

import hmac
import math

from fastapi import Security
from fastapi.security import APIKeyHeader
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.auth import AuthRateLimiter
from app.core.settings import Settings

server_token_header = APIKeyHeader(
    name="X-Reader-Server-Token",
    scheme_name="ReaderServerToken",
    auto_error=False,
    description="Deployment connection token configured by the server administrator.",
)


async def document_server_token(_token: str | None = Security(server_token_header)) -> None:
    """Declare the header in OpenAPI; ASGI middleware enforces it before routing."""


class ServerAccessMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.limiter = AuthRateLimiter(requests=60, window_seconds=60)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path + "/"):
            path = path[len(root_path) :]
        protected = path == "/.well-known/reader.json" or path == "/v1" or path.startswith("/v1/")
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or not protected:
            await self.app(scope, receive, send)
            return
        configured = self.settings.server_access_token
        expected = configured.get_secret_value() if configured else ""
        if not expected.strip():
            await JSONResponse(status_code=503, content={"detail": "server_access_not_configured"})(
                scope, receive, send
            )
            return
        client = scope.get("client")
        key = client[0] if client else "unknown"
        retry = self.limiter.retry_after(key)
        if retry is not None:
            await JSONResponse(
                status_code=429,
                content={"detail": "Too many connection attempts."},
                headers={"Retry-After": str(max(1, math.ceil(retry)))},
            )(scope, receive, send)
            return
        supplied = Headers(scope=scope).getlist("x-reader-server-token")
        valid = len(supplied) == 1 and hmac.compare_digest(
            supplied[0].encode("utf-8"), expected.encode("utf-8")
        )
        if not valid:
            await JSONResponse(status_code=401, content={"detail": "invalid_server_token"})(
                scope, receive, send
            )
            return
        self.limiter.refund(key)
        await self.app(scope, receive, send)
