"""Real persistence seams for Crawl4AI rules on the guarded disposable database."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from test_candidate_recovery_postgres import _database

from app.domain.enums import ExtractionStatus, SourceKind, SyncRunStatus
from app.ingestion.candidate_queue import upsert_candidates
from app.ingestion.models import SourceScanError, SourceScanPage, SourceScanRequest
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.web_rules import parse_web_rule
from app.services import web_rules
from app.storage.models import (
    Content,
    FeedSource,
    IngestionCandidate,
    SourceSyncRun,
    SourceSyncState,
)
from app.workers import candidates, sync

URL = "https://example.com/feed"
RULE = {
    "id": "native-persistence",
    "hosts": ["example.com"],
    "index_paths": ["/feed"],
    "listing": {
        "extraction": {
            "baseSelector": "article",
            "fields": [
                {"name": "url", "type": "attribute", "selector": "a", "attribute": "href"},
                {"name": "title", "type": "text", "selector": "h2"},
            ],
        }
    },
}
PASS = {
    "status": "completed",
    "execution_id": "persisted-execution",
    "first_window_completed": True,
    "items": [],
    "completed_windows": 1,
}


@pytest.mark.postgres
async def test_rule_activation_rejects_stale_scan_and_preserves_versions(monkeypatch):
    validator = AsyncMock(return_value=PASS)
    monkeypatch.setattr(web_rules, "execute_web_rule", validator)
    async with _database() as (factory, source_id):
        token, run_id = uuid4(), uuid4()
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            source.kind = SourceKind.WEB
            source.config = {
                "web_feed_discovery": {
                    "version": 1,
                    "source_url": source.canonical_url,
                    "status": "found",
                    "feed_url": "https://example.com/rss.xml",
                }
            }
            session.add(
                SourceSyncState(
                    source_id=source_id,
                    lease_token=token,
                    lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
                    committed_checkpoint={"head_ids": ["known"], "validators": {"etag": "old"}},
                    pending_checkpoint={"old": True},
                    continuation={"old": True},
                )
            )
            session.add(SourceSyncRun(id=run_id, source_id=source_id, status=SyncRunStatus.RUNNING))
            await session.commit()
        async with factory() as session:
            first = await web_rules.submit_web_rule(session, source_id=source_id, raw_rule=RULE)
        assert first["status"] == "accepted"
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            assert "web_feed_discovery" not in source.config
            async with httpx.AsyncClient() as client:
                assert isinstance(sync._build_adapter(source, client), WebBlogSourceAdapter)
            state = await session.get(SourceSyncState, source_id)
            assert state.lease_token is None and state.lease_expires_at is None
            assert state.committed_checkpoint == {"head_ids": ["known"]}
            assert state.continuation is None and state.pending_checkpoint == {}
            run = await session.get(SourceSyncRun, run_id)
            assert run.status == SyncRunStatus.PARTIAL
            assert run.error_code == "web_rule_replaced"
        with pytest.raises(SourceScanError, match="lease was lost"):
            await sync._persist_page(
                factory,
                sync.SourceClaim(source_id, token),
                run_id,
                SourceScanPage(items=(), provider_mode="old", raw_items=0, completed=True),
                candidate_items=(),
                max_changes=10,
                baseline_checkpoint={},
                mutate=lambda *_args: pytest.fail("stale scanner reached state mutation"),
            )
        revised = {**RULE, "id": "native-revised"}
        validator.return_value = {"status": "fail", "code": "web_structure_changed"}
        async with factory() as session:
            failed = await web_rules.submit_web_rule(session, source_id=source_id, raw_rule=revised)
        assert failed["status"] == "rejected"
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            assert source.config["web_rule_active_version"] == first["version"]
            assert source.config["web_rule_candidate"]["validation"]["status"] == "fail"
        validator.return_value = PASS
        async with factory() as session:
            second = await web_rules.submit_web_rule(session, source_id=source_id, raw_rule=revised)
        assert second["status"] == "accepted" and second["version"] != first["version"]
        async with factory() as session:
            rollback = await web_rules.rollback_web_rule(
                session,
                source_id=source_id,
                version=first["version"],
            )
        assert rollback["status"] == "accepted" and rollback["version"] == first["version"]
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(IngestionCandidate)
                    .where(IngestionCandidate.source_id == source_id)
                )
                == 0
            )


@pytest.mark.postgres
@pytest.mark.parametrize("text_only", [False, True])
@pytest.mark.parametrize("summary", ["", "A new short listing summary."])
async def test_native_listing_replay_and_new_update_persist_without_body(
    monkeypatch, text_only, summary
):
    import copy

    listing = '<article><a href="/one"><h2>One</h2></a></article>'
    listing_rule = copy.deepcopy(RULE)
    listing_rule["listing"]["extraction"]["fields"].append(
        {"name": "excerpt", "type": "text", "selector": ".summary"}
    )
    requested = []

    async def fetch(_client, url, **_kwargs):
        requested.append(url)
        body = "User-agent: *\nAllow: /" if url.endswith("robots.txt") else listing
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    # No translation provider is part of this storage integration test.
    monkeypatch.setattr(candidates, "_enqueue_default_title_translation", AsyncMock())
    async with _database() as (factory, source_id):
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            source.kind = SourceKind.WEB
            await session.commit()
        async with httpx.AsyncClient() as client:
            adapter = WebBlogSourceAdapter(
                WebBlogSourceConfig(url=URL, rule=parse_web_rule(listing_rule, source_url=URL)),
                client,
            )

            async def discover():
                page = await adapter.scan_page(SourceScanRequest(initial=True, conditional=False))
                async with factory() as session:
                    result = await upsert_candidates(
                        session,
                        source_id=source_id,
                        items=page.items,
                        observed_at=datetime.now(UTC),
                        max_changes=10,
                    )
                    await session.commit()
                await candidates.process_candidates(factory)
                return result

            assert (await discover()).changed == 1
            assert (await discover()).changed == 0
            async with factory() as session:
                first = await session.scalar(
                    select(Content).where(Content.authority_source_id == source_id)
                )
                assert first.title == "One" and not first.body_html
                first.body_html = None if text_only else "<p>Previously extracted article body.</p>"
                first.body_text = "Previously extracted article body."
                first.extraction_status = ExtractionStatus.SUCCESS
                await session.commit()
            listing = (
                '<article><a href="/two"><h2>Two</h2></a></article>'
                '<article><a href="/one"><h2>One updated</h2></a>'
                f'<p class="summary">{summary}</p></article>'
            )
            assert (await discover()).changed == 2
        async with factory() as session:
            contents = list(
                await session.scalars(
                    select(Content).where(Content.authority_source_id == source_id)
                )
            )
            assert len(contents) == 2
            first = next(item for item in contents if item.title == "One updated")
            assert first.body_html == (
                None if text_only else "<p>Previously extracted article body.</p>"
            )
            assert first.body_text == "Previously extracted article body."
            assert first.extraction_status == ExtractionStatus.SUCCESS
            assert not next(item for item in contents if item.title == "Two").body_html
        assert all(url in {URL, "https://example.com/robots.txt"} for url in requested)


@pytest.mark.postgres
async def test_retirement_migration_preserves_subscriptions_and_content(monkeypatch):
    import asyncpg
    from sqlalchemy import delete, text
    from test_postgres_migrations import (
        _assert_disposable_database,
        _run_alembic,
        _test_dsn,
        _test_guard_token,
    )

    from app.storage.models import SourceEntry, SourceSubscription, UserSavedContent, WebFrontier

    dsn = _test_dsn()
    connection = await asyncpg.connect(dsn)
    await _assert_disposable_database(connection, _test_guard_token())
    user_id, rss_id, uninitialized_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    try:
        # This guarded, disposable database is serialized by the test runner.
        # Build the historical fixture from empty schemas instead of downgrading
        # through the data-preserving Reader auth boundary at revision 28.
        await connection.execute(
            "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; "
            "DROP SCHEMA IF EXISTS auth CASCADE"
        )
        _run_alembic(dsn, "upgrade", "20260908_24")
        await connection.execute(
            "INSERT INTO profiles (id) VALUES ($1)",
            user_id,
        )
        async with _database() as (factory, source_id):
            async with factory() as session:
                source = await session.get(FeedSource, source_id)
                source.kind = SourceKind.WEB
                source.config = {
                    "web_rule": {"article_body_selectors": ["article"]},
                    "web_rule_versions": [{"rule": {"id": "retired"}}],
                    "web_rule_active_version": "old",
                    "web_rule_candidate": {"rule": {}},
                    "profile_id": "langchain_blog",
                    "feed_url": "https://example.com/rss",
                    "unrelated_setting": "keep",
                }
                session.add_all(
                    [
                        FeedSource(
                            id=rss_id,
                            kind=SourceKind.RSS,
                            canonical_key=f"rss-control:{rss_id}",
                            canonical_url=URL,
                            display_name="RSS control",
                            config={"keep": "rss"},
                        ),
                        FeedSource(
                            id=uninitialized_id,
                            kind=SourceKind.WEB,
                            canonical_key=f"web-no-state:{uninitialized_id}",
                            canonical_url="https://example.com/news",
                            display_name="No state",
                        ),
                    ]
                )
                await session.flush()
                content = Content(
                    authority_source_id=source_id,
                    kind="article",
                    title="Preserved article",
                    body_html="<p>Keep body</p>",
                )
                session.add(content)
                await session.flush()
                content_id = content.id
                session.add_all(
                    [
                        SourceEntry(
                            source_id=source_id,
                            content_id=content_id,
                            native_id="kept",
                            title="Preserved article",
                            external_url="https://example.com/kept",
                            feed_sort_at=now,
                        ),
                        SourceSubscription(user_id=user_id, source_id=source_id),
                        UserSavedContent(user_id=user_id, content_id=content_id),
                        SourceSyncRun(source_id=source_id, status=SyncRunStatus.RUNNING),
                        WebFrontier(
                            source_id=source_id,
                            original_url="https://example.com/old",
                            normalized_url="https://example.com/old",
                            url_hash="a" * 64,
                        ),
                    ]
                )
                # The fixture deliberately runs against revision 24. Use only
                # that schema's columns, independently of newer ORM additions.
                await session.execute(
                    text("""
                        INSERT INTO source_sync_states (
                            source_id, phase, initial_sync_completed,
                            committed_checkpoint, pending_checkpoint, continuation,
                            lease_token, lease_expires_at, next_scan_at,
                            consecutive_failures, consecutive_limit_runs, gap_detected
                        ) VALUES (
                            :source_id, 'idle', true,
                            '{"head_ids":["old"],"feed_url":"old"}', '{"old": true}',
                            '{"old": true}', :token, :expires, :now, 0, 0, false
                        ), (
                            :rss_id, 'idle', false, '{"rss":"keep"}', '{}', NULL,
                            NULL, NULL, :now, 0, 0, false
                        )
                    """),
                    {
                        "source_id": source_id,
                        "rss_id": rss_id,
                        "token": uuid4(),
                        "expires": now + timedelta(minutes=5),
                        "now": now,
                    },
                )
                for status in ("pending", "processing", "failed", "completed"):
                    session.add(
                        IngestionCandidate(
                            source_id=source_id,
                            native_id=status,
                            payload={"old": True},
                            payload_hash="a" * 64,
                            observed_at=now,
                            suggested_feed_sort_at=now,
                            status=status,
                        )
                    )
                session.add(
                    IngestionCandidate(
                        source_id=rss_id,
                        native_id="rss",
                        payload={"keep": True},
                        payload_hash="b" * 64,
                        observed_at=now,
                        suggested_feed_sort_at=now,
                    )
                )
                await session.commit()
            _run_alembic(dsn, "upgrade", "20260909_25")
            _run_alembic(dsn, "upgrade", "head")
            async with factory() as session:
                source = await session.get(FeedSource, source_id)
                assert source.config == {"unrelated_setting": "keep", "web_rule_revision": 0}
                state = await session.get(SourceSyncState, source_id)
                assert state.last_error_code == "web_rule_required"
                assert state.next_scan_at is None and state.lease_token is None
                assert state.committed_checkpoint == {} and state.pending_checkpoint == {}
                assert state.continuation is None and state.initial_sync_completed
                other = await session.get(SourceSyncState, uninitialized_id)
                assert other.last_error_code == "web_rule_required" and other.next_scan_at is None
                rss = await session.get(SourceSyncState, rss_id)
                assert rss.committed_checkpoint == {"rss": "keep"} and rss.next_scan_at == now
                assert (await session.get(Content, content_id)).body_html == "<p>Keep body</p>"
                assert (
                    await session.scalar(
                        select(SourceSubscription).where(SourceSubscription.source_id == source_id)
                    )
                    is not None
                )
                assert (
                    await session.scalar(
                        select(UserSavedContent).where(UserSavedContent.content_id == content_id)
                    )
                    is not None
                )
                assert list(
                    await session.scalars(
                        select(IngestionCandidate.status).where(
                            IngestionCandidate.source_id == source_id
                        )
                    )
                ) == ["completed"]
                assert (
                    await session.scalar(
                        select(IngestionCandidate).where(IngestionCandidate.source_id == rss_id)
                    )
                    is not None
                )
                assert (
                    await session.scalar(
                        select(WebFrontier).where(WebFrontier.source_id == source_id)
                    )
                    is None
                )
                run = await session.scalar(
                    select(SourceSyncRun).where(SourceSyncRun.source_id == source_id)
                )
                assert run.status == SyncRunStatus.PARTIAL and run.error_code == "web_rule_required"
            async with factory() as session:
                waiting = await web_rules.list_web_sources_needing_rule(session)
                waiting_ids = {row["source_id"] for row in waiting["sources"]}
                assert {str(source_id), str(uninitialized_id)} <= waiting_ids
                assert str(rss_id) not in waiting_ids
            monkeypatch.setattr(web_rules, "execute_web_rule", AsyncMock(return_value=PASS))
            async with factory() as session:
                accepted = await web_rules.submit_web_rule(
                    session,
                    source_id=source_id,
                    raw_rule=RULE,
                )
                assert accepted["status"] == "accepted"
            async with factory() as session:
                state = await session.get(SourceSyncState, source_id)
                assert state.next_scan_at is not None and state.last_error_code is None
                waiting = await web_rules.list_web_sources_needing_rule(session)
                assert str(source_id) not in {row["source_id"] for row in waiting["sources"]}
                await session.execute(
                    delete(FeedSource).where(FeedSource.id.in_([rss_id, uninitialized_id]))
                )
                await session.commit()
    finally:
        # A failed migration assertion must not leave subsequent tests on an
        # old schema and disguise their actual behavior with missing columns.
        _run_alembic(dsn, "upgrade", "head")
        await connection.execute("DELETE FROM profiles WHERE id = $1", user_id)
        await connection.close()
