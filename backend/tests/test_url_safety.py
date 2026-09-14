import gzip
import ipaddress
import threading
import time
import zlib
from unittest.mock import AsyncMock, Mock

import httpcore
import httpx
import pytest

from app.ingestion.models import SourceScanError
from app.ingestion.url_safety import (
    PublicAsyncClient,
    UnsafeSourceUrl,
    _all_trusted_proxy_fake_ips,
    _PublicNetworkBackend,
    _resolve_public_addresses,
    safe_get,
)


def test_mihomo_fake_ips_are_not_trusted_when_setting_is_disabled(
    monkeypatch,
) -> None:
    addresses = {ipaddress.ip_address("198.18.1.42")}
    monkeypatch.setattr(
        "app.ingestion.url_safety.get_settings",
        lambda: type("Settings", (), {"trust_proxy_fake_ip": False})(),
    )

    assert _all_trusted_proxy_fake_ips(addresses) is False


@pytest.mark.asyncio
async def test_safe_get_stops_streaming_at_the_response_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ingestion.url_safety.validate_public_http_url",
        AsyncMock(return_value="https://example.com/feed"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"too-large-body", request=request)

    async with PublicAsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceScanError) as error:
            await safe_get(
                client,
                "https://example.com/feed",
                max_response_bytes=4,
            )

    assert error.value.code == "response_too_large"


@pytest.mark.asyncio
async def test_safe_get_does_not_decode_compressed_content_twice(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ingestion.url_safety.validate_public_http_url",
        AsyncMock(return_value="https://example.com/feed"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_RawStream(gzip.compress(b"decoded feed")),
            headers={"content-encoding": "gzip"},
            request=request,
        )

    async with PublicAsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await safe_get(
            client,
            "https://example.com/feed",
            max_response_bytes=100,
        )

    assert response.text == "decoded feed"
    assert "content-encoding" not in response.headers


@pytest.mark.asyncio
async def test_public_network_backend_connects_to_the_validated_ip_without_second_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegate = AsyncMock()
    delegate.connect_tcp.return_value = object()
    resolve = AsyncMock(return_value=(ipaddress.ip_address("93.184.216.34"),))
    monkeypatch.setattr("app.ingestion.url_safety._resolve_public_addresses", resolve)
    backend = _PublicNetworkBackend(delegate=delegate)

    stream = await backend.connect_tcp("rebind.example", 443, timeout=2.0)

    assert stream is delegate.connect_tcp.return_value
    resolve.assert_awaited_once_with("rebind.example", 443, timeout=2.0)
    attempt = delegate.connect_tcp.await_args
    assert attempt.args == ("93.184.216.34", 443)
    assert 0 < attempt.kwargs["timeout"] <= 2.0
    assert attempt.kwargs["local_address"] is None
    assert attempt.kwargs["socket_options"] is None


@pytest.mark.asyncio
async def test_public_network_backend_preserves_budget_for_later_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegate = AsyncMock()
    stream = object()
    delegate.connect_tcp.side_effect = [httpcore.ConnectTimeout(), stream]
    resolve = AsyncMock(
        return_value=(
            ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946"),
            ipaddress.ip_address("93.184.216.34"),
        )
    )
    monkeypatch.setattr("app.ingestion.url_safety._resolve_public_addresses", resolve)
    backend = _PublicNetworkBackend(delegate=delegate)

    result = await backend.connect_tcp("dual-stack.example", 443, timeout=2.0)

    assert result is stream
    attempts = delegate.connect_tcp.await_args_list
    assert attempts[0].kwargs["timeout"] == 1.5
    assert 0 < attempts[1].kwargs["timeout"] <= 2.0
    assert attempts[0].args[0] == "2606:2800:220:1:248:1893:25c8:1946"
    assert attempts[1].args[0] == "93.184.216.34"


@pytest.mark.asyncio
async def test_public_address_resolution_has_a_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_resolution(*_args, **_kwargs):
        time.sleep(0.1)
        return [(None, None, None, None, ("93.184.216.34", 443))]

    monkeypatch.setattr("app.ingestion.url_safety.socket.getaddrinfo", slow_resolution)

    with pytest.raises(ValueError, match="resolution timed out"):
        await _resolve_public_addresses("slow-dns.example", 443, timeout=0.01)


@pytest.mark.asyncio
async def test_public_address_resolution_does_not_use_the_default_thread_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver_threads: list[str] = []

    def resolve(*_args, **_kwargs):
        resolver_threads.append(threading.current_thread().name)
        return [(None, None, None, None, ("93.184.216.34", 443))]

    monkeypatch.setattr(
        "app.ingestion.url_safety.socket.getaddrinfo",
        resolve,
    )

    await _resolve_public_addresses("isolated-resolver.example", 443, timeout=1)

    assert resolver_threads[0].startswith("reader-dns")


@pytest.mark.asyncio
async def test_overlong_hostname_label_is_rejected_before_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = Mock()
    monkeypatch.setattr("app.ingestion.url_safety.socket.getaddrinfo", lookup)

    with pytest.raises(UnsafeSourceUrl, match="hostname is invalid"):
        await _resolve_public_addresses(f"{'a' * 64}.example", 443, timeout=1)

    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_safe_get_rejects_a_client_that_can_reresolve_the_hostname() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None)) as client:
        with pytest.raises(TypeError, match="PublicAsyncClient"):
            await safe_get(client, "https://example.com/feed")


