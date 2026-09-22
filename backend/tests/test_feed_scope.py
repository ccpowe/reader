from __future__ import annotations

import base64
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.dialects import postgresql

from app.api import feed
from app.api.sources import _decode_channel_update_token, _encode_channel_update_token
from app.core.auth import AuthenticatedUser


def _compiled_scope(*, folder_name: str | None, source_id=None) -> str:
    clause = and_(
        *feed._feed_scope_filters(
            user_id=uuid4(),
            folder_name=folder_name,
            source_id=source_id,
        )
    )
    return str(clause.compile(dialect=postgresql.dialect()))


def test_source_feed_scope_keeps_subscription_authorization() -> None:
    sql = _compiled_scope(folder_name=None, source_id=uuid4())

    assert "source_subscriptions.user_id" in sql
    assert "source_subscriptions.is_enabled IS true" in sql
    assert "feed_sources.id" in sql
    assert "include_in_home" not in sql


def test_unfiltered_home_scope_only_includes_selected_sources() -> None:
    sql = _compiled_scope(folder_name=None)

    assert "source_subscriptions.include_in_home IS true" in sql


def test_folder_feed_scope_preserves_uncategorized_semantics() -> None:
    uncategorized_sql = _compiled_scope(folder_name="__uncategorized__")
    folder_sql = _compiled_scope(folder_name="AI")

    assert "source_subscriptions.folder_name IS NULL" in uncategorized_sql
    assert "source_subscriptions.folder_name =" in folder_sql
    assert "include_in_home" not in uncategorized_sql
    assert "include_in_home" not in folder_sql


def test_channel_update_token_is_source_bound_and_rejects_invalid_bytes() -> None:
    source_id = uuid4()
    token = _encode_channel_update_token(source_id, 17)

    assert _decode_channel_update_token(token) == (source_id, 17)
    with pytest.raises(HTTPException, match="Invalid channel update token"):
        _decode_channel_update_token("_w")
    numeric_source = base64.urlsafe_b64encode(
        b'{"source_id":123,"sequence":1}'
    ).decode("ascii")
    with pytest.raises(HTTPException, match="Invalid channel update token"):
        _decode_channel_update_token(numeric_source)


@pytest.mark.asyncio
async def test_empty_channel_has_confirmable_zero_snapshot_without_writes() -> None:
    source_id = uuid4()
    session = AsyncMock()
    session.scalar.return_value = 0

    token = await feed._channel_update_snapshot(
        session,
        user_id=uuid4(),
        source_id=source_id,
    )

    assert token is not None
    assert _decode_channel_update_token(token) == (source_id, 0)
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_filter_is_applied_before_feed_paging(monkeypatch) -> None:
    statements = []
    source_id = uuid4()
    user = AuthenticatedUser(id=uuid4(), email=None, claims={})

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return []

        async def commit(self):
            return None

    async def translation_context(*_args, **_kwargs):
        return object()

    async def project_titles(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(feed, "get_title_translation_context", translation_context)
    monkeypatch.setattr(feed, "project_titles", project_titles)

    items, next_cursor = await feed._query_feed_page(
        Session(),  # type: ignore[arg-type]
        current_user=user,
        folder_name=None,
        source_id=source_id,
        limit=20,
        cursor=None,
    )

    assert items == []
    assert next_cursor is None
    assert len(statements) == 1
    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert str(source_id) in sql
    assert str(user.id) in sql
    assert "LIMIT 21" in sql
