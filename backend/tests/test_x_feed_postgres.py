"""Self-reply grouping uses real PostgreSQL JSONB, recursive SQL and keyset pages."""

from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine
from test_postgres_migrations import _assert_disposable_database, _test_dsn, _test_guard_token

from app.api.feed import _decode_feed_cursor, _query_feed_page
from app.core.auth import AuthenticatedUser
from app.storage.database import build_session_factory
from app.storage.models import (
    Content,
    FeedSource,
    SourceEntry,
    SourceSubscription,
    UserSavedContent,
)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_x_grouping_pages_scope_late_parents_cycles_and_long_chain():
    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    user = AuthenticatedUser(id=uuid4(), email=None, claims={})
    sources = []
    now = datetime.now(UTC)
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        await connection.execute("INSERT INTO profiles (id) VALUES ($1)", user.id)
        await connection.execute(
            "INSERT INTO user_translation_preferences (user_id,is_enabled) VALUES ($1,false) "
            "ON CONFLICT (user_id) DO UPDATE SET is_enabled=false",
            user.id,
        )
        async with factory() as session:
            for index in range(3):
                source = FeedSource(
                    kind="x",
                    canonical_key=f"test:x:{uuid4()}",
                    canonical_url="https://x.com/writer",
                    display_name="Writer",
                )
                session.add(source)
                await session.flush()
                sources.append(source.id)
                if index < 2:
                    session.add(
                        SourceSubscription(
                            user_id=user.id,
                            source_id=source.id,
                            folder_name="AI" if index == 0 else "Other",
                        )
                    )
            await session.commit()

        async def add(
            native,
            *,
            source=0,
            parent=None,
            author="42",
            reply_author=None,
            repost=False,
            legacy=False,
            age=0,
        ):
            async with factory() as session:
                content = Content(
                    authority_source_id=sources[source],
                    kind="post",
                    title=native,
                    body_text="full collected body " * 50,
                )
                session.add(content)
                await session.flush()
                entry = SourceEntry(
                    source_id=sources[source],
                    content_id=content.id,
                    native_id=native,
                    title=native,
                    author_name="@writer",
                    external_url=f"https://x.com/i/status/{native}",
                    feed_sort_at=now - timedelta(seconds=age),
                    raw_metadata={}
                    if legacy
                    else {
                        "x": {
                            "version": 1,
                            "tweet_id": native,
                            "author": {"id": author},
                            "completeness": "parsed",
                            "conversation_id": "100",
                            "is_repost": repost,
                            "reply_to": {"tweet_id": parent, "author_id": reply_author}
                            if parent
                            else None,
                        }
                    },
                )
                session.add(entry)
                await session.commit()
                return content.id

        async def page(limit=2, cursor=None, source_id=None, folder_name=None):
            async with factory() as session:
                return await _query_feed_page(
                    session,
                    current_user=user,
                    folder_name=folder_name,
                    source_id=source_id,
                    limit=limit,
                    cursor=_decode_feed_cursor(cursor) if cursor else None,
                )

        root_id = await add("100", age=10)
        await add("101", parent="100", age=9)
        await add("102", parent="101", age=8)
        await add("103", parent="100", author="7", reply_author="42", age=7)
        await add("104", parent="100", reply_author="7", age=6)
        await add("105", parent="100", repost=True, age=5)
        await add("106", legacy=True, age=4)
        await add("100", source=1, age=3)  # Same native ID, separate source identity.
        await add("900", source=2, age=2)  # Never subscribed.
        async with factory() as session:
            session.add(UserSavedContent(user_id=user.id, content_id=root_id))
            await session.commit()
        seen = []
        cursor = None
        while True:
            items, cursor = await page(cursor=cursor)
            seen.extend(items)
            if cursor is None:
                break
        assert len(seen) == 6
        assert len({item.content_id for item in seen}) == 6
        root = next(item for item in seen if item.content_id == str(root_id))
        assert root.x_preview.thread.loaded_count == 3
        assert root.is_saved
        assert all(item.x_preview.tweet_id not in {"101", "102", "900"} for item in seen)
        other, _ = await page(source_id=sources[1])
        assert len(other) == 1 and other[0].x_preview.thread is None
        hidden, _ = await page(source_id=sources[2])
        assert hidden == []
        folder, _ = await page(limit=50, folder_name="AI")
        assert len(folder) == 5

        # Offline repair is source-scoped, dry by default, idempotent and text-preserving.
        from app.ingestion.x_relationship_backfill import backfill

        evidence = [
            {
                "tweet_id": "106",
                "raw": {
                    "rest_id": "106",
                    "legacy": {"user_id_str": "42", "quoted_status_id_str": "99"},
                },
            }
        ]
        async with factory() as session:
            dry = await backfill(session, source_id=sources[0], records=evidence, apply=False)
            assert dry["updated"] == 1
            entry = await session.scalar(
                select(SourceEntry).where(
                    SourceEntry.source_id == sources[0], SourceEntry.native_id == "106"
                )
            )
            assert "x" not in entry.raw_metadata
        async with factory() as session:
            assert (await backfill(session, source_id=sources[0], records=evidence, apply=True))[
                "updated"
            ] == 1
        async with factory() as session:
            assert (await backfill(session, source_id=sources[0], records=evidence, apply=True))[
                "updated"
            ] == 0
            entry = await session.scalar(
                select(SourceEntry).where(
                    SourceEntry.source_id == sources[0], SourceEntry.native_id == "106"
                )
            )
            assert entry.raw_metadata["x"]["quote"]["tweet_id"] == "99"
            content = await session.get(Content, entry.content_id)
            assert content.body_text == "full collected body " * 50

        # A child with a missing parent is a valid collected root until the parent arrives.
        await add("201", parent="200", age=20)
        await add("202", parent="201", age=19)
        before, _ = await page(limit=50, source_id=sources[0])
        assert (
            next(i for i in before if i.x_preview.tweet_id == "201").x_preview.thread.loaded_count
            == 2
        )
        await add("200", age=21)
        after, _ = await page(limit=50, source_id=sources[0])
        assert (
            next(i for i in after if i.x_preview.tweet_id == "200").x_preview.thread.loaded_count
            == 3
        )
        assert not any(i.x_preview.tweet_id == "201" for i in after)

        for native, parent in (("300", "301"), ("301", "300"), ("302", "302"), ("303", "301")):
            await add(native, parent=parent, age=30)
        cycled, _ = await page(limit=50, source_id=sources[0])
        assert sum(i.x_preview.tweet_id in {"300", "301", "302", "303"} for i in cycled) == 4

        # 300 collected posts must produce one card, without an O(n²) closure.
        async with factory() as session:
            for index in range(300):
                native = str(1000 + index)
                content = Content(authority_source_id=sources[1], kind="post", title=native)
                session.add(content)
                await session.flush()
                session.add(
                    SourceEntry(
                        source_id=sources[1],
                        content_id=content.id,
                        native_id=native,
                        title=native,
                        external_url=f"https://x.com/i/status/{native}",
                        feed_sort_at=now - timedelta(seconds=400 - index),
                        raw_metadata={
                            "x": {
                                "tweet_id": native,
                                "author": {"id": "42"},
                                "completeness": "parsed",
                                "reply_to": {"tweet_id": str(999 + index)} if index else None,
                            }
                        },
                    )
                )
            await session.commit()
        started = monotonic()
        long, _ = await page(limit=50, source_id=sources[1])
        elapsed = monotonic() - started
        assert len(long) == 2
        assert (
            next(i for i in long if i.x_preview.tweet_id == "1000").x_preview.thread.loaded_count
            == 300
        )
        print(f"300-post root propagation query: {elapsed:.3f}s")
        async with factory() as session:
            body = await session.scalar(select(Content.body_text).where(Content.id == root_id))
            assert body == "full collected body " * 50
    finally:
        async with factory() as session:
            await session.execute(delete(FeedSource).where(FeedSource.id.in_(sources)))
            await session.commit()
        await connection.execute("DELETE FROM profiles WHERE id=$1", user.id)
        await connection.close()
        await engine.dispose()
