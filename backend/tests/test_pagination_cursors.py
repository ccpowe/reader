from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.feed import _decode_feed_cursor, _encode_feed_cursor
from app.api.saved import _decode_saved_cursor, _encode_saved_cursor


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


@pytest.mark.parametrize("encode, decode", [
    (_encode_feed_cursor, _decode_feed_cursor),
    (_encode_saved_cursor, _decode_saved_cursor),
])
def test_naive_cursor_is_rejected_and_aware_cursor_is_normalised_to_utc(encode, decode) -> None:
    identifier = uuid4()
    with pytest.raises(HTTPException) as error:
        decode(encode(datetime(2026, 8, 1, 8, 30), identifier))
    assert error.value.status_code == 422

    offset_timestamp = datetime.fromisoformat("2026-08-01T08:30:00+08:00")
    assert decode(encode(offset_timestamp, identifier))[0] == datetime(
        2026, 8, 1, 0, 30, tzinfo=UTC
    )
