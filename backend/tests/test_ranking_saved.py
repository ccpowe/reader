from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.saved import (
    SaveRankingRequest,
    _saved_item_response,
    get_ranking_saved_state,
    save_ranking,
)
from app.core.auth import AuthenticatedUser
from app.domain.enums import TranslationStatus
from app.services import ranking_saved
from app.services.ranking_snapshots import RankingData, RankingRequest
from app.services.rankings import RankingItem
from app.translation.domain import TranslationProjection
from app.translation.projection import TranslationContext


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_existing_feed_identity_is_reused_without_source_or_subscription_writes(monkeypatch):
    content_id, user_id = uuid4(), uuid4()
    session = AsyncMock()
    session.scalar.return_value = content_id
    snapshot = RankingData(
        kind="github",
        title="GitHub",
        subtitle="",
        fetched_at=datetime.now(UTC),
        items=[RankingItem(rank=1, title="Repo", url="https://github.com/a/b")],
    )
    monkeypatch.setattr(ranking_saved, "get_snapshot", AsyncMock(return_value=snapshot))
    result = await ranking_saved.persist_ranking_item(
        session, user_id=user_id, request=RankingRequest(kind="github"), url=snapshot.items[0].url
    )
    assert result == content_id
    writes = {
        call.args[0].table.name: call.args[0]
        for call in session.execute.await_args_list
        if hasattr(call.args[0], "table")
    }
    assert set(writes) == {"profiles", "user_saved_contents"}
    assert writes["profiles"].compile().params["id"] == user_id
    assert "ON CONFLICT (id) DO NOTHING" in sql(writes["profiles"])
    saved = writes["user_saved_contents"]
    assert saved.compile().params["user_id"] == user_id
    assert saved.compile().params["content_id"] == content_id
    assert "ON CONFLICT (user_id, content_id) DO NOTHING" in sql(saved)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_snapshot_cannot_create_client_supplied_content(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(ranking_saved, "get_snapshot", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await ranking_saved.persist_ranking_item(
            session,
            user_id=uuid4(),
            request=RankingRequest(kind="github"),
            url="https://example.com",
        )
    assert error.value.status_code == 404
    assert all(
        "pg_advisory_xact_lock" in str(call.args[0]) for call in session.execute.await_args_list
    )


@pytest.mark.asyncio
async def test_reddit_requires_callers_subscription_before_reading_cache(monkeypatch):
    snapshot = AsyncMock()
    monkeypatch.setattr(ranking_saved, "get_snapshot", snapshot)
    monkeypatch.setattr(
        ranking_saved, "user_has_active_reddit_subscription", AsyncMock(return_value=False)
    )
    with pytest.raises(HTTPException) as error:
        await ranking_saved.persist_ranking_item(
            AsyncMock(),
            user_id=uuid4(),
            request=RankingRequest(kind="reddit"),
            url="https://reddit.com/r/MachineLearning/comments/a/",
        )
    assert error.value.status_code == 403
    snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_archive_has_durable_rows_and_no_subscription_write(monkeypatch):
    session = AsyncMock()
    source_id, content_id, user_id = uuid4(), uuid4(), uuid4()
    session.scalar.side_effect = [None, source_id, content_id, content_id]
    snapshot = RankingData(
        kind="github",
        title="GitHub",
        subtitle="",
        fetched_at=datetime.now(UTC),
        items=[RankingItem(rank=1, title="Repo", url="https://github.com/a/b")],
    )
    monkeypatch.setattr(ranking_saved, "get_snapshot", AsyncMock(return_value=snapshot))
    assert (
        await ranking_saved.persist_ranking_item(
            session,
            user_id=user_id,
            request=RankingRequest(kind="github"),
            url=snapshot.items[0].url,
        )
        == content_id
    )
    writes = {
        call.args[0].table.name: call.args[0]
        for call in session.execute.await_args_list
        if hasattr(call.args[0], "table")
    }
    assert set(writes) == {
        "feed_sources",
        "contents",
        "source_entries",
        "profiles",
        "user_saved_contents",
    }
    assert writes["profiles"].compile().params["id"] == user_id
    assert "ON CONFLICT (id) DO NOTHING" in sql(writes["profiles"])
    source = writes["feed_sources"].compile().params
    assert source["canonical_key"] == "ranking-archive:github"
    assert source["status"] == "paused"
    assert source["config"] == {"ranking_archive": True}
    content = writes["contents"].compile().params
    assert content["authority_source_id"] == source_id
    assert content["canonical_url"] == snapshot.items[0].url
    assert content["title"] == snapshot.items[0].title
    assert "ON CONFLICT (authority_source_id, url_hash)" in sql(writes["contents"])
    entry = writes["source_entries"].compile().params
    assert entry["source_id"] == source_id
    assert entry["content_id"] == content_id
    assert entry["external_url"] == snapshot.items[0].url
    assert entry["raw_metadata"] == {"ranking_kind": "github"}
    assert "ON CONFLICT (source_id, native_id)" in sql(writes["source_entries"])
    saved = writes["user_saved_contents"].compile().params
    assert saved["user_id"] == user_id
    assert saved["content_id"] == content_id
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_url_status_does_not_expose_content_id():
    session = AsyncMock()
    session.scalar.return_value = None
    user = AuthenticatedUser(id=uuid4(), email=None, claims={})
    state = await get_ranking_saved_state(
        url="https://example.com", current_user=user, session=session
    )
    assert state.content_id is None and state.is_saved is False
    statement = sql(session.scalar.await_args.args[0])
    assert "source_subscriptions.user_id" in statement
    assert "user_saved_contents.user_id" in statement
    assert "is_enabled IS true" in statement


def test_archived_saved_response_reopens_web_and_upgraded_content_reopens_reader():
    content = SimpleNamespace(
        id=uuid4(), title="Repo", excerpt="description", media=[], body_html=None
    )
    entry = SimpleNamespace(
        title="Repo",
        external_url="https://github.com/a/b",
        author_name=None,
        published_at=None,
        fetched_at=datetime.now(UTC),
        raw_metadata={"ranking_kind": "github"},
    )
    source = SimpleNamespace(id=uuid4(), display_name="GitHub", avatar_url=None, kind="web")
    assert _saved_item_response(None, entry, content, source, None).ranking_kind == "github"
    content.body_html = "<p>Extracted article</p>"
    assert _saved_item_response(None, entry, content, source, None).ranking_kind is None


def test_techtwitter_saved_response_restores_original_body_and_media_despite_html():
    from app.services.ranking_snapshots import ranking_item_payload

    body = "第一段\n" + "😀" * 4_100 + "\n最后一段"
    item = RankingItem(
        rank=7,
        title="A tweet",
        url="https://x.com/tech/status/123",
        author="Tech Person",
        author_handle="tech",
        body_text=body,
        description="An abstract, not the tweet",
        score=0,
        comments=3,
        retweet_count=4,
        bookmark_count=5,
        image_urls=("https://x.com/photo.jpg",),
        video_url="https://x.com/movie.mp4",
        video_thumbnail_url="https://x.com/cover.jpg",
        native_id="tweet-uuid",
    )
    content = SimpleNamespace(
        id=uuid4(),
        title="A tweet",
        excerpt=item.description,
        media=[],
        body_html="<p>Archive article HTML must not hide saved tweet</p>",
    )
    entry = SimpleNamespace(
        title=item.title,
        external_url=item.url,
        author_name=item.author,
        published_at=None,
        fetched_at=datetime.now(UTC),
        raw_metadata={"ranking_kind": "techtwitter", "ranking_item": ranking_item_payload(item)},
    )
    source = SimpleNamespace(
        id=uuid4(),
        canonical_key="ranking-archive:techtwitter",
        display_name="X 技术热门",
        avatar_url=None,
        kind="web",
    )

    response = _saved_item_response(None, entry, content, source, None)

    assert response.ranking_kind == "techtwitter"
    assert response.ranking_item is not None
    assert response.ranking_item.body_text == body
    assert "".join(segment.text for segment in response.ranking_item.body_segments) == body
    assert all(
        len(segment.text.encode("utf-16-le")) // 2 <= 8000
        for segment in response.ranking_item.body_segments
    )
    assert response.ranking_item.translation_locale is None
    assert response.ranking_item.author_handle == "tech"
    assert response.ranking_item.score == 0
    assert response.ranking_item.retweet_count == 4
    assert response.ranking_item.video_url == item.video_url
    assert response.ranking_item.video_thumbnail_url == item.video_thumbnail_url

    titled = _saved_item_response(
        None,
        entry,
        content,
        source,
        None,
        TranslationContext(target_locale="fr", enabled=True, engine=None),
        TranslationProjection(
            item_id=str(content.id),
            source_hash="title-hash",
            target_locale="fr",
            status=TranslationStatus.SUCCEEDED,
            translated_text="Titre traduit",
        ),
    )
    assert titled.translation_locale == "fr"
    assert titled.ranking_item.translation_locale is None


def test_existing_x_subscription_keeps_its_regular_saved_reading_path():
    content = SimpleNamespace(
        id=uuid4(),
        title="Full subscribed X post",
        excerpt=None,
        media=[],
        body_html="<p>Full native X body</p>",
    )
    entry = SimpleNamespace(
        title=content.title,
        external_url="https://x.com/tech/status/999",
        author_name="Tech author",
        published_at=None,
        fetched_at=datetime.now(UTC),
        raw_metadata={"x_relations": {"quoted": True}},
    )
    source = SimpleNamespace(id=uuid4(), display_name="Followed X", avatar_url=None, kind="x")

    response = _saved_item_response(None, entry, content, source, None)

    assert response.ranking_kind is None
    assert response.ranking_item is None
    assert response.source_kind == "x"


@pytest.mark.asyncio
async def test_github_daily_save_uses_daily_snapshot_and_product_hunt_is_unsupported(monkeypatch):
    saved_id = uuid4()
    save = AsyncMock(return_value=saved_id)
    monkeypatch.setattr("app.api.saved.persist_ranking_item", save)
    user = AuthenticatedUser(id=uuid4(), email=None, claims={})
    result = await save_ranking(
        SaveRankingRequest(kind="github", period="daily", url="https://github.com/a/b"),
        current_user=user,
        session=AsyncMock(),
    )
    assert result.content_id == str(saved_id)
    assert save.await_args.kwargs["request"] == RankingRequest(kind="github", period="daily")
    with pytest.raises(HTTPException) as error:
        await save_ranking(
            SaveRankingRequest(kind="hacker_news", period="daily", url="https://example.com/"),
            current_user=user,
            session=AsyncMock(),
        )
    assert error.value.status_code == 422
    with pytest.raises(ValueError, match="Product Hunt"):
        await ranking_saved.persist_ranking_item(
            AsyncMock(),
            user_id=user.id,
            request=RankingRequest(kind="product_hunt"),
            url="https://www.producthunt.com/posts/example",
        )
