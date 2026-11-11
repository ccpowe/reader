"""Ranking save integration on the guarded, migrated disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine
from test_postgres_migrations import _assert_disposable_database, _test_dsn, _test_guard_token

from app.api.feed import get_article
from app.api.saved import _query_saved_page, get_ranking_saved_state, save_content, unsave_content
from app.core.auth import AuthenticatedUser
from app.domain.enums import MediaType, SourceKind, SourceStatus, SourceVisibility
from app.services import ranking_snapshots
from app.services.ranking_saved import persist_ranking_item
from app.services.ranking_snapshots import (
    RankingData,
    RankingRequest,
    create_or_refresh_snapshot,
    get_snapshot,
    ranking_item_payload,
    sync_due_ranking_snapshots,
)
from app.services.rankings import RankingItem
from app.storage.database import build_session_factory
from app.storage.models import (
    Content,
    ContentMedia,
    FeedSource,
    RankingSnapshot,
    SourceEntry,
    SourceSubscription,
    UserSavedContent,
)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_ranking_archive_concurrency_durability_and_user_isolation():
    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    first, second = (AuthenticatedUser(id=uuid4(), email=None, claims={}) for _ in range(2))
    request = RankingRequest(kind="github")
    url = f"https://github.com/reader-test/{uuid4()}"
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        for user in (first, second):
            await connection.execute("INSERT INTO profiles (id) VALUES ($1)", user.id)
            await connection.execute(
                "INSERT INTO user_translation_preferences (user_id, is_enabled) "
                "VALUES ($1, false) ON CONFLICT (user_id) DO UPDATE SET is_enabled = false",
                user.id,
            )
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
                        "title": "GitHub",
                        "subtitle": "",
                        "items": [{"rank": 1, "title": "Durable ranking repository", "url": url}],
                    },
                    fetched_at=now,
                    next_refresh_at=now,
                    is_ready=True,
                )
            )
            await session.commit()

        async def save(user):
            async with factory() as session:
                return await persist_ranking_item(
                    session, user_id=user.id, request=request, url=url
                )

        ids = await asyncio.gather(save(first), save(first), save(second))
        assert len(set(ids)) == 1
        content_id = ids[0]
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Content).where(Content.canonical_url == url)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceEntry)
                    .where(SourceEntry.content_id == content_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(UserSavedContent)
                    .where(UserSavedContent.content_id == content_id)
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceSubscription)
                    .where(SourceSubscription.user_id.in_([first.id, second.id]))
                )
                == 0
            )
            await session.execute(
                delete(RankingSnapshot).where(RankingSnapshot.cache_key == request.cache_key)
            )
            await session.commit()
        # Fresh sessions simulate reopening after the shared ranking has expired.
        async with factory() as session:
            items, cursor = await _query_saved_page(
                session, current_user=first, limit=20, cursor=None
            )
            assert cursor is None and len(items) == 1
            assert items[0].ranking_kind == "github" and items[0].external_url == url
        async with factory() as session:
            assert (
                await get_article(content_id, current_user=first, session=session)
            ).external_url == url
        async with factory() as session:
            await unsave_content(content_id, current_user=first, session=session)
        async with factory() as session:
            state = await get_ranking_saved_state(url=url, current_user=first, session=session)
            assert state.is_saved is False and state.content_id is None
        async with factory() as session:
            assert (
                await get_ranking_saved_state(url=url, current_user=second, session=session)
            ).is_saved is True
        async with factory() as session:
            with pytest.raises(HTTPException) as error:
                await get_article(content_id, current_user=first, session=session)
            assert error.value.status_code == 404
        async with factory() as session:
            assert (
                await save_content(content_id, current_user=first, session=session)
            ).is_saved is True
    finally:
        await engine.dispose()
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_techtwitter_save_survives_snapshot_removal_with_original_body_and_media():
    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    first, second = (AuthenticatedUser(id=uuid4(), email=None, claims={}) for _ in range(2))
    request = RankingRequest(kind="techtwitter")
    url = f"https://x.com/reader_test/status/{uuid4().int}"
    body = "开头\n" + "😀" * 4_100 + "\n结尾"
    item = RankingItem(
        rank=3,
        title="A TechTwitter tweet",
        url=url,
        native_id=str(uuid4()),
        body_text=body,
        description="Short summary only",
        author="Alice",
        author_handle="alice_dev",
        published_at=datetime.now(UTC),
        score=0,
        comments=12,
        retweet_count=34,
        bookmark_count=56,
        image_urls=("https://media.example.com/photo.jpg",),
        video_url="https://media.example.com/movie.mp4",
        video_thumbnail_url="https://media.example.com/poster.jpg",
    )
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        for user in (first, second):
            await connection.execute("INSERT INTO profiles (id) VALUES ($1)", user.id)
            await connection.execute(
                "INSERT INTO user_translation_preferences (user_id, is_enabled) "
                "VALUES ($1, false) ON CONFLICT (user_id) DO UPDATE SET is_enabled = false",
                user.id,
            )
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
                        "title": "X 技术热门",
                        "subtitle": "",
                        "items": [ranking_item_payload(item)],
                    },
                    fetched_at=now,
                    next_refresh_at=now,
                    is_ready=True,
                )
            )
            await session.commit()
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Content).where(Content.canonical_url == url)
                )
                == 0
            )

        async def save(user):
            async with factory() as session:
                return await persist_ranking_item(
                    session, user_id=user.id, request=request, url=url
                )

        ids = await asyncio.gather(save(first), save(first), save(second))
        assert len(set(ids)) == 1
        content_id = ids[0]
        async with factory() as session:
            content = await session.get(Content, content_id)
            assert content.kind == "post"
            assert content.body_text == body
            assert content.excerpt == item.description
            source = await session.get(FeedSource, content.authority_source_id)
            assert source.kind == SourceKind.RANKING_ARCHIVE
            assert source.status == SourceStatus.PAUSED
            assert source.canonical_url == "https://www.techtwitter.com/"
            assert source.config == {"ranking_archive": True}
            media = list(
                await session.scalars(
                    select(ContentMedia)
                    .where(ContentMedia.content_id == content_id)
                    .order_by(ContentMedia.sort_order)
                )
            )
            assert [(m.media_type, m.original_url, m.poster_url) for m in media] == [
                (MediaType.IMAGE, item.image_urls[0], None),
                (MediaType.VIDEO, item.video_url, item.video_thumbnail_url),
            ]
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceSubscription)
                    .where(SourceSubscription.user_id.in_([first.id, second.id]))
                )
                == 0
            )
            await session.execute(
                delete(RankingSnapshot).where(RankingSnapshot.cache_key == request.cache_key)
            )
            await session.commit()
        async with factory() as session:
            items, cursor = await _query_saved_page(
                session, current_user=first, limit=20, cursor=None
            )
            assert cursor is None and len(items) == 1
            restored = items[0]
            assert restored.ranking_kind == "techtwitter"
            assert restored.ranking_item is not None
            assert restored.ranking_item.body_text == body
            assert "".join(s.text for s in restored.ranking_item.body_segments) == body
            assert restored.ranking_item.description == item.description
            assert restored.ranking_item.author_handle == "alice_dev"
            assert (restored.ranking_item.score, restored.ranking_item.comments) == (0, 12)
            assert (
                restored.ranking_item.retweet_count,
                restored.ranking_item.bookmark_count,
            ) == (34, 56)
            assert restored.ranking_item.image_urls == list(item.image_urls)
            assert restored.ranking_item.video_url == item.video_url
            assert restored.ranking_item.video_thumbnail_url == item.video_thumbnail_url
            assert restored.ranking_item.translation_locale is None
        async with factory() as session:
            await unsave_content(content_id, current_user=first, session=session)
        async with factory() as session:
            state = await get_ranking_saved_state(url=url, current_user=first, session=session)
            assert not state.is_saved and state.content_id is None
        async with factory() as session:
            assert (
                await get_ranking_saved_state(url=url, current_user=second, session=session)
            ).is_saved
            rows, _ = await _query_saved_page(session, current_user=second, limit=20, cursor=None)
            assert rows[0].ranking_item.body_text == body
    finally:
        await engine.dispose()
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_techtwitter_visible_x_content_reused_without_replacing_its_richer_data():
    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    subscribed, outsider = (AuthenticatedUser(id=uuid4(), email=None, claims={}) for _ in range(2))
    request = RankingRequest(kind="techtwitter")
    url = f"https://x.com/reader_test/status/{uuid4().int}"
    old_url = url.replace("/reader_test/", "/old_reader_handle/")
    item = RankingItem(
        rank=1,
        title="Tweet",
        url=url,
        native_id=str(uuid4()),
        body_text="Original public tweet",
        author="Alice",
        author_handle="alice",
        video_thumbnail_url="https://media.example.com/cover-only.jpg",
    )
    rich_text = "Already ingested richer X subscription body\nwith embedded context"
    rich_video = "https://media.example.com/ingested-video.mp4"
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        for user in (subscribed, outsider):
            await connection.execute("INSERT INTO profiles (id) VALUES ($1)", user.id)
            await connection.execute(
                "INSERT INTO user_translation_preferences (user_id, is_enabled) "
                "VALUES ($1, false) ON CONFLICT (user_id) DO UPDATE SET is_enabled = false",
                user.id,
            )
        async with factory() as session:
            await session.execute(
                delete(RankingSnapshot).where(RankingSnapshot.cache_key == request.cache_key)
            )
            source = FeedSource(
                kind=SourceKind.X,
                canonical_key=f"x:reader-test-{uuid4()}",
                canonical_url="https://x.com/reader_test",
                display_name="Subscribed X",
                visibility=SourceVisibility.PRIVATE,
                owner_id=subscribed.id,
                status=SourceStatus.ACTIVE,
                config={},
            )
            session.add(source)
            await session.flush()
            content = Content(
                authority_source_id=source.id,
                kind="post",
                canonical_url=url,
                title="Complete original article",
                body_text=rich_text,
                body_html="<p>Full X article</p>",
                extraction_status="not_needed",
            )
            session.add(content)
            await session.flush()
            session.add_all(
                [
                    SourceSubscription(
                        user_id=subscribed.id,
                        source_id=source.id,
                        is_enabled=True,
                    ),
                    SourceEntry(
                        source_id=source.id,
                        content_id=content.id,
                        native_id="subscribed-x-identity",
                        external_url=url,
                        title=content.title,
                        author_name="Alice",
                        feed_sort_at=datetime.now(UTC),
                        raw_metadata={"x_relations": {"quoted": True}},
                    ),
                    ContentMedia(
                        content_id=content.id,
                        media_type=MediaType.VIDEO,
                        original_url=rich_video,
                        poster_url="https://media.example.com/existing-poster.jpg",
                        sort_order=0,
                        is_active=True,
                    ),
                ]
            )
            await session.execute(
                pg_insert(FeedSource)
                .values(
                    canonical_key="ranking-archive:techtwitter",
                    canonical_url="https://www.techtwitter.com/",
                    kind=SourceKind.RANKING_ARCHIVE,
                    display_name="X 技术热门",
                    visibility=SourceVisibility.SHARED,
                    status=SourceStatus.PAUSED,
                    config={"ranking_archive": True},
                )
                .on_conflict_do_nothing(index_elements=[FeedSource.canonical_key])
            )
            archive_source_id = await session.scalar(
                select(FeedSource.id).where(
                    FeedSource.canonical_key == "ranking-archive:techtwitter"
                )
            )
            stale_archive = Content(
                authority_source_id=archive_source_id,
                kind="post",
                canonical_url=old_url,
                url_hash=hashlib.sha256(old_url.encode()).hexdigest(),
                title="Old archive title",
                body_text=None,
                extraction_status="not_needed",
            )
            session.add(stale_archive)
            await session.flush()
            archived_id = stale_archive.id
            session.add_all(
                [
                    SourceEntry(
                        source_id=archive_source_id,
                        content_id=archived_id,
                        native_id=item.native_id,
                        external_url=old_url,
                        title=stale_archive.title,
                        feed_sort_at=datetime.now(UTC),
                        raw_metadata={"ranking_kind": "techtwitter"},
                    ),
                    ContentMedia(
                        content_id=archived_id,
                        media_type=MediaType.IMAGE,
                        original_url="https://media.example.com/old-picture.jpg",
                        sort_order=0,
                        is_active=True,
                    ),
                ]
            )
            now = datetime.now(UTC)
            session.add(
                RankingSnapshot(
                    cache_key=request.cache_key,
                    kind=request.kind,
                    parameters=request.parameters,
                    payload={
                        "title": "X 技术热门",
                        "subtitle": "",
                        "items": [ranking_item_payload(item)],
                    },
                    fetched_at=now,
                    next_refresh_at=now,
                    is_ready=True,
                )
            )
            existing_id = content.id
            await session.commit()
        async with factory() as session:
            assert (
                await persist_ranking_item(session, user_id=subscribed.id, request=request, url=url)
                == existing_id
            )
        async with factory() as session:
            saved_id = await persist_ranking_item(
                session, user_id=outsider.id, request=request, url=url
            )
            assert saved_id == archived_id and saved_id != existing_id
            archived = await session.get(Content, archived_id)
            assert archived.body_text == item.body_text
            assert archived.canonical_url == url
        async with factory() as session:
            content = await session.get(Content, existing_id)
            assert content.body_text == rich_text
            assert content.body_html == "<p>Full X article</p>"
            media = list(
                await session.scalars(
                    select(ContentMedia).where(ContentMedia.content_id == existing_id)
                )
            )
            assert [(m.media_type, m.original_url) for m in media] == [
                (MediaType.VIDEO, rich_video)
            ]
            archive_media = list(
                await session.scalars(
                    select(ContentMedia).where(ContentMedia.content_id == archived_id)
                )
            )
            assert {(m.media_type, m.original_url) for m in archive_media} == {
                (MediaType.IMAGE, "https://media.example.com/old-picture.jpg"),
                (MediaType.IMAGE, item.video_thumbnail_url),
            }
            archive_entry = await session.scalar(
                select(SourceEntry).where(SourceEntry.content_id == archived_id)
            )
            assert archive_entry.raw_metadata["ranking_item"]["author_handle"] == "alice"
            assert archive_entry.external_url == url
            assert archive_entry.raw_metadata["ranking_item"]["url"] == url
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Content)
                    .where(
                        Content.authority_source_id == archive_source_id,
                        Content.canonical_url.in_((old_url, url)),
                    )
                )
                == 1
            )
            entry = await session.scalar(
                select(SourceEntry).where(SourceEntry.content_id == existing_id)
            )
            assert entry.raw_metadata == {"x_relations": {"quoted": True}}
            await session.execute(
                delete(RankingSnapshot).where(RankingSnapshot.cache_key == request.cache_key)
            )
            await session.commit()
        async with factory() as session:
            rows, _ = await _query_saved_page(
                session, current_user=subscribed, limit=20, cursor=None
            )
            assert rows[0].ranking_kind is None
            assert rows[0].ranking_item is None
            assert rows[0].external_url == url
        async with factory() as session:
            assert (
                await get_article(existing_id, current_user=subscribed, session=session)
            ).body_text == rich_text
        async with factory() as session:
            rows, _ = await _query_saved_page(session, current_user=outsider, limit=20, cursor=None)
            assert rows[0].ranking_item.body_text == item.body_text
            assert rows[0].ranking_item.url == url
            assert (
                await get_ranking_saved_state(url=url, current_user=outsider, session=session)
            ).is_saved
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceSubscription)
                    .where(SourceSubscription.user_id == outsider.id)
                )
                == 0
            )
    finally:
        await engine.dispose()
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_reddit_saved_identity_is_reused_by_normal_ingestion():
    from app.domain.enums import ContentKind
    from app.ingestion.models import DiscoveredContent
    from app.ingestion.source_identity import canonical_reddit_identity
    from app.services.subscriptions import subscribe_to_reddit_source
    from app.workers.candidates import _get_or_create_content

    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    user_id = uuid4()
    subreddit = f"test{uuid4().hex[:12]}"
    request = RankingRequest(kind="reddit", subreddit=subreddit)
    url = f"https://www.reddit.com/r/{subreddit}/comments/abc123/post/"
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        await connection.execute("INSERT INTO profiles (id) VALUES ($1)", user_id)
        async with factory() as session:
            source, *_ = await subscribe_to_reddit_source(
                session,
                user_id=user_id,
                identity=canonical_reddit_identity(subreddit),
                folder_name=None,
                limit=20,
            )
            source_id = source.id
            now = datetime.now(UTC)
            snapshot = await session.get(RankingSnapshot, request.cache_key)
            snapshot.payload = {
                "title": subreddit,
                "subtitle": "",
                "items": [
                    {
                        "rank": 1,
                        "title": "Reddit post",
                        "url": url,
                        "native_id": "abc123",
                    }
                ],
            }
            snapshot.fetched_at = now
            snapshot.next_refresh_at = now
            snapshot.is_ready = True
            await session.commit()
        async with factory() as session:
            archived_id = await persist_ranking_item(
                session, user_id=user_id, request=request, url=url
            )
        async with factory() as session:
            content = await _get_or_create_content(
                session,
                DiscoveredContent(
                    native_id="abc123",
                    kind=ContentKind.POST,
                    title="Reddit post",
                    external_url=url,
                    published_at=None,
                ),
                source_id=source_id,
            )
            assert content.id == archived_id
            assert content.authority_source_id == source_id
            await session.commit()
        # The same URL entering from the ranking again reuses its subscribed Content.
        async with factory() as session:
            assert (
                await persist_ranking_item(session, user_id=user_id, request=request, url=url)
                == archived_id
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(UserSavedContent)
                    .where(
                        UserSavedContent.user_id == user_id,
                        UserSavedContent.content_id == archived_id,
                    )
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceSubscription)
                    .where(
                        SourceSubscription.user_id == user_id,
                    )
                )
                == 1
            )
    finally:
        await engine.dispose()
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_reddit_subscription_rolls_back_snapshot_failure_and_reuses_quota(monkeypatch):
    from app.ingestion.source_identity import canonical_reddit_identity
    from app.services import subscriptions
    from app.services.ranking_snapshots import ensure_reddit_snapshots
    from app.services.subscriptions import (
        SourceSubscriptionLimitExceeded,
        subscribe_to_reddit_source,
    )

    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    user_id = uuid4()
    subreddit = f"test{uuid4().hex[:12]}"
    identity = canonical_reddit_identity(subreddit)
    board_keys = [
        RankingRequest(
            kind="reddit", subreddit=subreddit, sort=sort, time_filter=time_filter
        ).cache_key
        for sort, time_filter in (("hot", None), ("rising", None), ("top", "week"))
    ]

    async def fail_after_snapshots(session, name):
        await ensure_reddit_snapshots(session, name)
        raise RuntimeError("snapshot setup failed")

    try:
        await _assert_disposable_database(connection, _test_guard_token())
        with monkeypatch.context() as patch:
            patch.setattr(subscriptions, "ensure_reddit_snapshots", fail_after_snapshots)
            async with factory() as session:
                with pytest.raises(RuntimeError, match="snapshot setup failed"):
                    await subscribe_to_reddit_source(
                        session, user_id=user_id, identity=identity, folder_name=None, limit=1
                    )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM feed_sources WHERE canonical_key = $1", identity.canonical_key
            )
            == 0
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM source_subscriptions WHERE user_id = $1", user_id
            )
            == 0
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM ranking_snapshots WHERE cache_key = ANY($1::text[])",
                board_keys,
            )
            == 0
        )

        async with factory() as session:
            source, subscription, _, created = await subscribe_to_reddit_source(
                session, user_id=user_id, identity=identity, folder_name=None, limit=1
            )
            assert created
        async with factory() as session:
            again, same_subscription, _, created = await subscribe_to_reddit_source(
                session, user_id=user_id, identity=identity, folder_name=None, limit=1
            )
            assert not created
            assert again.id == source.id and same_subscription.id == subscription.id
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM ranking_snapshots WHERE cache_key = ANY($1::text[])",
                board_keys,
            )
            == 3
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM source_subscriptions WHERE user_id = $1", user_id
            )
            == 1
        )
        async with factory() as session:
            with pytest.raises(SourceSubscriptionLimitExceeded):
                await subscribe_to_reddit_source(
                    session,
                    user_id=user_id,
                    identity=canonical_reddit_identity(f"test{uuid4().hex[:12]}"),
                    folder_name=None,
                    limit=1,
                )
    finally:
        await engine.dispose()
        await connection.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_product_hunt_date_keyed_cache_publish_and_current_day_worker_seed(monkeypatch):
    """A date is replaced in place; other dates persist and only today is worker-due."""
    from app.core.settings import Settings
    from app.services.rankings import PRODUCT_HUNT_TIMEZONE, RankingItem

    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    factory = build_session_factory(engine)
    historical = RankingRequest(kind="product_hunt", day=date(2025, 1, 2))
    another_day = RankingRequest(kind="product_hunt", day=date(2025, 1, 3))
    today = RankingRequest(kind="product_hunt", day=datetime.now(PRODUCT_HUNT_TIMEZONE).date())
    live_calls = []
    worker_calls = []
    monkeypatch.setattr(
        ranking_snapshots,
        "get_settings",
        lambda: Settings(_env_file=None, product_hunt_token="test-only"),
    )

    async def live(request, *, limit):
        live_calls.append(request)
        revision = live_calls.count(request)
        return RankingData(
            kind="product_hunt",
            title="Product Hunt",
            subtitle=f"Featured launches · {request.product_hunt_day.isoformat()}",
            items=[
                RankingItem(
                    rank=1,
                    title=f"Launch revision {revision}",
                    url=f"https://www.producthunt.com/posts/{request.product_hunt_day.isoformat()}",
                    description="Tagline",
                    native_id=f"ph-{request.product_hunt_day.isoformat()}",
                    product_id=f"ph-{request.product_hunt_day.isoformat()}",
                    website_url="https://example.org/launch",
                    image_urls=("https://cdn.example.org/launch.png",),
                    score=revision,
                    comments=2,
                )
            ],
            fetched_at=datetime.now(UTC),
            truncated=revision == 1,
        )

    guarded = False
    try:
        await _assert_disposable_database(connection, _test_guard_token())
        guarded = True
        monkeypatch.setattr(ranking_snapshots, "fetch_live_ranking", live)
        async with factory() as session:
            await session.execute(
                delete(RankingSnapshot).where(
                    RankingSnapshot.cache_key.in_(
                        (historical.cache_key, another_day.cache_key, today.cache_key)
                    )
                )
            )
            await session.commit()
            first = await create_or_refresh_snapshot(session, historical)
            second = await get_snapshot(session, historical)
            assert first == second and first.truncated
            assert second.items[0].product_id == "ph-2025-01-02"
            assert second.items[0].website_url == "https://example.org/launch"
            assert second.items[0].image_urls == ("https://cdn.example.org/launch.png",)
            assert live_calls == [historical]
            other = await create_or_refresh_snapshot(session, another_day)
            assert other.items[0].title == "Launch revision 1"
            await session.execute(
                update(RankingSnapshot)
                .where(RankingSnapshot.cache_key == historical.cache_key)
                .values(next_refresh_at=datetime.now(UTC) - timedelta(days=1))
            )
            await session.commit()
            revised = await create_or_refresh_snapshot(session, historical)
            assert revised.items[0].title == "Launch revision 2"
            assert revised.items[0].score == 2
            assert revised.truncated is False
            assert live_calls == [historical, another_day, historical]
            assert await get_snapshot(session, another_day) == other
            await session.execute(
                update(RankingSnapshot)
                .where(RankingSnapshot.cache_key.in_((historical.cache_key, another_day.cache_key)))
                .values(next_refresh_at=datetime.now(UTC) - timedelta(days=1))
            )
            await session.commit()
        async with factory() as session:
            stored_historical = await get_snapshot(session, historical)
            stored_other = await get_snapshot(session, another_day)
            assert stored_historical == revised
            assert stored_other == other
            assert ranking_snapshots._request_from_snapshot(
                await session.get(RankingSnapshot, historical.cache_key)
            ) == historical
            assert ranking_snapshots._request_from_snapshot(
                await session.get(RankingSnapshot, another_day.cache_key)
            ) == another_day

        async def record_worker(_session, request, *, force=False):
            worker_calls.append(request)
            return None

        monkeypatch.setattr(ranking_snapshots, "create_or_refresh_snapshot", record_worker)
        async with factory() as session:
            await sync_due_ranking_snapshots(session, limit=30)
        assert today in worker_calls
        assert historical not in worker_calls
        assert another_day not in worker_calls
        assert {
            record["cache_key"]: record["total"]
            for record in await connection.fetch(
                """
                SELECT cache_key, count(*) AS total
                FROM ranking_snapshots
                WHERE cache_key = ANY($1::text[])
                GROUP BY cache_key
                """,
                [historical.cache_key, another_day.cache_key],
            )
        } == {historical.cache_key: 1, another_day.cache_key: 1}
    finally:
        if guarded:
            await connection.execute(
                "DELETE FROM ranking_snapshots WHERE cache_key = ANY($1::text[])",
                [historical.cache_key, another_day.cache_key, today.cache_key],
            )
        await engine.dispose()
        await connection.close()
