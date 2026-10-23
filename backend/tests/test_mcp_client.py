"""Connection security and session transitions visible to MCP consumers."""

import asyncio

import httpx
import pytest

from app.mcp.client import MCPSettings, ReaderAPIError, ReaderClient


def _settings():
    return MCPSettings(
        api_url="https://reader.example/reader",
        server_token="deployment-secret",
        email="mcp@example.com",
        password="account-secret",
    )


def _session(access="first", refresh="refresh-first", expires=3600):
    return httpx.Response(
        200, json={"access_token": access, "refresh_token": refresh, "expires_in": expires}
    )


@pytest.mark.asyncio
async def test_concurrent_expired_access_tokens_share_one_refresh():
    logins = refreshes = 0
    both_started = asyncio.Event()
    rejected = 0

    async def upstream(request):
        nonlocal logins, refreshes, rejected
        if request.url.path == "/reader/v1/auth/login":
            logins += 1
            return _session()
        if request.url.path == "/reader/v1/auth/refresh":
            refreshes += 1
            return _session("second", "refresh-second")
        if request.headers.get("authorization") == "Bearer first":
            rejected += 1
            if rejected == 2:
                both_started.set()
            await both_started.wait()
            return httpx.Response(401, json={"detail": "Expired access token."})
        return httpx.Response(200, json={"groups": []})

    client = ReaderClient(_settings(), transport=httpx.MockTransport(upstream))
    try:
        async with asyncio.timeout(5):
            await asyncio.gather(
                client.request("GET", "/v1/mcp/updates/summary"),
                client.request("GET", "/v1/mcp/updates/summary"),
            )
        assert logins == 1
        assert refreshes == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_revoked_refresh_is_terminal_and_does_not_relogin():
    logins = refreshes = 0

    async def upstream(request):
        nonlocal logins, refreshes
        if request.url.path.endswith("/login"):
            logins += 1
            return _session(expires=1)
        if request.url.path.endswith("/refresh"):
            refreshes += 1
            return httpx.Response(401, json={"detail": "Session revoked."})
        return httpx.Response(200, json={"items": []})

    client = ReaderClient(_settings(), transport=httpx.MockTransport(upstream))
    try:
        await client.request("GET", "/v1/sources")
        for _ in range(2):
            with pytest.raises(ReaderAPIError, match="restart the MCP server"):
                await client.request("GET", "/v1/sources")
        assert (logins, refreshes) == (1, 1)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_login_redirect_cannot_forward_credentials():
    destinations = []

    async def upstream(request):
        destinations.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://untrusted.example/login"})

    client = ReaderClient(_settings(), transport=httpx.MockTransport(upstream))
    try:
        with pytest.raises(ReaderAPIError) as error:
            await client.request("GET", "/v1/sources")
        assert error.value.status_code == 307
        assert destinations == ["https://reader.example/reader/v1/auth/login"]
        assert "account-secret" not in str(error.value)
        assert "deployment-secret" not in str(error.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cancelled_refresh_is_not_replayed_and_old_token_can_logout():
    started = asyncio.Event()
    refreshes = logouts = 0

    async def upstream(request):
        nonlocal refreshes, logouts
        if request.url.path.endswith("/login"):
            return _session(expires=1)
        if request.url.path.endswith("/refresh"):
            refreshes += 1
            started.set()
            await asyncio.Event().wait()
        if request.url.path.endswith("/logout"):
            logouts += 1
            return httpx.Response(204)
        return httpx.Response(200, json=[])

    client = ReaderClient(_settings(), transport=httpx.MockTransport(upstream))
    try:
        await client.request("GET", "/v1/sources")
        request = asyncio.create_task(client.request("GET", "/v1/sources"))
        async with asyncio.timeout(5):
            await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        with pytest.raises(ReaderAPIError, match="interrupted"):
            await client.request("GET", "/v1/sources")
        assert refreshes == 1
    finally:
        await client.aclose()
    assert logouts == 1
