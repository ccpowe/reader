"""The translation service and routes against a real database, with a scripted model."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from reader.core.db import build_engine, build_session_factory
from reader.core.settings import Settings
from reader.models import Translation, TranslationBudget
from reader.translation.engines import EngineError, find_engine
from reader.translation.service import Translator, text_hash
from tests.reader.pg import api_client, postgres_dsn, reset_schema

pytestmark = pytest.mark.postgres


class ScriptedModel:
    """Stands in for EnginePool: translates to "译:<text>" unless told otherwise."""

    def __init__(self, reply=None, delay: float = 0.0):
        self.reply = reply  # (items) -> dict | str | Exception
        self.delay = delay
        self.calls: list[list[dict]] = []

    async def complete(self, engine, system, user, *, before=None) -> str:
        if before is not None:
            await before()
        items = json.loads(user)["items"]
        self.calls.append(items)
        if self.delay:
            await asyncio.sleep(self.delay)
        result = self.reply(items) if self.reply else None
        if isinstance(result, Exception):
            raise result
        if isinstance(result, str):
            return result
        return json.dumps(
            {
                "items": [
                    {"item_id": item["item_id"], "translated_text": f"译:{item['text']}"}
                    for item in items
                ]
            }
        )

    async def aclose(self) -> None:
        pass


@asynccontextmanager
async def _translator(model: ScriptedModel, **settings):
    dsn = postgres_dsn()
    await reset_schema(dsn)
    config = Settings(_env_file=None, database_url=dsn, database_ssl=False, **settings)
    engine = build_engine(config)
    translator = Translator(build_session_factory(engine), config, model)
    try:
        yield translator, find_engine(config, None)
    finally:
        await translator.aclose()
        await engine.dispose()


def _statuses(outcomes) -> list[tuple[str, str | None]]:
    return [(outcome.status, outcome.text or outcome.error_code) for outcome in outcomes]


@pytest.mark.asyncio
async def test_translations_are_cached_by_text() -> None:
    model = ScriptedModel()
    async with _translator(model) as (translator, engine):
        first = await translator.translate(
            ["Hello", "World", "Hello"], "title", "zh-CN", engine, wait=5
        )
        again = await translator.translate(["  Hello\n"], "title", "zh-CN", engine, wait=5)
        other_purpose = await translator.translate(["Hello"], "caption", "zh-CN", engine, wait=5)

    assert _statuses(first) == [
        ("succeeded", "译:Hello"),
        ("succeeded", "译:World"),
        ("succeeded", "译:Hello"),
    ]
    assert _statuses(again) == [("succeeded", "译:Hello")]
    assert _statuses(other_purpose) == [("succeeded", "译:Hello")]
    # Duplicates in one request and the cached repeat cost no extra calls.
    assert [len(call) for call in model.calls] == [2, 1]


@pytest.mark.asyncio
async def test_the_same_text_at_the_same_time_is_sent_once() -> None:
    model = ScriptedModel(delay=0.2)
    async with _translator(model) as (translator, engine):
        results = await asyncio.gather(
            translator.translate(["Hello"], "title", "zh-CN", engine, wait=0.05),
            translator.translate(["Hello"], "title", "zh-CN", engine, wait=0.05),
        )
        await asyncio.sleep(0.4)
        later = await translator.translate(["Hello"], "title", "zh-CN", engine, wait=1)

    assert [_statuses(result) for result in results] == [[("pending", None)]] * 2
    assert _statuses(later) == [("succeeded", "译:Hello")]
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_a_garbled_batch_is_retried_item_by_item() -> None:
    def reply(items):
        if len(items) > 1:
            return '{"items": []}'
        if items[0]["text"] == "Bad":
            return EngineError("request_rejected", "no", retryable=False, isolate=True)
        return None

    model = ScriptedModel(reply)
    async with _translator(model) as (translator, engine):
        outcomes = await translator.translate(["Good", "Bad"], "title", "zh-CN", engine, wait=5)

    assert _statuses(outcomes) == [("succeeded", "译:Good"), ("failed", "request_rejected")]
    assert [len(call) for call in model.calls] == [2, 1, 1]


@pytest.mark.asyncio
async def test_broken_markers_are_retried_once_with_feedback() -> None:
    text = "Costs ⟪READER_OPEN_0⟫rise⟪READER_CLOSE_0⟫."

    def drop_markers(items):
        return json.dumps(
            {"items": [{"item_id": items[0]["item_id"], "translated_text": "成本上升。"}]}
        )

    model = ScriptedModel(drop_markers)
    async with _translator(model) as (translator, engine):
        outcomes = await translator.translate([text], "web_segment", "zh-CN", engine, wait=5)

    assert _statuses(outcomes) == [("failed", "marker_mismatch")]
    assert len(model.calls) == 2 and "validation_feedback" in model.calls[1][0]


@pytest.mark.asyncio
async def test_the_character_budget_stops_model_calls() -> None:
    model = ScriptedModel()
    async with _translator(model, translation_chars_per_minute=8) as (translator, engine):
        fits = await translator.translate(["Hello"], "title", "zh-CN", engine, wait=5)
        over = await translator.translate(["World"], "title", "zh-CN", engine, wait=5)
        async with translator.factory() as db:
            used = await db.scalar(
                select(TranslationBudget.chars_used).where(TranslationBudget.period == "minute")
            )

    assert _statuses(fits) == [("succeeded", "译:Hello")]
    assert _statuses(over) == [("failed", "translation_quota_exceeded")]
    assert over[0].retry_after == 60
    assert len(model.calls) == 1 and used == 5


@pytest.mark.asyncio
async def test_failed_and_abandoned_work_is_taken_over() -> None:
    model = ScriptedModel(lambda items: EngineError("timeout", "slow"))
    async with _translator(model) as (translator, engine):
        failed = await translator.translate(["Hello"], "title", "zh-CN", engine, wait=5)
        too_soon = await translator.translate(["Hello"], "title", "zh-CN", engine, wait=5)
        model.reply = None
        async with translator.factory() as db:
            long_ago = datetime.now(UTC) - timedelta(minutes=10)
            await db.execute(update(Translation).values(finished_at=long_ago))
            await db.commit()
        retried = await translator.translate(["Hello"], "title", "zh-CN", engine, wait=5)

        # A row left "running" by a process that died is taken over after 5 minutes.
        async with translator.factory() as db:
            db.add(
                Translation(
                    purpose="title",
                    source_hash=text_hash("Orphan"),
                    target_locale="zh-CN",
                    engine_fingerprint=engine.fingerprint,
                    source_text="Orphan",
                    status="running",
                    started_at=long_ago,
                )
            )
            await db.commit()
        orphan = await translator.translate(["Orphan"], "title", "zh-CN", engine, wait=5)
        async with translator.factory() as db:
            attempts = await db.scalar(
                select(Translation.attempts).where(Translation.source_text == "Hello")
            )

    assert _statuses(failed) == [("failed", "timeout")]
    assert _statuses(too_soon) == [("failed", "timeout")]
    assert _statuses(retried) == [("succeeded", "译:Hello")]
    assert _statuses(orphan) == [("succeeded", "译:Orphan")]
    assert attempts == 2 and len(model.calls) == 3


@pytest.mark.asyncio
async def test_a_run_that_was_taken_over_cannot_overwrite_the_new_result() -> None:
    first_call = asyncio.Event()
    release = asyncio.Event()

    class TwoRuns(ScriptedModel):
        async def complete(self, engine, system, user, *, before=None) -> str:
            items = json.loads(user)["items"]
            self.calls.append(items)
            label = "old" if len(self.calls) == 1 else "new"
            if label == "old":
                first_call.set()
                await release.wait()
            return json.dumps(
                {"items": [{"item_id": items[0]["item_id"], "translated_text": label}]}
            )

    model = TwoRuns()
    async with _translator(model) as (translator, engine):
        old = asyncio.create_task(
            translator.translate(["Hello"], "title", "zh-CN", engine, wait=10)
        )
        await first_call.wait()
        # The first run looks abandoned (e.g. stuck for over 5 minutes) and is taken over.
        async with translator.factory() as db:
            await db.execute(
                update(Translation).values(started_at=datetime.now(UTC) - timedelta(minutes=10))
            )
            await db.commit()
        newer = await translator.translate(["Hello"], "title", "zh-CN", engine, wait=5)
        release.set()
        await old
        await asyncio.sleep(0.1)
        final = await translator.translate(["Hello"], "title", "zh-CN", engine, wait=1)

    assert _statuses(newer) == [("succeeded", "new")]
    assert _statuses(final) == [("succeeded", "new")]


@pytest.mark.asyncio
async def test_overlapping_requests_in_opposite_order_do_not_deadlock() -> None:
    model = ScriptedModel(delay=0.01)
    async with _translator(model) as (translator, engine):
        for round_ in range(10):
            a, b = f"A{round_}", f"B{round_}"
            results = await asyncio.gather(
                translator.translate([a, b], "title", "zh-CN", engine, wait=2),
                translator.translate([b, a], "title", "zh-CN", engine, wait=2),
            )
            assert all(outcome.status != "failed" for result in results for outcome in result)


# --- Routes -----------------------------------------------------------------------


async def _signed_in(client) -> dict:
    tokens = (
        await client.post(
            "/auth/register", json={"email": "me@example.com", "password": "long password 1"}
        )
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_preference_defaults_and_updates(monkeypatch) -> None:
    monkeypatch.setenv("APP_DEEPSEEK_API_KEY", "k")
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        default = await client.get("/me/translation-preference", headers=auth)
        changed = await client.patch(
            "/me/translation-preference",
            json={"target_locale": "ja", "enabled": False},
            headers=auth,
        )
        unknown = await client.patch(
            "/me/translation-preference", json={"engine_id": "nope"}, headers=auth
        )
        unconfigured = await client.patch(
            "/me/translation-preference", json={"engine_id": "codex-subscription"}, headers=auth
        )
        bad_locale = await client.patch(
            "/me/translation-preference", json={"target_locale": "not a locale"}, headers=auth
        )
        reset = await client.patch(
            "/me/translation-preference", json={"engine_id": None}, headers=auth
        )

    body = default.json()
    assert (body["target_locale"], body["enabled"], body["engine_id"]) == (
        "zh-CN",
        True,
        "deepseek-v4-flash",
    )
    assert {engine["id"]: engine["available"] for engine in body["engines"]} == {
        "deepseek-v4-flash": True,
        "openrouter-minimax-m3": False,
        "codex-subscription": False,
    }
    assert (changed.json()["target_locale"], changed.json()["enabled"]) == ("ja", False)
    assert unknown.json()["error"]["code"] == "unknown_engine"
    assert unconfigured.json()["error"]["code"] == "engine_unavailable"
    assert bad_locale.json()["error"]["code"] == "invalid_request"
    assert reset.json()["engine_id"] == "deepseek-v4-flash"
    assert reset.json()["target_locale"] == "ja"


@pytest.mark.asyncio
async def test_translate_route(monkeypatch) -> None:
    monkeypatch.setenv("APP_DEEPSEEK_API_KEY", "k")
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        model = ScriptedModel()
        client.app.state.translator = Translator(
            client.app.state.session_factory, Settings(_env_file=None), model
        )
        await client.patch("/me/translation-preference", json={"target_locale": "ja"}, headers=auth)
        response = await client.post(
            "/translations", json={"purpose": "paragraph", "texts": ["One", "Two"]}, headers=auth
        )
        explicit = await client.post(
            "/translations",
            json={"purpose": "title", "texts": ["One"], "target_locale": "fr"},
            headers=auth,
        )
        too_many = await client.post(
            "/translations", json={"purpose": "title", "texts": ["x"] * 101}, headers=auth
        )
        nul = await client.post(
            "/translations", json={"purpose": "title", "texts": ["a\u0000b"]}, headers=auth
        )
        async with client.app.state.session_factory() as db:
            rows = await db.scalar(select(func.count()).select_from(Translation))

    body = response.json()
    assert body["target_locale"] == "ja" and body["engine_id"] == "deepseek-v4-flash"
    assert [(item["status"], item["text"]) for item in body["items"]] == [
        ("succeeded", "译:One"),
        ("succeeded", "译:Two"),
    ]
    assert explicit.json()["target_locale"] == "fr"
    assert too_many.json()["error"]["code"] == "invalid_request"
    assert nul.json()["error"]["code"] == "invalid_request"
    assert rows == 3


@pytest.mark.asyncio
async def test_no_configured_engine_means_unavailable(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        response = await client.post(
            "/translations", json={"purpose": "title", "texts": ["One"]}, headers=auth
        )

    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "translation_unavailable",
    )
