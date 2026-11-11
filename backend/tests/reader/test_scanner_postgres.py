"""Scanner behaviour against a real database, with a scripted adapter."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from reader.core.db import build_engine, build_session_factory
from reader.core.settings import Settings
from reader.ingestion import scanner
from reader.ingestion.errors import ScanError
from reader.ingestion.items import Item, Page
from reader.ingestion.retention import prune_articles, prune_scan_runs
from reader.models import Article, SavedArticle, Source, SourceScanRun, Subscription, User
from tests.reader.pg import postgres_dsn, reset_schema

pytestmark = pytest.mark.postgres


def _item(n: int, **extra) -> Item:
    return Item(
        native_id=f"id-{n}", kind="article", title=f"Item {n}", url=f"https://e.com/{n}", **extra
    )


def _items(*numbers: int) -> list[Item]:
    return [_item(n) for n in numbers]


class ScriptedAdapter:
    """Returns pages by cursor; records what was asked."""

    def __init__(self, pages: dict[str | None, Page] | None = None, error: ScanError | None = None):
        self.pages = pages or {}
        self.error = error
        self.calls: list[tuple[str | None, int]] = []

    async def fetch_page(self, cursor, limit):
        self.calls.append((cursor, limit))
        if self.error:
            raise self.error
        page = self.pages[cursor]
        return Page(
            items=page.items[:limit],
            next_cursor=page.next_cursor,
            chronological=page.chronological,
            source_name=page.source_name,
            source_icon_url=page.source_icon_url,
        )


@asynccontextmanager
async def _database(**settings):
    dsn = postgres_dsn()
    await reset_schema(dsn)
    config = Settings(database_url=dsn, database_ssl=False, **settings)
    engine = build_engine(config)
    try:
        yield build_session_factory(engine), config
    finally:
        await engine.dispose()


async def _followed_source(factory, kind="rss", **fields) -> Source:
    async with factory() as db:
        user = User(email=f"{uuid4()}@e.com", password_hash="x")
        source = Source(
            kind=kind,
            canonical_key=f"{kind}:{uuid4()}",
            url="https://e.com/feed",
            config=fields.pop("config", {}),
            next_scan_at=datetime.now(UTC) - timedelta(seconds=1),
            **fields,
        )
        db.add_all([user, source])
        await db.flush()
        db.add(Subscription(user_id=user.id, source_id=source.id))
        await db.commit()
        return source


async def _scan(factory, config, monkeypatch, adapter) -> None:
    monkeypatch.setattr(scanner, "_build_adapter", lambda *args: adapter)
    async with factory() as db:
        source = await db.scalar(select(Source))
        source.next_scan_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    claims = await scanner.claim_due_sources(factory, config, 10)
    assert len(claims) == 1
    await scanner.scan_source(factory, config, claims[0])


async def _state(factory):
    async with factory() as db:
        source = await db.scalar(select(Source))
        articles = list(
            await db.execute(
                select(Article.native_id, Article.sequence).order_by(
                    Article.sequence.nulls_first(), Article.native_id
                )
            )
        )
        runs = list(await db.scalars(select(SourceScanRun).order_by(SourceScanRun.started_at)))
        return source, [tuple(row) for row in articles], runs


@pytest.mark.asyncio
async def test_first_scan_is_a_baseline_and_later_items_get_sequences(monkeypatch) -> None:
    async with _database(scan_initial_items=3) as (factory, config):
        await _followed_source(factory)
        first = ScriptedAdapter(
            {
                None: Page(
                    _items(5, 4, 3, 2, 1), source_name="Feed", source_icon_url="https://e.com/i.png"
                )
            }
        )
        await _scan(factory, config, monkeypatch, first)
        source, articles, runs = await _state(factory)

        assert first.calls == [(None, 3)]
        assert articles == [("id-3", None), ("id-4", None), ("id-5", None)]
        assert (source.status, source.initial_scan_done, source.latest_sequence) == (
            "active",
            True,
            0,
        )
        assert (source.display_name, source.icon_url) == ("Feed", "https://e.com/i.png")
        assert source.lease_token is None and source.next_scan_at > datetime.now(UTC)
        assert [(run.status, run.items_read, run.new_items) for run in runs] == [("success", 3, 0)]

        # Two new items above the known ones: oldest new gets the lower sequence.
        await _scan(factory, config, monkeypatch, ScriptedAdapter({None: Page(_items(7, 6, 5, 4))}))
        source, articles, _ = await _state(factory)

    assert ("id-6", 1) in articles and ("id-7", 2) in articles
    assert source.latest_sequence == 2 and source.last_new_items == 2
    assert not source.may_have_gap


@pytest.mark.asyncio
async def test_next_page_only_until_a_known_item_is_seen(monkeypatch) -> None:
    async with _database(scan_initial_items=1) as (factory, config):
        await _followed_source(factory)
        await _scan(factory, config, monkeypatch, ScriptedAdapter({None: Page(_items(1))}))
        adapter = ScriptedAdapter(
            {
                None: Page(_items(5, 4), next_cursor="p2"),
                "p2": Page(_items(3, 2), next_cursor="p3"),
                "p3": Page(_items(1, 0), next_cursor="p4"),
            }
        )
        await _scan(factory, config, monkeypatch, adapter)
        source, articles, _ = await _state(factory)

    assert [cursor for cursor, _ in adapter.calls] == [None, "p2", "p3"]  # stopped at id-1
    assert sorted(native for native, sequence in articles if sequence) == [
        "id-0",
        "id-2",
        "id-3",
        "id-4",
        "id-5",
    ]
    assert not source.may_have_gap


@pytest.mark.asyncio
async def test_item_limit_ends_the_scan_and_flags_a_possible_gap(monkeypatch) -> None:
    async with _database(scan_initial_items=1, scan_max_items=10) as (factory, config):
        await _followed_source(factory)
        await _scan(factory, config, monkeypatch, ScriptedAdapter({None: Page(_items(0))}))
        adapter = ScriptedAdapter(
            {
                None: Page(_items(*range(20, 14, -1)), next_cursor="p2"),
                "p2": Page(_items(*range(14, 8, -1)), next_cursor="p3"),
            }
        )
        await _scan(factory, config, monkeypatch, adapter)
        source, articles, _ = await _state(factory)

    assert adapter.calls == [(None, 10), ("p2", 4)]
    assert source.last_new_items == 10
    assert source.may_have_gap


@pytest.mark.asyncio
async def test_a_known_pinned_post_does_not_end_the_scan(monkeypatch) -> None:
    async with _database(scan_initial_items=1) as (factory, config):
        await _followed_source(factory, kind="x", config={"handle": "w"})
        await _scan(factory, config, monkeypatch, ScriptedAdapter({None: Page([_item(1)])}))
        pinned_known = _item(1, pinned=True)
        adapter = ScriptedAdapter(
            {
                None: Page([pinned_known, _item(3)], next_cursor="p2"),
                "p2": Page([_item(2), _item(1)]),
            }
        )
        await _scan(factory, config, monkeypatch, adapter)
        _, articles, _ = await _state(factory)

    assert [cursor for cursor, _ in adapter.calls] == [None, "p2"]
    assert {native for native, sequence in articles if sequence} == {"id-2", "id-3"}


@pytest.mark.asyncio
async def test_a_failure_on_a_later_page_writes_nothing_so_the_retry_sees_everything(
    monkeypatch,
) -> None:
    async with _database(scan_initial_items=1) as (factory, config):
        await _followed_source(factory)
        await _scan(factory, config, monkeypatch, ScriptedAdapter({None: Page(_items(1))}))
        pages = {None: Page(_items(5, 4), next_cursor="p2"), "p2": Page(_items(3, 2, 1))}

        class SecondPageFails(ScriptedAdapter):
            async def fetch_page(self, cursor, limit):
                if cursor == "p2":
                    raise ScanError("rss_http_500", "boom")
                return await super().fetch_page(cursor, limit)

        await _scan(factory, config, monkeypatch, SecondPageFails(pages))
        source, articles, _ = await _state(factory)
        assert articles == [("id-1", None)] and source.latest_sequence == 0

        await _scan(factory, config, monkeypatch, ScriptedAdapter(pages))
        source, articles, _ = await _state(factory)

    assert articles[1:] == [("id-2", 1), ("id-3", 2), ("id-4", 3), ("id-5", 4)]
    assert not source.may_have_gap


@pytest.mark.asyncio
async def test_an_empty_page_is_not_a_gap(monkeypatch) -> None:
    async with _database(scan_initial_items=1) as (factory, config):
        await _followed_source(factory)
        await _scan(factory, config, monkeypatch, ScriptedAdapter({None: Page(_items(1))}))
        await _scan(factory, config, monkeypatch, ScriptedAdapter({None: Page([])}))
        source, _, _ = await _state(factory)

    assert source.last_new_items == 0 and not source.may_have_gap


@pytest.mark.asyncio
async def test_popularity_lists_never_flag_a_gap(monkeypatch) -> None:
    async with _database(scan_initial_items=2) as (factory, config):
        await _followed_source(factory, kind="reddit", config={"subreddit": "python"})
        await _scan(
            factory,
            config,
            monkeypatch,
            ScriptedAdapter({None: Page(_items(1, 2), chronological=False)}),
        )
        await _scan(
            factory,
            config,
            monkeypatch,
            ScriptedAdapter({None: Page(_items(9, 8), chronological=False)}),
        )
        source, _, _ = await _state(factory)

    assert source.last_new_items == 2 and not source.may_have_gap


@pytest.mark.asyncio
async def test_an_edited_item_is_updated_without_counting_as_new(monkeypatch) -> None:
    async with _database(scan_initial_items=5) as (factory, config):
        await _followed_source(factory)
        dated = _item(1, published_at=datetime(2024, 1, 1, tzinfo=UTC))
        await _scan(factory, config, monkeypatch, ScriptedAdapter({None: Page([dated])}))
        edited = Item(native_id="id-1", kind="article", title="Edited", url="https://e.com/1")
        await _scan(factory, config, monkeypatch, ScriptedAdapter({None: Page([edited])}))
        async with factory() as db:
            article = await db.scalar(select(Article))
            source = await db.scalar(select(Source))

    assert article.title == "Edited" and article.sequence is None
    # A missing date in a later observation keeps the known one.
    assert article.published_at == datetime(2024, 1, 1, tzinfo=UTC)
    assert source.latest_sequence == 0


@pytest.mark.asyncio
async def test_failures_are_recorded_and_backed_off(monkeypatch) -> None:
    async with _database() as (factory, config):
        await _followed_source(factory)
        await _scan(
            factory, config, monkeypatch, ScriptedAdapter(error=ScanError("rss_http_500", "boom"))
        )
        source, _, runs = await _state(factory)
        assert (source.consecutive_failures, source.last_error_code) == (1, "rss_http_500")
        assert source.lease_token is None and source.status == "pending"
        assert timedelta(minutes=1) < source.next_scan_at - datetime.now(UTC) < timedelta(minutes=3)
        assert (runs[0].status, runs[0].error_code) == ("failed", "rss_http_500")

        await _scan(
            factory,
            config,
            monkeypatch,
            ScriptedAdapter(error=ScanError("gone", "x", long_lived=True)),
        )
        source, _, _ = await _state(factory)

    assert source.consecutive_failures == 2
    assert source.next_scan_at - datetime.now(UTC) > timedelta(hours=23)


@pytest.mark.asyncio
async def test_unexpected_errors_are_contained(monkeypatch) -> None:
    class Broken:
        async def fetch_page(self, cursor, limit):
            raise RuntimeError("bug")

    async with _database() as (factory, config):
        await _followed_source(factory)
        await _scan(factory, config, monkeypatch, Broken())
        source, _, _ = await _state(factory)

    assert source.last_error_code == "internal_error" and source.lease_token is None


@pytest.mark.asyncio
async def test_a_scan_that_lost_its_lease_writes_nothing(monkeypatch) -> None:
    async with _database() as (factory, config):
        await _followed_source(factory)
        claims = await scanner.claim_due_sources(factory, config, 10)

        class Thief:
            async def fetch_page(self, cursor, limit):
                async with factory() as db:  # another worker took the source over
                    source = await db.scalar(select(Source))
                    source.lease_token = uuid4()
                    await db.commit()
                return Page(_items(1))

        monkeypatch.setattr(scanner, "_build_adapter", lambda *args: Thief())
        await scanner.scan_source(factory, config, claims[0])
        async with factory() as db:
            count = await db.scalar(select(func.count()).select_from(Article))
            source = await db.scalar(select(Source))
            run = await db.scalar(select(SourceScanRun))

    assert count == 0
    assert source.lease_token is not None and not source.initial_scan_done
    assert (run.status, run.error_code) == ("failed", "lease_lost")


@pytest.mark.asyncio
async def test_only_followed_due_unleased_sources_are_claimed() -> None:
    async with _database() as (factory, config):
        followed = await _followed_source(factory)
        async with factory() as db:
            past = datetime.now(UTC) - timedelta(minutes=1)
            db.add_all(
                [
                    Source(kind="rss", canonical_key="rss:unfollowed", url="u", next_scan_at=past),
                ]
            )
            await db.commit()
        first = await scanner.claim_due_sources(factory, config, 10)
        second = await scanner.claim_due_sources(factory, config, 10)

    assert [claim.source_id for claim in first] == [followed.id]
    assert second == []  # still leased


@pytest.mark.asyncio
async def test_retention_keeps_saved_and_newest_articles() -> None:
    async with _database() as (factory, config):
        source = await _followed_source(factory)
        async with factory() as db:
            user = await db.scalar(select(User))
            base = datetime(2024, 1, 1, tzinfo=UTC)
            articles = [
                Article(
                    source_id=source.id,
                    native_id=str(n),
                    url=f"u{n}",
                    url_hash=str(n),
                    kind="article",
                    title=str(n),
                    content_hash="h",
                    first_seen_at=base + timedelta(hours=n),
                )
                for n in range(5)
            ]
            board = Article(
                source_id=None,
                native_id="b",
                url="b",
                url_hash="b",
                kind="article",
                title="b",
                content_hash="h",
            )
            db.add_all([*articles, board])
            await db.flush()
            db.add(SavedArticle(user_id=user.id, article_id=articles[0].id))
            for n in range(25):
                db.add(
                    SourceScanRun(
                        source_id=source.id,
                        status="success",
                        started_at=base + timedelta(minutes=n),
                    )
                )
            await db.commit()
            removed = await prune_articles(db, keep_per_source=2)
            pruned_runs = await prune_scan_runs(db, keep_per_source=20)
            kept = sorted(await db.scalars(select(Article.native_id)))

    assert kept == ["0", "3", "4"]  # newest two plus the saved one; the unsaved board item went
    assert removed == 3 and pruned_runs == 5
