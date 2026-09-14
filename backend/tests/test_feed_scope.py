from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import and_
from sqlalchemy.dialects import postgresql

from app.api import feed
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


def test_folder_feed_scope_preserves_uncategorized_semantics() -> None:
    uncategorized_sql = _compiled_scope(folder_name="__uncategorized__")
    folder_sql = _compiled_scope(folder_name="AI")

    assert "source_subscriptions.folder_name IS NULL" in uncategorized_sql
    assert "source_subscriptions.folder_name =" in folder_sql


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
