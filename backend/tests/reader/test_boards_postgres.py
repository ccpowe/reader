"""Board snapshots, refresh rules and saving entries, against a real database."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from reader.boards import fetchers
from reader.boards.fetchers import BoardItem
from reader.boards.service import Boards, prune_snapshots, resolve
from reader.core.db import build_engine, build_session_factory
from reader.core.errors import AppError
from reader.core.settings import Settings
from reader.ingestion.errors import ScanError
from reader.models import Article, BoardSnapshot, SavedArticle, Translation
from reader.translation.engines import find_engine
from reader.translation.service import text_hash
from tests.reader.pg import api_client, postgres_dsn, reset_schema

pytestmark = pytest.mark.postgres


class Upstream:
    """A scripted board upstream: returns `items`, raises `error`, or waits."""

    def __init__(self, items=None):
        self.items = items or [BoardItem(rank=1, title="One", url="https://a/1")]
        self.error: ScanError | None = None
        self.delay = 0.0
        self.calls = 0

    async def __call__(self) -> list[BoardItem]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.items


@asynccontextmanager
async def _boards(**values):
    dsn = postgres_dsn()
    await reset_schema(dsn)
    settings = Settings(_env_file=None, database_url=dsn, database_ssl=False, **values)
    engine = build_engine(settings)
    boards = Boards(build_session_factory(engine), settings)
    try:
        yield boards, settings
    finally:
        await boards.aclose()
        await engine.dispose()


def _target(settings, upstream, board="hn", variant=None):
    return replace(resolve(settings, board, variant, None), fetch=upstream)


async def _age(boards, key, **delta):
    """Pretend the stored snapshot was fetched `delta` ago."""
    async with boards.factory() as db:
        await db.execute(
            update(BoardSnapshot)
            .where(BoardSnapshot.key == key)
            .values(fetched_at=datetime.now(UTC) - timedelta(**delta))
        )
        await db.commit()


@pytest.mark.asyncio
async def test_a_board_is_fetched_only_when_expired() -> None:
    upstream = Upstream()
    async with _boards() as (boards, settings):
        target = _target(settings, upstream)
        first = await boards.get(target)
        cached = await boards.get(target)
        await _age(boards, "hn", minutes=6)
        expired = await boards.get(target)

    assert [item.title for item in first.items] == ["One"] and not first.stale
    assert not cached.stale and upstream.calls == 2
    assert not expired.stale


@pytest.mark.asyncio
async def test_a_failed_fetch_serves_the_old_copy_or_an_error() -> None:
    upstream = Upstream()
    async with _boards() as (boards, settings):
        target = _target(settings, upstream)
        upstream.error = ScanError("hn_http_503", "down")
        with pytest.raises(AppError) as nothing_yet:
            await boards.get(target)
        upstream.error = None
        await boards.get(target)
        await _age(boards, "hn", minutes=6)
        upstream.error = ScanError("hn_http_503", "down")
        old = await boards.get(target)

    assert (nothing_yet.value.status, nothing_yet.value.code) == (502, "board_unavailable")
    assert old.stale and old.error_code == "hn_http_503"
    assert [item.title for item in old.items] == ["One"]


@pytest.mark.asyncio
async def test_a_slow_fetch_shows_the_old_copy_and_finishes_later() -> None:
    upstream = Upstream()
    async with _boards(board_fetch_wait_seconds=1.0) as (boards, settings):
        target = _target(settings, upstream)
        upstream.delay = 1.5
        with pytest.raises(AppError) as loading:
            await boards.get(target)
        await asyncio.sleep(0.7)  # the first fetch completes in the background
        loaded = await boards.get(target)
        await _age(boards, "hn", minutes=6)
        upstream.items = [BoardItem(rank=1, title="Two", url="https://a/2")]
        stale = await boards.get(target)
        await asyncio.sleep(0.7)
        fresh = await boards.get(target)

    assert (loading.value.status, loading.value.code) == (503, "board_loading")
    assert [item.title for item in loaded.items] == ["One"]
    assert stale.stale and [item.title for item in stale.items] == ["One"]
    assert [item.title for item in fresh.items] == ["Two"] and not fresh.stale


@pytest.mark.asyncio
async def test_simultaneous_opens_share_one_fetch() -> None:
    upstream = Upstream()
    upstream.delay = 0.2
    async with _boards() as (boards, settings):
        target = _target(settings, upstream)
        views = await asyncio.gather(*(boards.get(target) for _ in range(5)))

    assert upstream.calls == 1 and all(view.items for view in views)


@pytest.mark.asyncio
async def test_manual_refresh_at_most_once_a_minute() -> None:
    upstream = Upstream()
    async with _boards() as (boards, settings):
        target = _target(settings, upstream)
        await boards.get(target)
        await boards.get(target, refresh=True)
        await boards.get(target, refresh=True)
        async with boards.factory() as db:
            await db.execute(
                update(BoardSnapshot).values(
                    manual_refresh_at=datetime.now(UTC) - timedelta(minutes=2)
                )
            )
            await db.commit()
        await boards.get(target, refresh=True)

    assert upstream.calls == 3


@pytest.mark.asyncio
async def test_a_failed_manual_refresh_is_reported_stale() -> None:
    upstream = Upstream()
    async with _boards() as (boards, settings):
        target = _target(settings, upstream)
        await boards.get(target)  # fresh copy
        upstream.error = ScanError("hn_http_503", "down")
        failed = await boards.get(target, refresh=True)

    assert failed.stale and failed.error_code == "hn_http_503" and failed.items


@pytest.mark.asyncio
async def test_a_fetch_that_began_before_a_day_ended_is_not_final() -> None:
    upstream = Upstream()
    upstream.delay = 0.3
    async with _boards() as (boards, settings):
        target = replace(
            _target(settings, upstream),
            ttl=timedelta(0),
            final_after=datetime.now(UTC) + timedelta(seconds=0.1),
        )
        await boards.get(target)
        async with boards.factory() as db:
            snapshot = await db.get(BoardSnapshot, target.key)

    assert snapshot.fetched_at < target.final_after
    assert not target.is_fresh(snapshot.fetched_at, datetime.now(UTC))


@pytest.mark.asyncio
async def test_a_rate_limit_pauses_the_board() -> None:
    upstream = Upstream()
    async with _boards() as (boards, settings):
        target = _target(settings, upstream)
        await boards.get(target)
        await _age(boards, "hn", minutes=6)
        upstream.error = ScanError("hn_http_429", "slow down", retry_after_seconds=120)
        limited = await boards.get(target)
        upstream.error = None
        paused = await boards.get(target, refresh=True)

    assert limited.stale and limited.paused_until is not None
    assert (
        timedelta(seconds=100) < limited.paused_until - datetime.now(UTC) <= timedelta(seconds=120)
    )
    assert paused.stale and upstream.calls == 2  # nothing is fetched while paused


@pytest.mark.asyncio
async def test_a_bot_check_pauses_the_board_for_an_hour() -> None:
    upstream = Upstream()
    upstream.error = ScanError("techtwitter_challenge_required", "checkpoint")
    async with _boards() as (boards, settings):
        target = _target(settings, upstream, "techtwitter")
        with pytest.raises(AppError) as error:
            await boards.get(target)
        upstream.error = None
        with pytest.raises(AppError):
            await boards.get(target)
        async with boards.factory() as db:
            snapshot = await db.get(BoardSnapshot, "techtwitter")

    assert error.value.code == "board_unavailable" and error.value.retry_after > 3500
    assert upstream.calls == 1
    assert snapshot.paused_until - datetime.now(UTC) > timedelta(minutes=59)


@pytest.mark.asyncio
async def test_unopened_snapshots_are_pruned() -> None:
    async with _boards() as (boards, settings):
        await boards.get(_target(settings, Upstream()))
        await boards.get(_target(settings, Upstream(), "github", "daily"))
        await _age(boards, "hn", days=8)
        async with boards.factory() as db:
            removed = await prune_snapshots(db, 7)
        async with boards.factory() as db:
            kept = list(await db.scalars(select(BoardSnapshot.key)))

    assert removed == 1 and kept == ["github:daily"]


# --- Routes -----------------------------------------------------------------------


async def _signed_in(client) -> dict:
    tokens = (
        await client.post(
            "/auth/register", json={"email": "me@example.com", "password": "long password 1"}
        )
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_board_route_saving_and_translations(monkeypatch) -> None:
    monkeypatch.setenv("APP_DEEPSEEK_API_KEY", "k")
    items = [
        BoardItem(rank=1, title="Tool", url="https://gh/a", description="Does things"),
        BoardItem(rank=2, title="Other", url="https://gh/b"),
    ]

    async def fetch_github(period, limit):
        assert (period, limit) == ("weekly", 50)
        return items

    monkeypatch.setattr(fetchers, "fetch_github_trending", fetch_github)
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        fingerprint = find_engine(Settings(_env_file=None, deepseek_api_key="k"), None).fingerprint
        async with client.app.state.session_factory() as db:
            for purpose, text, translated in [
                ("board_title", "Tool", "工具"),
                ("board_description", "Does things", "做事"),
            ]:
                db.add(
                    Translation(
                        purpose=purpose,
                        source_hash=text_hash(text),
                        target_locale="zh-CN",
                        engine_fingerprint=fingerprint,
                        source_text=text,
                        translated_text=translated,
                        status="succeeded",
                    )
                )
            await db.commit()
        board = await client.get("/boards/github", params={"variant": "weekly"}, headers=auth)
        body = {"board": "github", "variant": "weekly", "url": "https://gh/a"}
        saved = await client.post("/saved/from-board", json=body, headers=auth)
        again = await client.post("/saved/from-board", json=body, headers=auth)
        missing = await client.post(
            "/saved/from-board", json={**body, "url": "https://gh/zzz"}, headers=auth
        )
        after = await client.get("/boards/github", params={"variant": "weekly"}, headers=auth)
        opened = await client.get(f"/articles/{saved.json()['article_id']}", headers=auth)
        listed = (await client.get("/saved", headers=auth)).json()["items"]
        in_folder = (await client.get("/saved", params={"group": ""}, headers=auth)).json()["items"]
        bad_variant = await client.get("/boards/github", params={"variant": "x"}, headers=auth)
        no_subreddit = await client.get("/boards/reddit", headers=auth)
        unknown = await client.get("/boards/myspace", headers=auth)
        async with client.app.state.session_factory() as db:
            articles = await db.scalar(select(func.count()).select_from(Article))
            saves = await db.scalar(select(func.count()).select_from(SavedArticle))

    entries = board.json()["items"]
    assert [entry["title"] for entry in entries] == ["Tool", "Other"]
    assert entries[0]["title_translation"] == {"text": "工具", "engine_fingerprint": fingerprint}
    assert entries[0]["description_translation"]["text"] == "做事"
    assert entries[1]["title_translation"] is None and not entries[0]["is_saved"]
    assert board.json()["stale"] is False and board.json()["age_seconds"] is not None
    assert saved.json() == again.json() and articles == 1 and saves == 1
    assert missing.json()["error"]["code"] == "board_item_not_found"
    assert [entry["is_saved"] for entry in after.json()["items"]] == [True, False]
    assert opened.json()["title"] == "Tool" and opened.json()["source"] is None
    assert listed[0]["title"] == "Tool" and listed[0]["summary"] == "Does things"
    assert listed[0]["source"] is None and listed[0]["board"] == "github"
    assert in_folder == []  # board items have no folder
    assert bad_variant.json()["error"]["code"] == "invalid_variant"
    assert no_subreddit.json()["error"]["code"] == "invalid_subreddit"
    assert unknown.status_code == 422  # not one of the boards
