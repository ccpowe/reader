from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api.feed import _decode_feed_cursor, _encode_feed_cursor
from app.api.saved import _decode_saved_cursor, _encode_saved_cursor
from app.core.auth import AuthenticatedUser, require_current_user
from app.core.settings import Settings
from app.main import create_app
from app.storage.database import get_session


@pytest.mark.parametrize(
    ("encode", "decode"),
    [(_encode_feed_cursor, _decode_feed_cursor), (_encode_saved_cursor, _decode_saved_cursor)],
)
def test_cursor_round_trip_preserves_keyset_boundary(encode, decode) -> None:
    timestamp = datetime(2026, 8, 1, 8, 30, tzinfo=UTC)
    identifier = uuid4()

    assert decode(encode(timestamp, identifier)) == (timestamp, identifier)


@pytest.mark.parametrize("decode", [_decode_feed_cursor, _decode_saved_cursor])
def test_invalid_cursor_is_reported_as_422(decode) -> None:
    with pytest.raises(HTTPException) as error:
        decode("not-a-valid-cursor")

    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "encode, decode",
    [
        (_encode_feed_cursor, _decode_feed_cursor),
        (_encode_saved_cursor, _decode_saved_cursor),
    ],
)
def test_naive_cursor_is_rejected_and_aware_cursor_is_normalised_to_utc(encode, decode) -> None:
    identifier = uuid4()
    with pytest.raises(HTTPException) as error:
        decode(encode(datetime(2026, 8, 1, 8, 30), identifier))
    assert error.value.status_code == 422

    offset_timestamp = datetime.fromisoformat("2026-08-01T08:30:00+08:00")
    assert decode(encode(offset_timestamp, identifier))[0] == datetime(
        2026, 8, 1, 0, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("path", "identifier_key", "detail"),
    [
        ("/v1/feed/page", "entry_id", "Invalid feed cursor."),
        ("/v1/saved/page", "content_id", "Invalid saved cursor."),
    ],
)
@pytest.mark.parametrize("invalid_id", [123, None, [], {}])
@pytest.mark.asyncio
async def test_page_cursor_with_non_string_uuid_returns_422_over_http(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    identifier_key: str,
    detail: str,
    invalid_id: object,
) -> None:
    settings = Settings(_env_file=None, server_access_token="cursor-test-token")
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[require_current_user] = lambda: AuthenticatedUser(
        id=uuid4(), email=None, claims={}
    )
    app.dependency_overrides[get_session] = lambda: object()
    payload = json.dumps({"at": "2026-08-01T08:30:00+00:00", identifier_key: invalid_id})
    cursor = base64.urlsafe_b64encode(payload.encode()).decode("ascii").rstrip("=")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            path,
            params={"cursor": cursor},
            headers={"X-Reader-Server-Token": "cursor-test-token"},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": detail}
