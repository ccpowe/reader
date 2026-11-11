from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.storage.profiles import ensure_profile


@pytest.mark.asyncio
async def test_legacy_profile_creation_is_a_database_level_upsert() -> None:
    session = AsyncMock()

    await ensure_profile(session, uuid4())

    statement = session.execute.await_args.args[0]
    rendered = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (id) DO NOTHING" in rendered






