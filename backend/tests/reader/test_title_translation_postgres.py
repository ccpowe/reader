"""Background title translation against a real database, with a scripted model."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from reader.core.db import build_engine, build_session_factory
from reader.core.settings import Settings
from reader.ingestion import scanner
from reader.ingestion.items import Item, Page
from reader.models import Source, Subscription, Translation, TranslationPreference, User
from reader.translation.engines import EngineError, find_engine
from reader.translation.service import Translator, text_hash
from reader.translation.titles import (
    pending_rows,
    prune_translations,
    request_titles,
    translate_pending_titles,
)
from tests.reader.pg import api_client, postgres_dsn, reset_schema
from tests.reader.test_translation_postgres import ScriptedModel

pytestmark = pytest.mark.postgres


@asynccontextmanager
async def _database(**values):
    dsn = postgres_dsn()
    await reset_schema(dsn)
    settings = Settings(
        _env_file=None,
        database_url=dsn,
        database_ssl=False,
        deepseek_api_key="k",
        scan_initial_items=5,
        **values,
    )
    engine = build_engine(settings)
    try:
        yield build_session_factory(engine), settings
    finally:
        await engine.dispose()


async def _source_with_followers(factory, *preferences: dict | None) -> Source:
    """One source; one follower per entry (None = no preference row)."""
    async with factory() as db:
        source = Source(
            kind="rss",
            canonical_key=f"rss:{uuid4()}",
            url="https://e.com/feed",
            next_scan_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        db.add(source)
        await db.flush()
        for preference in preferences:
            user = User(email=f"{uuid4()}@e.com", password_hash="x")
            db.add(user)
            await db.flush()
            db.add(Subscription(user_id=user.id, source_id=source.id))
            if preference is not None:
                db.add(TranslationPreference(user_id=user.id, **preference))
        await db.commit()
        return source


async def _rows(factory) -> list[Translation]:
    async with factory() as db:
        return list(
            await db.scalars(
                select(Translation).order_by(Translation.target_locale, Translation.source_text)
            )
        )


@pytest.mark.asyncio
async def test_titles_are_queued_for_what_followers_read() -> None:
    async with _database() as (factory, settings):
        source = await _source_with_followers(
            factory,
            None,  # defaults: zh-CN, the default engine
            {"target_locale": "ja", "enabled": True},
            {"target_locale": "fr", "enabled": False},
            {"target_locale": "de", "engine_id": "codex-subscription"},  # not configured
        )
        async with factory() as db:
            queued = await request_titles(db, settings, source.id, ["Hello  world", " ", "Bye"])
            again = await request_titles(db, settings, source.id, ["Hello world"])
            await db.commit()
        rows = await _rows(factory)

    assert queued == 4 and again == 2
    fingerprint = find_engine(settings, None).fingerprint
    assert [(row.target_locale, row.source_text, row.status) for row in rows] == [
        ("ja", "Bye", "pending"),
        ("ja", "Hello world", "pending"),
        ("zh-CN", "Bye", "pending"),
        ("zh-CN", "Hello world", "pending"),
    ]
    assert {row.engine_fingerprint for row in rows} == {fingerprint}


@pytest.mark.asyncio
async def test_a_scan_queues_new_and_changed_titles(monkeypatch) -> None:
    async with _database() as (factory, settings):
        await _source_with_followers(factory, None)

        async def scan(*items):
            page = Page(list(items))

            class Adapter:
                async def fetch_page(self, cursor, limit):
                    return page

            monkeypatch.setattr(scanner, "_build_adapter", lambda *args: Adapter())
            async with factory() as db:
                await db.execute(
                    update(Source).values(next_scan_at=datetime.now(UTC) - timedelta(seconds=1))
                )
                await db.commit()
            (claim,) = await scanner.claim_due_sources(factory, settings, 10)
            await scanner.scan_source(factory, settings, claim)

        await scan(Item(native_id="1", kind="article", title="First", url="https://e.com/1"))
        await scan(
            Item(native_id="2", kind="article", title="Second", url="https://e.com/2"),
            Item(native_id="1", kind="article", title="First, edited", url="https://e.com/1"),
        )
        texts = [row.source_text for row in await _rows(factory)]

    assert texts == ["First", "First, edited", "Second"]


@pytest.mark.asyncio
async def test_the_worker_translates_queued_titles() -> None:
    model = ScriptedModel()
    async with _database() as (factory, settings):
        source = await _source_with_followers(factory, None, {"target_locale": "ja"})
        async with factory() as db:
            await request_titles(db, settings, source.id, ["One", "Two"])
            await db.commit()
        translator = Translator(factory, settings, model)
        taken = await translate_pending_titles(translator)
        nothing_left = await translate_pending_titles(translator)
        rows = await _rows(factory)

    assert taken == 4 and nothing_left == 0
    assert [(row.target_locale, row.status, row.translated_text) for row in rows] == [
        ("ja", "succeeded", "译:One"),
        ("ja", "succeeded", "译:Two"),
        ("zh-CN", "succeeded", "译:One"),
        ("zh-CN", "succeeded", "译:Two"),
    ]
    assert len(model.calls) == 2  # one batch per locale


@pytest.mark.asyncio
async def test_the_worker_gives_up_after_three_attempts() -> None:
    model = ScriptedModel(lambda items: EngineError("timeout", "slow"))
    async with _database() as (factory, settings):
        source = await _source_with_followers(factory, None)
        async with factory() as db:
            await request_titles(db, settings, source.id, ["One"])
            await db.commit()
        translator = Translator(factory, settings, model)
        for _ in range(5):
            await translate_pending_titles(translator)
            async with factory() as db:  # skip the pause between attempts
                await db.execute(
                    update(Translation).values(finished_at=datetime.now(UTC) - timedelta(minutes=5))
                )
                await db.commit()
        (row,) = await _rows(factory)

    assert (row.status, row.error_code, row.attempts) == ("failed", "timeout", 3)
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_a_last_attempt_that_was_abandoned_is_ended() -> None:
    model = ScriptedModel()
    async with _database() as (factory, settings):
        async with factory() as db:
            db.add(
                Translation(
                    purpose="title",
                    source_hash=text_hash("One"),
                    target_locale="zh-CN",
                    engine_fingerprint=find_engine(settings, None).fingerprint,
                    source_text="One",
                    status="running",
                    attempts=3,
                    started_at=datetime.now(UTC) - timedelta(minutes=10),
                )
            )
            await db.commit()
        await translate_pending_titles(Translator(factory, settings, model))
        (row,) = await _rows(factory)

    assert (row.status, row.error_code) == ("failed", "abandoned")
    assert model.calls == []


def test_rows_are_inserted_in_the_claim_lock_order() -> None:
    rows = pending_rows(["Alpha", "Beta", "Gamma"], [("zh-CN", "f"), ("ja", "f")])
    keys = [(row["source_hash"], row["target_locale"]) for row in rows]
    assert keys == sorted(keys) and len(keys) == 6


@pytest.mark.asyncio
async def test_titles_for_a_removed_engine_fail_without_a_model_call() -> None:
    model = ScriptedModel()
    async with _database() as (factory, settings):
        async with factory() as db:
            db.add(
                Translation(
                    purpose="title",
                    source_hash=text_hash("One"),
                    target_locale="zh-CN",
                    engine_fingerprint="0" * 64,
                    source_text="One",
                    status="pending",
                )
            )
            await db.commit()
        await translate_pending_titles(Translator(factory, settings, model))
        (row,) = await _rows(factory)

    assert (row.status, row.error_code) == ("failed", "engine_unavailable")
    assert model.calls == []


@pytest.mark.asyncio
async def test_unused_translations_are_pruned() -> None:
    async with _database() as (factory, settings):
        old = datetime.now(UTC) - timedelta(days=40)
        async with factory() as db:
            for text, status, used in [
                ("old", "succeeded", old),
                ("busy", "running", old),
                ("recent", "succeeded", datetime.now(UTC)),
            ]:
                db.add(
                    Translation(
                        purpose="title",
                        source_hash=text_hash(text),
                        target_locale="zh-CN",
                        engine_fingerprint="f",
                        source_text=text,
                        status=status,
                        last_used_at=used,
                    )
                )
            await db.commit()
        async with factory() as db:
            removed = await prune_translations(db, 30)
        kept = sorted(row.source_text for row in await _rows(factory))

    assert removed == 1 and kept == ["busy", "recent"]


@pytest.mark.asyncio
async def test_the_feed_shows_titles_translated_for_my_preference(monkeypatch) -> None:
    monkeypatch.setenv("APP_DEEPSEEK_API_KEY", "k")
    async with api_client(postgres_dsn(), monkeypatch) as client:
        tokens = (
            await client.post(
                "/auth/register", json={"email": "me@example.com", "password": "long password 1"}
            )
        ).json()
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}
        me = (await client.get("/me", headers=auth)).json()["id"]
        settings = Settings(_env_file=None, deepseek_api_key="k")
        fingerprint = find_engine(settings, None).fingerprint
        async with client.app.state.session_factory() as db:
            from reader.models import Article

            source = Source(kind="rss", canonical_key="rss:x", url="https://e.com/f")
            db.add(source)
            await db.flush()
            db.add(Subscription(user_id=me, source_id=source.id))
            for n, title in enumerate(["Hello world", "Untranslated"]):
                db.add(
                    Article(
                        source_id=source.id,
                        native_id=str(n),
                        url=f"https://e.com/{n}",
                        url_hash=str(n),
                        kind="article",
                        title=title,
                        content_hash="h",
                    )
                )
            db.add(
                Translation(
                    purpose="title",
                    source_hash=text_hash("Hello world"),
                    target_locale="zh-CN",
                    engine_fingerprint=fingerprint,
                    source_text="Hello world",
                    translated_text="你好世界",
                    status="succeeded",
                )
            )
            await db.commit()
        before = (await client.get("/feed", headers=auth)).json()["items"]
        await client.patch("/me/translation-preference", json={"target_locale": "ja"}, headers=auth)
        other_locale = (await client.get("/feed", headers=auth)).json()["items"]
        await client.patch(
            "/me/translation-preference",
            json={"target_locale": "zh-CN", "enabled": False},
            headers=auth,
        )
        disabled = (await client.get("/feed", headers=auth)).json()["items"]

    translated = {item["title"]: item["title_translation"] for item in before}
    assert translated == {
        "Hello world": {"text": "你好世界", "engine_fingerprint": fingerprint},
        "Untranslated": None,
    }
    assert all(item["title_translation"] is None for item in other_locale + disabled)
