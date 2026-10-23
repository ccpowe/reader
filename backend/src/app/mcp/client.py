"""Own the local MCP connection and rotating Reader session.

Credentials come only from the host environment. Requests retain the configured
path prefix, verify TLS, and never follow redirects. A revoked or uncertain
refresh is terminal for this process rather than silently logging back in.
"""

from __future__ import annotations

import asyncio
import json as json_module
import logging
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="READER_MCP_", env_file=None, extra="ignore")

    api_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    server_token: SecretStr = Field(min_length=1)
    email: str = Field(min_length=1)
    password: SecretStr = Field(min_length=1)

    @field_validator("api_url")
    @classmethod
    def credential_free_base(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        parsed = urlsplit(str(value))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API URL must not contain credentials, a query, or a fragment.")
        return value


class ReaderAPIError(RuntimeError):
    """Safe API failure suitable for delivery to the model, without credentials."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ReaderClient:
    """One user/session per local MCP process; concurrent calls share refreshes."""

    def __init__(
        self, settings: MCPSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=str(settings.api_url).rstrip("/") + "/",
            headers={"X-Reader-Server-Token": settings.server_token.get_secret_value()},
            timeout=httpx.Timeout(60, connect=10),
            follow_redirects=False,
            transport=transport,
        )
        self._auth_lock = asyncio.Lock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._session_error: ReaderAPIError | None = None

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        token: str | None = None,
    ) -> httpx.Response:
        # Paths are internal constants, never a model-supplied destination.
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            return await self._http.request(
                method, path.lstrip("/"), params=params, json=json, headers=headers
            )
        except httpx.HTTPError:
            raise ReaderAPIError("Reader API connection failed or timed out.") from None

    @staticmethod
    def _result(response: httpx.Response, *, authentication: bool = False) -> Any:
        if response.is_success:
            try:
                return response.json()
            except ValueError:
                raise ReaderAPIError("Reader API returned an invalid JSON response.") from None
        message = f"Reader API request failed (HTTP {response.status_code})."
        if not authentication:
            try:
                detail = response.json().get("detail")
                if detail is not None:
                    text = detail if isinstance(detail, str) else json_module.dumps(detail)
                    message += " " + text[:1000]
            except (ValueError, AttributeError):
                pass
        raise ReaderAPIError(message, status_code=response.status_code)

    async def _session_token(self, *, rejected_token: str | None = None) -> str:
        async with self._auth_lock:
            if self._session_error is not None:
                raise self._session_error
            fresh = self._access_token is not None and self._expires_at > monotonic() + 30
            if fresh and (rejected_token is None or rejected_token != self._access_token):
                return self._access_token
            refreshing = self._refresh_token is not None
            path = "/v1/auth/refresh" if refreshing else "/v1/auth/login"
            payload = (
                {"refresh_token": self._refresh_token}
                if refreshing
                else {
                    "email": self._settings.email,
                    "password": self._settings.password.get_secret_value(),
                }
            )
            try:
                response = await self._send("POST", path, json=payload)
                data = self._result(response, authentication=True)
                access = data["access_token"]
                refresh = data["refresh_token"]
                lifetime = float(data["expires_in"])
                if not isinstance(access, str) or not isinstance(refresh, str) or lifetime <= 0:
                    raise ValueError("Invalid authentication response")
            except asyncio.CancelledError:
                # The server may already have consumed a rotating refresh token.
                # Never replay that token after the caller cancels its request.
                self._session_error = ReaderAPIError(
                    "Reader authentication was interrupted; restart the MCP server."
                )
                raise
            except (ReaderAPIError, KeyError, TypeError, ValueError) as exc:
                if refreshing:
                    self._session_error = ReaderAPIError(
                        "Reader session refresh failed; restart the MCP server to sign in again.",
                        status_code=exc.status_code if isinstance(exc, ReaderAPIError) else None,
                    )
                    raise self._session_error from None
                if isinstance(exc, ReaderAPIError):
                    raise
                raise ReaderAPIError("Reader API returned an invalid login response.") from None
            self._access_token = access
            self._refresh_token = refresh
            self._expires_at = monotonic() + lifetime
            return access

    async def request(
        self, method: str, path: str, *, params: dict | None = None, json: dict | None = None
    ) -> Any:
        token = await self._session_token()
        response = await self._send(method, path, params=params, json=json, token=token)
        if response.status_code == 401:
            # The deployment gate is distinct from the account session. Refreshing
            # cannot repair a wrong server token, and would rotate unnecessarily.
            try:
                deployment_rejected = response.json().get("detail") == "invalid_server_token"
            except (ValueError, AttributeError):
                deployment_rejected = False
            if not deployment_rejected:
                token = await self._session_token(rejected_token=token)
                response = await self._send(method, path, params=params, json=json, token=token)
        return self._result(response)

    async def aclose(self) -> None:
        try:
            if self._refresh_token is not None:
                try:
                    async with asyncio.timeout(5):
                        response = await self._send(
                            "POST", "/v1/auth/logout", json={"refresh_token": self._refresh_token}
                        )
                        if not response.is_success:
                            logger.warning(
                                "Reader MCP logout failed (HTTP %s).", response.status_code
                            )
                except (ReaderAPIError, TimeoutError):
                    logger.warning("Reader MCP logout did not complete; session will expire.")
        finally:
            await self._http.aclose()
