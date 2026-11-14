"""Opaque keyset cursors and the shared list response shape.

A cursor is base64url JSON of the sort key of the last item returned, e.g.
`{"at": "...", "id": "..."}`. Clients treat it as an opaque string.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from reader.core.errors import AppError

_SUPPORTED = (datetime, UUID, int, str)


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(**values: datetime | UUID | int | str) -> str:
    payload: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError(f"cursor field {key} must be timezone-aware")
            payload[key] = value.astimezone(UTC).isoformat()
        elif isinstance(value, UUID):
            payload[key] = str(value)
        elif isinstance(value, int | str):
            payload[key] = value
        else:
            raise TypeError(f"unsupported cursor field type: {type(value).__name__}")
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, **types: type) -> dict[str, Any]:
    """Decode a cursor whose fields have exactly the given types, or raise 422."""
    try:
        if len(cursor) > 1024:
            raise ValueError("cursor too long")
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != set(types):
            raise ValueError("unexpected cursor fields")
        return {key: _parse(payload[key], kind) for key, kind in types.items()}
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise AppError(422, "invalid_cursor", "The cursor is invalid.") from exc


def _parse(value: Any, kind: type) -> Any:
    if kind not in _SUPPORTED:
        raise TypeError(f"unsupported cursor field type: {kind.__name__}")
    if kind is datetime:
        if not isinstance(value, str):
            raise ValueError("datetime must be a string")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return parsed.astimezone(UTC)
    if kind is UUID:
        if not isinstance(value, str):
            raise ValueError("UUID must be a string")
        return UUID(value)
    if kind is int and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError("expected an integer")
    if kind is str and not isinstance(value, str):
        raise ValueError("expected a string")
    return value
