from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.services.subscriptions import (
    SourceSubscriptionLimitExceeded,
    enforce_reddit_subscription_limit,
)
from app.storage.profiles import ensure_profile


def test_existing_usable_native_rule_skips_feed_discovery_and_authoring():
    from types import SimpleNamespace

    from test_web_rule_triggers import RULE, URL

    from app.domain.enums import SourceKind
    from app.ingestion.web_feed import web_feed_probe_required
    from app.services.subscriptions import web_source_needs_rule

    source = SimpleNamespace(kind=SourceKind.WEB, canonical_url=URL, config={"web_rule": RULE})
    assert not web_feed_probe_required(URL, source.config)
    assert not web_source_needs_rule(source)


@pytest.mark.parametrize("status", ["found", "not_found"])
@pytest.mark.parametrize("mismatch", ["source_url", "version"])
def test_discovery_from_another_source_or_version_cannot_skip_probe(status, mismatch):
    from app.ingestion.web_feed import configured_web_feed_url, web_feed_probe_required

    url = "https://example.com/blog"
    record = {
        "version": 1,
        "source_url": url,
        "status": status,
        "feed_url": "https://example.com/rss",
    }
    record[mismatch] = "https://example.com/other-column" if mismatch == "source_url" else 2
    config = {"web_feed_discovery": record}
    assert configured_web_feed_url(url, config) is None
    assert web_feed_probe_required(url, config)


@pytest.mark.asyncio
async def test_reddit_subscription_limit_is_serialized_and_enforced() -> None:
    session = AsyncMock()
    session.scalar.side_effect = [False, 20]

    with pytest.raises(SourceSubscriptionLimitExceeded):
        await enforce_reddit_subscription_limit(
            session,
            user_id=uuid4(),
            canonical_key="reddit:newcommunity",
            limit=20,
        )

    lock = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "pg_advisory_xact_lock" in lock


@pytest.mark.asyncio
async def test_existing_reddit_subscription_does_not_consume_another_slot() -> None:
    session = AsyncMock()
    session.scalar.return_value = True

    await enforce_reddit_subscription_limit(
        session,
        user_id=uuid4(),
        canonical_key="reddit:existing",
        limit=20,
    )

    assert session.scalar.await_count == 1


@pytest.mark.asyncio
async def test_legacy_profile_creation_is_a_database_level_upsert() -> None:
    session = AsyncMock()

    await ensure_profile(session, uuid4())

    statement = session.execute.await_args.args[0]
    rendered = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (id) DO NOTHING" in rendered


