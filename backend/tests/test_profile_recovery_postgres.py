"""Legacy account recovery must persist across independent database sessions."""

from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine
from test_postgres_migrations import _assert_disposable_database, _test_dsn, _test_guard_token

from app.api.profile import get_profile
from app.core.auth import AuthenticatedUser
from app.services.ranking_saved import persist_ranking_item
from app.services.ranking_snapshots import RankingRequest
from app.storage.database import build_session_factory
from app.storage.models import Content, Profile, RankingSnapshot, UserSavedContent


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["profile", "github", "hacker_news"])
async def test_legacy_profile_recovery_is_durable(action):
    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    user = AuthenticatedUser(id=uuid4(), email=None, claims={})
    url = f"https://example.com/legacy-ranking/{uuid4()}"
    request = None
    guarded = False
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        guarded = True
        await connection.execute(
            "INSERT INTO profiles (id) VALUES ($1)", user.id
        )
        await connection.execute("DELETE FROM profiles WHERE id = $1", user.id)
        if action != "profile":
            request = RankingRequest(kind=action)
            async with factory() as session:
                await session.execute(
                    delete(RankingSnapshot).where(RankingSnapshot.cache_key == request.cache_key)
                )
                now = datetime.now(UTC)
                session.add(
                    RankingSnapshot(
                        cache_key=request.cache_key,
                        kind=request.kind,
                        parameters=request.parameters,
                        payload={
                            "title": "Ranking",
                            "subtitle": "",
                            "items": [{"rank": 1, "title": "Legacy save", "url": url}],
                        },
                        fetched_at=now,
                        next_refresh_at=now,
                        is_ready=True,
                    )
                )
                await session.commit()
        async with factory() as session:
            if request is None:
                response = await get_profile(request=None, current_user=user, session=session)
                assert response.id == str(user.id)
            else:
                content_id = await persist_ranking_item(
                    session, user_id=user.id, request=request, url=url
                )
        async with factory() as session:
            assert await session.get(Profile, user.id) is not None
            if request is not None:
                assert (
                    await session.scalar(
                        select(UserSavedContent.id).where(
                            UserSavedContent.user_id == user.id,
                            UserSavedContent.content_id == content_id,
                        )
                    )
                    is not None
                )
    finally:
        if guarded:
            await connection.execute("DELETE FROM profiles WHERE id = $1", user.id)
            async with factory() as session:
                await session.execute(delete(Content).where(Content.canonical_url == url))
                if request is not None:
                    await session.execute(
                        delete(RankingSnapshot).where(
                            RankingSnapshot.cache_key == request.cache_key
                        )
                    )
                await session.commit()
        await connection.close()
        await engine.dispose()