class _RawStream(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        yield self.body


@pytest.mark.asyncio
async def test_safe_get_bounds_gzip_allocation_before_rejecting(monkeypatch):
    monkeypatch.setattr(
        "app.ingestion.url_safety.validate_public_http_url", AsyncMock()
    )
    allocations = []
    original = zlib.decompressobj

    class Decoder:
        def __init__(self, *args):
            self.decoder = original(*args)

        def decompress(self, data, max_length=0):
            assert 0 < max_length <= 4097
            result = self.decoder.decompress(data, max_length)
            allocations.append(len(result))
            return result

        def __getattr__(self, name):
            return getattr(self.decoder, name)

    monkeypatch.setattr("app.ingestion.url_safety.zlib.decompressobj", Decoder)
    body = gzip.compress(b"x" * 1024 * 1024)
    async with PublicAsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, stream=_RawStream(body), headers={"content-encoding": "gzip"}
        )
    )) as client:
        with pytest.raises(SourceScanError, match="size limit") as error:
            await safe_get(client, "https://example.com/feed", max_response_bytes=4096)
    assert error.value.code == "response_too_large"
    assert allocations == [4097]


@pytest.mark.asyncio
@pytest.mark.parametrize("body,expected", [
    (gzip.compress(b"one") + gzip.compress(b"two"), b"onetwo"),
    (gzip.compress(b""), b""),
])
async def test_safe_get_accepts_complete_gzip_members(monkeypatch, body, expected):
    monkeypatch.setattr("app.ingestion.url_safety.validate_public_http_url", AsyncMock())
    async with PublicAsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, stream=_RawStream(body), headers={"content-encoding": "gzip"}
        )
    )) as client:
        result = await safe_get(client, "https://example.com/feed", max_response_bytes=100)
    assert result.content == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding,body,error", [
    ("gzip", gzip.compress(b"test")[:-1], httpx.DecodingError),
    ("gzip", b"broken", httpx.DecodingError),
    ("br", b"ignored", SourceScanError),
])
async def test_safe_get_rejects_unsupported_or_invalid_compression(
    monkeypatch, encoding, body, error
):
    monkeypatch.setattr("app.ingestion.url_safety.validate_public_http_url", AsyncMock())
    async with PublicAsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, stream=_RawStream(body), headers={"content-encoding": encoding}
        )
    )) as client:
        with pytest.raises(error):
            await safe_get(client, "https://example.com/feed", max_response_bytes=100)
