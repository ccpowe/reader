from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import sources


class _Session:
    def __init__(self, source: object | None) -> None:
        self.source = source
        self.calls = 0
        self.active_transaction = False

    async def scalar(self, _statement: object) -> object | None:
        self.active_transaction = True
        self.calls += 1
        return self.source

    async def rollback(self):
        self.active_transaction = False


class _Upstream:
    def __init__(
        self,
        *,
        content_type: str = "image/png",
        content_length: str | None = None,
        chunks: tuple[bytes, ...] = (b"avatar",),
        status_code: int = 200,
    ) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        if content_length is not None:
            self.headers["content-length"] = content_length
        self.chunks = chunks
        self.iterated = False

    async def aiter_raw(self, *, chunk_size: int):
        assert chunk_size == 64 * 1024
        self.iterated = True
        for chunk in self.chunks:
            yield chunk


def _request(*, etag: str | None = None) -> Request:
    headers = [] if etag is None else [(b"if-none-match", etag.encode())]
    return Request({"type": "http", "headers": headers})


@pytest.fixture(autouse=True)
def clear_avatar_cache() -> None:
    sources._avatar_cache.clear()


async def test_avatar_proxy_hides_unsubscribed_sources_without_fetching(monkeypatch) -> None:
    fetched = False

    @asynccontextmanager
    async def fake_stream(*_args: object, **_kwargs: object):
        nonlocal fetched
        fetched = True
        yield _Upstream()

    monkeypatch.setattr(sources, "safe_stream_get", fake_stream)

    with pytest.raises(HTTPException) as error:
        await sources.proxy_source_avatar(
            uuid4(),
            _request(),
            current_user=SimpleNamespace(id=uuid4()),
            session=_Session(None),
        )

    assert error.value.status_code == 404
    assert fetched is False


async def test_avatar_proxy_rejects_unsafe_content_type_before_reading(monkeypatch) -> None:
    upstream = _Upstream(content_type="image/svg+xml", content_length="100")

    @asynccontextmanager
    async def fake_stream(*_args: object, **_kwargs: object):
        yield upstream

    monkeypatch.setattr(sources, "safe_stream_get", fake_stream)
    source = SimpleNamespace(id=uuid4(), avatar_url="https://images.example/avatar")

    with pytest.raises(HTTPException) as error:
        await sources.proxy_source_avatar(
            source.id,
            _request(),
            current_user=SimpleNamespace(id=uuid4()),
            session=_Session(source),
        )

    assert error.value.status_code == 502
    assert upstream.iterated is False


@pytest.mark.parametrize("content_type", ["image/x-icon", "image/vnd.microsoft.icon"])
async def test_avatar_proxy_accepts_raster_favicon_content_types(
    monkeypatch, content_type: str
) -> None:
    upstream = _Upstream(content_type=content_type, content_length="6")

    @asynccontextmanager
    async def fake_stream(*_args: object, **_kwargs: object):
        yield upstream

    monkeypatch.setattr(sources, "safe_stream_get", fake_stream)
    source = SimpleNamespace(id=uuid4(), avatar_url="https://images.example/favicon.ico")

    response = await sources.proxy_source_avatar(
        source.id,
        _request(),
        current_user=SimpleNamespace(id=uuid4()),
        session=_Session(source),
    )

    assert response.status_code == 200
    assert response.media_type == content_type
    assert response.body == b"avatar"


async def test_avatar_proxy_rejects_declared_oversize_before_reading(monkeypatch) -> None:
    upstream = _Upstream(content_length=str(sources._MAX_AVATAR_BYTES + 1))

    @asynccontextmanager
    async def fake_stream(*_args: object, **_kwargs: object):
        yield upstream

    monkeypatch.setattr(sources, "safe_stream_get", fake_stream)
    source = SimpleNamespace(id=uuid4(), avatar_url="https://images.example/avatar")

    with pytest.raises(HTTPException) as error:
        await sources.proxy_source_avatar(
            source.id,
            _request(),
            current_user=SimpleNamespace(id=uuid4()),
            session=_Session(source),
        )

    assert error.value.status_code == 502
    assert upstream.iterated is False


async def test_avatar_proxy_streams_with_a_hard_size_cap(monkeypatch) -> None:
    upstream = _Upstream(chunks=(b"x" * sources._MAX_AVATAR_BYTES, b"x"))

    @asynccontextmanager
    async def fake_stream(*_args: object, **_kwargs: object):
        yield upstream

    monkeypatch.setattr(sources, "safe_stream_get", fake_stream)
    source = SimpleNamespace(id=uuid4(), avatar_url="https://images.example/avatar")

    with pytest.raises(HTTPException) as error:
        await sources.proxy_source_avatar(
            source.id,
            _request(),
            current_user=SimpleNamespace(id=uuid4()),
            session=_Session(source),
        )

    assert error.value.status_code == 502
    assert upstream.iterated is True


async def test_avatar_proxy_uses_private_cache_after_authorizing_each_request(monkeypatch) -> None:
    upstream = _Upstream(content_length="6")
    fetches = 0

    @asynccontextmanager
    async def fake_stream(*_args: object, **_kwargs: object):
        nonlocal fetches
        fetches += 1
        yield upstream

    monkeypatch.setattr(sources, "safe_stream_get", fake_stream)
    source = SimpleNamespace(id=uuid4(), avatar_url="https://images.example/avatar")
    user = SimpleNamespace(id=uuid4())

    first = await sources.proxy_source_avatar(
        source.id, _request(), current_user=user, session=_Session(source)
    )
    second = await sources.proxy_source_avatar(
        source.id,
        _request(etag=first.headers["etag"]),
        current_user=user,
        session=_Session(source),
    )

    assert fetches == 1
    assert first.body == b"avatar"
    assert first.headers["cache-control"] == "private, max-age=86400"
    assert first.headers["vary"] == "Authorization"
    assert second.status_code == 304


async def test_avatar_proxy_releases_transaction_before_external_io(monkeypatch):
    source_id = uuid4()
    session = _Session(SimpleNamespace(id=source_id, avatar_url="https://images.example/avatar"))

    @asynccontextmanager
    async def fake_stream(_client, url, **_kwargs):
        assert not session.active_transaction
        assert url == "https://images.example/avatar"
        yield _Upstream()

    monkeypatch.setattr(sources, "safe_stream_get", fake_stream)
    response = await sources.proxy_source_avatar(
        source_id, _request(), current_user=SimpleNamespace(id=uuid4()), session=session
    )
    assert response.body == b"avatar"
    assert not session.active_transaction
