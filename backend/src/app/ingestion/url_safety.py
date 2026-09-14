"""Safe outbound URL validation for user-supplied sources.

The source URL is untrusted input. Requests must not be able to reach local
services, private network addresses, or credentials embedded in a URL.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import zlib
from collections.abc import AsyncIterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from time import monotonic
from urllib.parse import urljoin, urlsplit
from weakref import WeakKeyDictionary

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from app.core.settings import get_settings
from app.ingestion.models import SourceScanError


class UnsafeSourceUrl(ValueError):
    """Raised when a source URL could target a non-public destination."""


logger = logging.getLogger(__name__)
_MIHOMO_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_DNS_MAX_WORKERS = 4
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class _BoundedDnsResolver:
    """Isolate blocking libc DNS and bound unresolved calls across requests."""

    def __init__(self, max_workers: int) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="reader-dns",
        )
        self._max_workers = max_workers
        self._slots_by_loop: WeakKeyDictionary = WeakKeyDictionary()

    async def getaddrinfo(self, hostname: str, port: int):
        loop = asyncio.get_running_loop()
        slots = self._slots_by_loop.get(loop)
        if slots is None:
            slots = asyncio.Semaphore(self._max_workers)
            self._slots_by_loop[loop] = slots
        await slots.acquire()
        try:
            future = self._executor.submit(
                socket.getaddrinfo,
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except BaseException:
            slots.release()
            raise

        # A timed-out asyncio waiter cannot stop libc getaddrinfo. Keep its slot
        # occupied until the actual thread finishes so attackers cannot create
        # unbounded resolver threads or consume the default application pool.
        def release_slot(_future) -> None:
            try:
                loop.call_soon_threadsafe(slots.release)
            except RuntimeError:
                # The request loop may already be gone when a timed-out libc
                # resolver finally returns. Its per-loop semaphore dies too.
                pass

        future.add_done_callback(release_slot)
        return await asyncio.wrap_future(future, loop=loop)


_DNS_RESOLVER = _BoundedDnsResolver(_DNS_MAX_WORKERS)


class _PublicNetworkBackend:
    """Resolve, validate, and connect to the exact validated address.

    HTTP Core still sees the original hostname, so it retains the correct Host
    header and TLS SNI. Only the physical TCP destination is replaced with the
    already-validated IP literal.
    """

    def __init__(self, *, delegate=None) -> None:
        self._delegate = delegate or AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        deadline = monotonic() + timeout if timeout is not None else None
        settings = get_settings()
        resolution_timeout = settings.ingestion_dns_timeout_seconds
        if timeout is not None:
            resolution_timeout = min(resolution_timeout, timeout)
        addresses = await _resolve_public_addresses(host, port, timeout=resolution_timeout)
        last_error: Exception | None = None
        for index, address in enumerate(addresses):
            remaining = None if deadline is None else max(deadline - monotonic(), 0.0)
            if remaining is not None and remaining <= 0:
                last_error = httpcore.ConnectTimeout("Public destination connection timed out.")
                break
            attempt_timeout = remaining
            if index < len(addresses) - 1:
                attempt_timeout = min(
                    settings.ingestion_connect_attempt_timeout_seconds,
                    remaining
                    if remaining is not None
                    else settings.ingestion_connect_attempt_timeout_seconds,
                )
            try:
                return await self._delegate.connect_tcp(
                    str(address),
                    port,
                    timeout=attempt_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(self, *_args, **_kwargs):
        raise httpcore.ConnectError("Unix sockets are not valid public HTTP destinations.")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class _PublicAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport backed by a rebinding-safe public network connector."""

    def __init__(self) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
            network_backend=_PublicNetworkBackend(),
        )


class PublicAsyncClient(httpx.AsyncClient):
    """HTTP client whose real sockets can reach only freshly validated public IPs."""

    def __init__(self, *args, transport=None, **kwargs) -> None:
        # Explicit transports are used by deterministic tests and do not open
        # sockets. Production callers always receive the guarded transport.
        kwargs["trust_env"] = False
        super().__init__(
            *args,
            transport=transport if transport is not None else _PublicAsyncHTTPTransport(),
            **kwargs,
        )