@pytest.mark.parametrize("web", [True, False])
async def test_new_web_subscription_schedules_feed_probe_before_any_rule_job(monkeypatch, web):
    from types import SimpleNamespace

    from app.domain.enums import SourceKind, SyncPhase
    from app.ingestion.source_identity import canonical_rss_identity, canonical_web_identity
    from app.services import subscriptions

    source = SimpleNamespace(
        id=uuid4(),
        config={},
        kind=SourceKind.WEB if web else SourceKind.RSS,
        canonical_url="https://example.com/news",
        latest_update_sequence=0,
    )
    state = SimpleNamespace(next_scan_at=None)
    subscription = SimpleNamespace(id=uuid4())
    session = AsyncMock()
    session.get.side_effect = [None, state]
    session.scalar.return_value = subscription.id
    monkeypatch.setattr(subscriptions, "ensure_profile", AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr(subscriptions, "ensure_web_rule_job", enqueue)
    monkeypatch.setattr(subscriptions, "_find_source", AsyncMock(return_value=source))
    monkeypatch.setattr(
        subscriptions, "_find_subscription", AsyncMock(side_effect=[None, subscription])
    )
    identity = (canonical_web_identity if web else canonical_rss_identity)(
        "https://example.com/news"
    )
    await subscriptions.subscribe_to_shared_source(
        session,
        user_id=uuid4(),
        identity=identity,
        display_name=None,
        folder_name=None,
    )
    params = session.execute.await_args_list[0].args[0].compile(dialect=postgresql.dialect()).params
    assert params["phase"] == SyncPhase.IDLE
    assert params["last_error_code"] == ("web_feed_discovering" if web else None)
    enqueue.assert_not_awaited()
    assert params["next_scan_at"] is not None
    assert state.next_scan_at is not None


@pytest.mark.parametrize("stored_rule", [None, {"old_selectors": ["article"]}])
@pytest.mark.parametrize("probe_status", [None, "retry", "not_found", "found"])
@pytest.mark.parametrize("was_enabled", [False, True])
async def test_existing_web_subscription_only_dispatches_after_feed_probe_not_found(
    monkeypatch, stored_rule, probe_status, was_enabled
):
    from types import SimpleNamespace

    from app.domain.enums import SourceKind, SyncPhase
    from app.ingestion.source_identity import canonical_web_identity
    from app.services import subscriptions

    url = "https://example.com/blog"
    discovery = (
        {
            "web_feed_discovery": {
                "version": 1,
                "source_url": url,
                "status": probe_status,
                **({"feed_url": "https://example.com/feed.xml"} if probe_status == "found" else {}),
            }
        }
        if probe_status
        else {}
    )
    source = SimpleNamespace(
        id=uuid4(),
        config={"web_rule": stored_rule, **discovery},
        kind=SourceKind.WEB,
        canonical_url=url,
    )
    state = SimpleNamespace(
        next_scan_at=None,
        phase=SyncPhase.DEGRADED,
        last_error_code="web_rule_required",
    )
    subscription = SimpleNamespace(id=uuid4(), is_enabled=was_enabled)
    session, enqueue = AsyncMock(), AsyncMock()
    session.get.return_value = state
    monkeypatch.setattr(subscriptions, "ensure_profile", AsyncMock())
    monkeypatch.setattr(subscriptions, "_find_source", AsyncMock(return_value=source))
    monkeypatch.setattr(subscriptions, "_find_subscription", AsyncMock(return_value=subscription))
    monkeypatch.setattr(subscriptions, "ensure_web_rule_job", enqueue)
    await subscriptions.subscribe_to_shared_source(
        session,
        user_id=uuid4(),
        identity=canonical_web_identity(source.canonical_url),
        display_name=None,
        folder_name=None,
        commit=False,
    )
    assert subscription.is_enabled
    if probe_status == "not_found":
        assert state.next_scan_at is None and state.phase == SyncPhase.DEGRADED
        enqueue.assert_awaited_once_with(session, source=source, state=state, reason="author")
    else:
        enqueue.assert_not_awaited()
        if probe_status != "found":
            assert state.next_scan_at is not None
            assert state.last_error_code == "web_feed_discovering"
        elif not was_enabled:
            assert state.next_scan_at is not None
    session.commit.assert_not_awaited()


async def test_resubscribing_during_feed_retry_preserves_fetch_error(monkeypatch):
    from types import SimpleNamespace

    from app.domain.enums import SourceKind, SyncPhase
    from app.ingestion.source_identity import canonical_web_identity
    from app.services import subscriptions

    url = "https://example.com/blog"
    source = SimpleNamespace(
        id=uuid4(),
        config={"web_feed_discovery": {"version": 1, "source_url": url, "status": "retry"}},
        kind=SourceKind.WEB,
        canonical_url=url,
    )
    state = SimpleNamespace(
        next_scan_at=None,
        phase=SyncPhase.DEGRADED,
        last_error_code="network_error",
        last_error_message="Temporary network failure.",
    )
    subscription = SimpleNamespace(id=uuid4(), is_enabled=True)
    session = AsyncMock()
    session.get.return_value = state
    monkeypatch.setattr(subscriptions, "ensure_profile", AsyncMock())
    monkeypatch.setattr(subscriptions, "_find_source", AsyncMock(return_value=source))
    monkeypatch.setattr(subscriptions, "_find_subscription", AsyncMock(return_value=subscription))
    enqueue = AsyncMock()
    monkeypatch.setattr(subscriptions, "ensure_web_rule_job", enqueue)
    await subscriptions.subscribe_to_shared_source(
        session,
        user_id=uuid4(),
        identity=canonical_web_identity(url),
        display_name=None,
        folder_name=None,
        commit=False,
    )
    assert state.next_scan_at is not None
    assert state.phase == SyncPhase.DEGRADED
    assert state.last_error_code == "network_error"
    enqueue.assert_not_awaited()
