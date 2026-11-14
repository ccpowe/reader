from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from reader.core.errors import AppError
from reader.core.pagination import Page, decode_cursor, encode_cursor


def test_cursor_round_trips_typed_fields_in_utc() -> None:
    at = datetime(2026, 11, 10, 9, 30, tzinfo=timezone(timedelta(hours=8)))
    item = uuid4()

    cursor = encode_cursor(at=at, id=item, sequence=7)
    decoded = decode_cursor(cursor, at=datetime, id=type(item), sequence=int)

    assert decoded == {"at": at.astimezone(UTC), "id": item, "sequence": 7}
    assert decoded["at"].tzinfo is UTC


@pytest.mark.parametrize(
    "cursor",
    [
        "not base64 !!",
        encode_cursor(at=datetime(2026, 1, 1, tzinfo=UTC)),  # missing "id"
        encode_cursor(at=datetime(2026, 1, 1, tzinfo=UTC), id="x", extra=1),
        encode_cursor(at="2026-01-01T00:00:00", id=str(uuid4())),  # naive time
        encode_cursor(at=datetime(2026, 1, 1, tzinfo=UTC), id="not-a-uuid"),
        "a" * 2000,
    ],
)
def test_bad_cursor_is_a_422_invalid_cursor(cursor: str) -> None:
    from uuid import UUID

    with pytest.raises(AppError) as raised:
        decode_cursor(cursor, at=datetime, id=UUID)

    assert (raised.value.status, raised.value.code) == (422, "invalid_cursor")


def test_naive_datetime_cannot_be_encoded() -> None:
    with pytest.raises(ValueError):
        encode_cursor(at=datetime(2026, 1, 1))


def test_page_shape() -> None:
    assert Page[int](items=[1, 2], next_cursor="c").model_dump() == {
        "items": [1, 2],
        "next_cursor": "c",
    }