def validate_http_url(url: str) -> str:
    """Validate URL syntax before any DNS or HTTP request."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeSourceUrl("Invalid URL.") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeSourceUrl("Only HTTP and HTTPS source URLs are allowed.")
    if not parsed.hostname:
        raise UnsafeSourceUrl("Source URL has no hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeSourceUrl("Source URL must not contain embedded credentials.")
    if port is not None and not 1 <= port <= 65535:
        raise UnsafeSourceUrl("Source URL port is invalid.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeSourceUrl("Localhost is not a valid source destination.")
    return url


async def validate_public_http_url(url: str) -> str:
    """Resolve a hostname and reject every non-public resolved address."""
    validate_http_url(url)
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

    await _resolve_public_addresses(
        hostname,
        port,
        timeout=get_settings().ingestion_dns_timeout_seconds,
    )
    return url


async def _resolve_public_addresses(
    hostname: str,
    port: int,
    *,
    timeout: float | None = None,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    normalized_host = hostname.rstrip(".")
    try:
        raw_addresses = [ipaddress.ip_address(normalized_host)]
    except ValueError:
        normalized_host = _validated_dns_hostname(normalized_host)
        try:
            resolution_timeout = (
                get_settings().ingestion_dns_timeout_seconds if timeout is None else timeout
            )
            async with asyncio.timeout(resolution_timeout):
                resolved = await _DNS_RESOLVER.getaddrinfo(normalized_host, port)
        except TimeoutError as exc:
            raise UnsafeSourceUrl("Source hostname resolution timed out.") from exc
        except (UnicodeError, socket.gaierror) as exc:
            raise UnsafeSourceUrl("Source hostname could not be resolved.") from exc
        raw_addresses = [ipaddress.ip_address(result[4][0].split("%", 1)[0]) for result in resolved]

    addresses = tuple(dict.fromkeys(raw_addresses))
    if not addresses:
        raise UnsafeSourceUrl("Source URL resolves to a non-public address.")
    address_set = set(addresses)
    if _all_trusted_proxy_fake_ips(address_set):
        logger.warning(
            "Allowing Mihomo fake-IP hostname in local development: host=%s", normalized_host
        )
        return addresses
    if any(not address.is_global for address in addresses):
        raise UnsafeSourceUrl("Source URL resolves to a non-public address.")
    return addresses


def _validated_dns_hostname(hostname: str) -> str:
    """Return bounded IDNA ASCII or reject input before libc resolver work."""
    if not hostname:
        raise UnsafeSourceUrl("Source hostname is invalid.")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeSourceUrl("Source hostname is invalid.") from exc
    labels = ascii_hostname.split(".")
    if len(ascii_hostname) > 253 or any(
        not label or len(label) > 63 or _DNS_LABEL.fullmatch(label) is None for label in labels
    ):
        raise UnsafeSourceUrl("Source hostname is invalid.")
    return ascii_hostname.lower()


def _all_trusted_proxy_fake_ips(
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> bool:
    """Allow only Mihomo's fake-IP range when explicitly enabled for local development."""
    return get_settings().trust_proxy_fake_ip and all(
        isinstance(address, ipaddress.IPv4Address) and address in _MIHOMO_FAKE_IP_NETWORK
        for address in addresses
    )


