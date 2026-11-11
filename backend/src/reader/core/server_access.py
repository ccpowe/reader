"""Deployment gate: every request except a few public paths needs the server token."""

from __future__ import annotations

import hmac
import math

from fastapi import Security
from fastapi.security import APIKeyHeader
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from reader.core.errors import error_response
from reader.core.rate_limit import RateLimiter
from reader.core.settings import Settings

SERVER_TOKEN_HEADER = "X-Reader-Server-Token"

# Probes, the OpenAPI document and avatar images (loaded by <Image> without headers).
PUBLIC_PATHS = frozenset({"/health", "/ready", "/worker-ready", "/openapi.json", "/docs"})
PUBLIC_PREFIXES = ("/avatars/", "/docs/")

server_token = APIKeyHeader(
    name=SERVER_TOKEN_HEADER,
    scheme_name="ReaderServerToken",
    auto_error=False,
    description="Deployment connection token configured by the server administrator.",
)


async def document_server_token(_token: str | None = Security(server_token)) -> None:
    """Declares the header in OpenAPI; the middleware below enforces it."""


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


class ServerAccessMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.limiter = RateLimiter(requests=60, window_seconds=60)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path + "/"):
            path = path[len(root_path) :]
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or is_public(path):
            await self.app(scope, receive, send)
            return
        configured = self.settings.server_access_token
        expected = configured.get_secret_value() if configured else ""
        if not expected.strip():
            response = error_response(
                503, "server_not_configured", "The server access token is not configured."
            )
            await response(scope, receive, send)
            return
        client = scope.get("client")
        key = client[0] if client else "unknown"
        wait = self.limiter.retry_after(key)
        if wait is not None:
            response = error_response(
                429,
                "too_many_attempts",
                "Too many connection attempts.",
                retry_after=max(1, math.ceil(wait)),
            )
            await response(scope, receive, send)
            return
        supplied = Headers(scope=scope).getlist(SERVER_TOKEN_HEADER.lower())
        if len(supplied) != 1 or not hmac.compare_digest(
            supplied[0].encode("utf-8"), expected.encode("utf-8")
        ):
            response = error_response(401, "invalid_server_token", "Invalid server token.")
            await response(scope, receive, send)
            return
        self.limiter.refund(key)
        await self.app(scope, receive, send)
