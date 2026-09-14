from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.api import saved
from app.core.auth import AuthenticatedUser


@pytest.mark.asyncio
async def test_save_is_a_database_level_idempotent_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    monkeypatch.setattr(saved, "_assert_accessible_content", AsyncMock())
    user = AuthenticatedUser(id=uuid4(), email=None, claims={})
    content_id = uuid4()

    response = await saved.save_content(content_id, current_user=user, session=session)

    statement = session.execute.await_args.args[0]
    rendered = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (user_id, content_id) DO NOTHING" in rendered
    session.commit.assert_awaited_once()
    assert response.content_id == str(content_id)
    assert response.is_saved is True


@pytest.mark.asyncio
async def test_saved_search_is_forwarded_to_the_sql_page_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_page = AsyncMock(return_value=([], None))
    monkeypatch.setattr(saved, "_query_saved_page", query_page)
    user = AuthenticatedUser(id=uuid4(), email=None, claims={})

    await saved.list_saved_content_page(
        limit=20,
        cursor=None,
        q="  Claude  ",
        current_user=user,
        session=AsyncMock(),
    )

    assert query_page.await_args.kwargs["query"] == "Claude"
