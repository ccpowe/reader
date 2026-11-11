"""Subscription routes end to end against a real database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from reader.ingestion.feed_discovery import FeedDiscoveryResult
from reader.ingestion.url_safety import UnresolvableHost
from reader.models import Article, Source
from reader.subscriptions import service
from reader.subscriptions.icons import Icon
from tests.reader.pg import api_client, postgres_dsn

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No real DNS, feed discovery or YouTube lookups."""

    async def public(url):
        if "nodns" in url:
            raise UnresolvableHost("Source hostname could not be resolved.")
        return url

    async def discover(url, _client, **_kwargs):
        if "nofeed" in url:
            return FeedDiscoveryResult("not_found", None, {})
        if "down" in url:
            return FeedDiscoveryResult("retry", None, {})
        return FeedDiscoveryResult("found", url.rstrip("/") + "/feed.xml", {})

    async def channel(value, _client):
        return "UC" + "a" * 22

    monkeypatch.setattr(service, "validate_public_http_url", public)
    monkeypatch.setattr(service, "discover_web_feed", discover)
    monkeypatch.setattr(service, "resolve_youtube_channel_id", channel)


async def _signed_in(client) -> dict:
    tokens = (
        await client.post(
            "/auth/register", json={"email": "me@example.com", "password": "long password 1"}
        )
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _code(response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_each_type_resolves_to_its_source(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        cases = {
            "rss": "https://e.com/feed.xml",
            "website": "https://blog.example.com/",
            "youtube": "https://www.youtube.com/@someone",
            "x": "@Writer",
            "reddit": "r/Python",
        }
        created = {
            kind: (
                await client.post(
                    "/subscriptions", json={"type": kind, "value": value}, headers=auth
                )
            ).json()
            for kind, value in cases.items()
        }

    assert created["rss"]["type"] == "rss" and created["rss"]["status"] == "pending"
    # A website becomes an RSS subscription to its discovered feed.
    assert (created["website"]["type"], created["website"]["url"]) == (
        "rss",
        "https://blog.example.com/feed.xml",
    )
    assert created["youtube"]["url"].endswith("channel_id=UC" + "a" * 22)
    assert (created["x"]["type"], created["x"]["name"]) == ("x", "@Writer")
    assert (created["reddit"]["type"], created["reddit"]["name"]) == ("reddit", "r/Python")
    assert all(item["new_count"] == 0 for item in created.values())


@pytest.mark.asyncio
async def test_bad_input_and_feedless_websites_are_rejected(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)

        async def post(kind, value):
            return await client.post(
                "/subscriptions", json={"type": kind, "value": value}, headers=auth
            )

        no_feed = await post("website", "https://nofeed.example.com/")
        down = await post("website", "https://down.example.com/")
        no_dns = await post("rss", "https://nodns.example.com/feed")
        bad_x = await post("x", "not a handle!")
        bad_reddit = await post("reddit", "r/x")
        bad_type = await post("web", "https://e.com/")

    assert _code(no_feed) == (422, "feed_not_found")
    assert _code(down) == (502, "source_unreachable")
    assert _code(no_dns) == (502, "source_unreachable")
    assert _code(bad_x) == (422, "invalid_x")
    assert _code(bad_reddit) == (422, "invalid_reddit")
    assert _code(bad_type) == (422, "invalid_request")


@pytest.mark.asyncio
async def test_following_twice_returns_the_same_subscription(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        body = {"type": "rss", "value": "https://e.com/feed.xml", "group": "News"}
        first = (await client.post("/subscriptions", json=body, headers=auth)).json()
        second = (await client.post("/subscriptions", json=body, headers=auth)).json()
        listed = (await client.get("/subscriptions", headers=auth)).json()

    assert first["id"] == second["id"] and first["group"] == "News"
    assert len(listed) == 1


@pytest.mark.asyncio
async def test_badge_counts_new_items_and_viewed_clears_it(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        sub = (
            await client.post(
                "/subscriptions", json={"type": "rss", "value": "https://e.com/f"}, headers=auth
            )
        ).json()
        async with client.app.state.session_factory() as db:
            source = await db.get(Source, sub["source_id"])
            for sequence in (1, 2, 3):
                db.add(
                    Article(
                        source_id=source.id,
                        native_id=str(sequence),
                        url=f"u{sequence}",
                        url_hash=str(sequence),
                        kind="article",
                        title="t",
                        content_hash="h",
                        sequence=sequence,
                    )
                )
            source.latest_sequence = 3
            await db.commit()
        before = (await client.get("/subscriptions", headers=auth)).json()[0]
        partly = await client.post(
            f"/subscriptions/{sub['id']}/viewed", json={"sequence": 2}, headers=auth
        )
        backwards = await client.post(
            f"/subscriptions/{sub['id']}/viewed", json={"sequence": 1}, headers=auth
        )
        beyond = await client.post(
            f"/subscriptions/{sub['id']}/viewed", json={"sequence": 99}, headers=auth
        )

    assert (before["new_count"], before["latest_sequence"]) == (3, 3)
    assert partly.json()["new_count"] == 1
    assert backwards.json()["new_count"] == 1  # "seen up to" never moves back
    assert beyond.json()["new_count"] == 0


@pytest.mark.asyncio
async def test_update_and_unsubscribe(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        sub = (
            await client.post(
                "/subscriptions",
                json={"type": "rss", "value": "https://e.com/f", "group": "A", "name": "Mine"},
                headers=auth,
            )
        ).json()
        renamed = await client.patch(
            f"/subscriptions/{sub['id']}", json={"include_in_home": False}, headers=auth
        )
        cleared = await client.patch(
            f"/subscriptions/{sub['id']}", json={"group": None, "name": "  "}, headers=auth
        )
        deleted = await client.delete(f"/subscriptions/{sub['id']}", headers=auth)
        missing = await client.patch(f"/subscriptions/{sub['id']}", json={}, headers=auth)
        async with client.app.state.session_factory() as db:
            source_kept = await db.get(Source, sub["source_id"])

    assert (renamed.json()["group"], renamed.json()["name"]) == ("A", "Mine")
    assert renamed.json()["include_in_home"] is False
    assert cleared.json()["group"] is None and cleared.json()["name"] is None
    assert deleted.status_code == 204
    assert _code(missing) == (404, "subscription_not_found")
    assert source_kept is not None  # unfollowed sources are kept


@pytest.mark.asyncio
async def test_other_users_cannot_touch_my_subscription(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        mine = await _signed_in(client)
        sub = (
            await client.post(
                "/subscriptions", json={"type": "rss", "value": "https://e.com/f"}, headers=mine
            )
        ).json()
        other_tokens = (
            await client.post(
                "/auth/register",
                json={"email": "other@example.com", "password": "long password 1"},
            )
        ).json()
        theirs = {"Authorization": f"Bearer {other_tokens['access_token']}"}
        patch = await client.patch(f"/subscriptions/{sub['id']}", json={}, headers=theirs)
        delete = await client.delete(f"/subscriptions/{sub['id']}", headers=theirs)
        icon = await client.get(f"/sources/{sub['source_id']}/icon", headers=theirs)

    assert _code(patch) == (404, "subscription_not_found")
    assert _code(delete) == (404, "subscription_not_found")
    assert _code(icon) == (404, "icon_not_found")


@pytest.mark.asyncio
async def test_icon_is_relayed_for_followed_sources(monkeypatch) -> None:
    async def fake_fetch(source_id, url):
        assert url == "https://e.com/icon.png"
        return Icon(b"\x89PNG...", "image/png")

    monkeypatch.setattr("reader.subscriptions.api.fetch_icon", fake_fetch)
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        sub = (
            await client.post(
                "/subscriptions", json={"type": "rss", "value": "https://e.com/f"}, headers=auth
            )
        ).json()
        no_icon = await client.get(f"/sources/{sub['source_id']}/icon", headers=auth)
        async with client.app.state.session_factory() as db:
            await db.execute(update(Source).values(icon_url="https://e.com/icon.png"))
            await db.commit()
        listed = (await client.get("/subscriptions", headers=auth)).json()[0]
        icon = await client.get(f"/sources/{sub['source_id']}/icon", headers=auth)
        cached = await client.get(
            f"/sources/{sub['source_id']}/icon",
            headers={**auth, "If-None-Match": icon.headers["etag"]},
        )

    assert _code(no_icon) == (404, "icon_not_found")
    assert listed["icon_url"] == f"http://reader.test/sources/{sub['source_id']}/icon"
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"
    assert cached.status_code == 304


@pytest.mark.asyncio
async def test_following_an_unfollowed_source_schedules_a_scan_now(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth = await _signed_in(client)
        body = {"type": "rss", "value": "https://e.com/f"}
        sub = (await client.post("/subscriptions", json=body, headers=auth)).json()
        await client.delete(f"/subscriptions/{sub['id']}", headers=auth)
        async with client.app.state.session_factory() as db:
            await db.execute(
                update(Source).values(next_scan_at=datetime.now(UTC) + timedelta(days=3))
            )
            await db.commit()
        again = (await client.post("/subscriptions", json=body, headers=auth)).json()
        async with client.app.state.session_factory() as db:
            source = await db.scalar(select(Source))

    assert again["source_id"] == sub["source_id"]
    assert source.next_scan_at <= datetime.now(UTC)