async def safe_get(
    client: PublicAsyncClient,
    url: str,
    *,
    max_redirects: int = 5,
    max_response_bytes: int | None = None,
    **kwargs: object,
) -> httpx.Response:
    """GET a URL while validating the initial target and every redirect."""
    _require_public_client(client)
    if max_response_bytes is not None:
        stream_kwargs = dict(kwargs)
        supplied_headers = stream_kwargs.get("headers")
        bounded_headers = dict(supplied_headers) if isinstance(supplied_headers, Mapping) else {}
        # Avoid a small compressed response expanding into an unexpectedly
        # large decoder allocation before the decoded-byte limit can fire.
        bounded_headers.setdefault("Accept-Encoding", "identity")
        stream_kwargs["headers"] = bounded_headers
        async with safe_stream_get(
            client,
            url,
            max_redirects=max_redirects,
            **stream_kwargs,
        ) as response:
            declared = response.headers.get("content-length")
            if declared:
                try:
                    if int(declared) > max_response_bytes:
                        raise SourceScanError(
                            "response_too_large", "Upstream response exceeded size limit."
                        )
                except ValueError:
                    pass
            content = await _read_bounded_body(response, max_response_bytes)
            # The returned body is decoded; prevent HTTPX decoding it again.
            headers = response.headers.copy()
            for header in ("content-encoding", "content-length", "transfer-encoding"):
                if header in headers:
                    del headers[header]
            return httpx.Response(
                response.status_code,
                headers=headers,
                content=bytes(content),
                request=response.request,
                extensions=response.extensions,
            )

    current_url = url
    for redirect_count in range(max_redirects + 1):
        await validate_public_http_url(current_url)
        response = await client.get(current_url, follow_redirects=False, **kwargs)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("location")
        if location is None:
            return response
        if redirect_count == max_redirects:
            raise UnsafeSourceUrl("Source URL redirected too many times.")
        current_url = urljoin(current_url, location)
    raise UnsafeSourceUrl("Source URL redirected too many times.")


async def _read_bounded_body(response: httpx.Response, limit: int) -> bytes:
    """Bound allocations before decoding an untrusted compressed body."""
    if limit < 0:
        raise ValueError("max_response_bytes must be non-negative")
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "", "gzip", "x-gzip"}:
        # We request identity. Only gzip has a bounded decoder here; never
        # silently fall back to HTTPX's unbounded brotli/deflate decoders.
        raise SourceScanError("unsupported_content_encoding", "Unsupported response encoding.")
    if response.is_stream_consumed:
        # Explicit test/custom transports can return an already buffered response.
        if len(response.content) > limit:
            raise SourceScanError("response_too_large", "Upstream response exceeded size limit.")
        return response.content
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding in {"gzip", "x-gzip"} else None
    content = bytearray()
    wire_bytes = 0
    try:
        async for raw in response.aiter_raw(chunk_size=64 * 1024):
            wire_bytes += len(raw)
            if wire_bytes > limit:
                raise SourceScanError(
                    "response_too_large", "Upstream response exceeded size limit."
                )
            if decoder is None:
                content.extend(raw)
                continue
            while raw:
                if decoder.eof:
                    # RFC 1952 permits concatenated gzip members.
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                decoded = decoder.decompress(raw, max(1, limit - len(content) + 1))
                if len(content) + len(decoded) > limit:
                    raise SourceScanError(
                        "response_too_large", "Upstream response exceeded size limit."
                    )
                content.extend(decoded)
                raw = decoder.unused_data if decoder.eof else decoder.unconsumed_tail
        if decoder is not None and not decoder.eof:
            raise httpx.DecodingError("Incomplete gzip response")
    except zlib.error as exc:
        raise httpx.DecodingError("Invalid gzip response") from exc
    return bytes(content)


@asynccontextmanager
async def safe_stream_get(
    client: PublicAsyncClient,
    url: str,
    *,
    max_redirects: int = 5,
    **kwargs: object,
) -> AsyncIterator[httpx.Response]:
    """Stream a GET while validating its initial target and every redirect.

    Unlike :func:`safe_get`, this never buffers the response body.  Consumers
    which handle untrusted, potentially large responses should use it and
    impose their own streaming size limit.
    """
    _require_public_client(client)
    current_url = url
    for redirect_count in range(max_redirects + 1):
        await validate_public_http_url(current_url)
        async with client.stream("GET", current_url, follow_redirects=False, **kwargs) as response:
            if response.status_code not in {301, 302, 303, 307, 308}:
                yield response
                return
            location = response.headers.get("location")
            if location is None:
                yield response
                return
            if redirect_count == max_redirects:
                raise UnsafeSourceUrl("Source URL redirected too many times.")
            current_url = urljoin(current_url, location)
    raise UnsafeSourceUrl("Source URL redirected too many times.")


def _require_public_client(client: httpx.AsyncClient) -> None:
    if not isinstance(client, PublicAsyncClient):
        raise TypeError("safe_get requires a PublicAsyncClient with pinned public DNS.")
