"""Feed, summary, article and saved routes end to end against a real database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from reader.models import Article, SavedArticle, Source, Subscription
from tests.reader.pg import api_client, postgres_dsn

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 11, 1, tzinfo=UTC)


async def _sign_up(client, email: str = "me@example.com") -> tuple[dict, UUID]:
    tokens = (
        await client.post("/auth/register", json={"email": email, "password": "long password 1"})
    ).json()
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = (await client.get("/me", headers=auth)).json()
    return auth, UUID(me["id"])


async def _follow(client, user_id: UUID, kind: str = "rss", **subscription) -> Subscription:
    async with client.app.state.session_factory() as db:
        source = Source(
            kind=kind, canonical_key=f"{kind}:{uuid4()}", url="https://e.com/f", display_name="S"
        )
        db.add(source)
        await db.flush()
        row = Subscription(user_id=user_id, source_id=source.id, **subscription)
        db.add(row)
        await db.commit()
        return row


def _article(source_id: UUID, n: int, *, sequence: int | None = None, **fields) -> Article:
    return Article(
        source_id=source_id,
        native_id=str(n),
        url=f"https://e.com/{n}",
        url_hash=f"{source_id}-{n}",
        kind=fields.pop("kind", "article"),
        title=fields.pop("title", f"Item {n}"),
        content_hash="h",
        first_seen_at=T0 + timedelta(minutes=n),
        sequence=sequence,
        **fields,
    )


async def _add(client, *articles: Article) -> list[UUID]:
    async with client.app.state.session_factory() as db:
        db.add_all(articles)
        await db.commit()
        return [article.id for article in articles]


def _titles(response) -> list[str]:
    return [item["title"] for item in response.json()["items"]]


def _code(response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_empty_group_means_without_a_group(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth, me = await _sign_up(client)
        grouped = await _follow(client, me, group_name="News")
        loose = await _follow(client, me)
        await _add(
            client,
            _article(grouped.source_id, 1, sequence=1),
            _article(loose.source_id, 2, sequence=1),
        )
        ungrouped = await client.get("/feed", params={"group": ""}, headers=auth)
        window = {"since": (T0 - timedelta(days=1)).isoformat()}
        summary = await client.get("/feed/summary", params={**window, "group": ""}, headers=auth)

    assert _titles(ungrouped) == ["Item 2"]
    assert [s["subscription_id"] for s in summary.json()["subscriptions"]] == [str(loose.id)]


@pytest.mark.asyncio
async def test_feed_scopes_and_pages(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth, me = await _sign_up(client)
        home = await _follow(client, me, group_name="News")
        hidden = await _follow(client, me, include_in_home=False, group_name="News")
        off = await _follow(client, me, is_enabled=False)
        await _add(
            client,
            *(_article(home.source_id, n, sequence=n) for n in (1, 2, 3)),
            _article(hidden.source_id, 4),
            _article(off.source_id, 5),
        )
        first = await client.get("/feed", params={"limit": 2}, headers=auth)
        second = await client.get(
            "/feed", params={"limit": 2, "cursor": first.json()["next_cursor"]}, headers=auth
        )
        group = await client.get("/feed", params={"group": "News"}, headers=auth)
        everything = await client.get("/feed", params={"scope": "all"}, headers=auth)
        only = await client.get("/feed", params={"subscription": str(hidden.id)}, headers=auth)
        disabled = await client.get("/feed", params={"subscription": str(off.id)}, headers=auth)

        other_auth, _ = await _sign_up(client, "other@example.com")
        foreign = await client.get(
            "/feed", params={"subscription": str(home.id)}, headers=other_auth
        )
        empty = await client.get("/feed", headers=other_auth)
        bad_cursor = await client.get("/feed", params={"cursor": "nope"}, headers=auth)

    assert _titles(first) == ["Item 3", "Item 2"]
    assert _titles(second) == ["Item 1"] and second.json()["next_cursor"] is None
    item = first.json()["items"][0]
    assert item["source"]["subscription_id"] == str(home.id) and item["sequence"] == 3
    assert item["x"] is None and item["is_saved"] is False
    assert _titles(group) == ["Item 4", "Item 3", "Item 2", "Item 1"]
    assert _titles(everything) == ["Item 4", "Item 3", "Item 2", "Item 1"]  # not the disabled one
    assert item["source"]["group"] == "News"
    assert _titles(only) == ["Item 4"]
    assert _titles(disabled) == ["Item 5"]  # asked for by name, so shown
    assert _code(foreign) == (404, "subscription_not_found")
    assert empty.json() == {"items": [], "next_cursor": None}
    assert _code(bad_cursor) == (422, "invalid_cursor")


@pytest.mark.asyncio
async def test_a_time_window_returns_updates_only(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth, me = await _sign_up(client)
        sub = await _follow(client, me, group_name="AI")
        other = await _follow(client, me)
        await _add(
            client,
            _article(sub.source_id, 1),  # first-scan baseline
            _article(sub.source_id, 2, sequence=1),
            _article(sub.source_id, 3, sequence=2),
            _article(other.source_id, 4, sequence=1),
            _article(sub.source_id, 9, sequence=3),
        )
        window = {"since": (T0).isoformat(), "until": (T0 + timedelta(minutes=5)).isoformat()}
        feed = await client.get("/feed", params=window, headers=auth)
        summary = await client.get("/feed/summary", params=window, headers=auth)
        grouped = await client.get("/feed/summary", params={**window, "group": "AI"}, headers=auth)
        naive = await client.get(
            "/feed/summary", params={"since": "2026-11-01T00:00:00"}, headers=auth
        )

    assert _titles(feed) == ["Item 4", "Item 3", "Item 2"]
    body = summary.json()
    assert body["total"] == 3
    assert [(entry["subscription_id"], entry["count"]) for entry in body["subscriptions"]] == [
        (str(sub.id), 2),
        (str(other.id), 1),
    ]
    assert grouped.json()["total"] == 2
    assert _code(naive) == (422, "invalid_request")


def _post(source_id: UUID, n: int, *, root: int | None = None, **fields) -> Article:
    x = {"tweet_id": str(n), "author": {"id": "7", "handle": "w"}, "completeness": "parsed"}
    if root is not None:
        x["thread_root"] = str(root)
        x["reply_to"] = {"tweet_id": str(n - 1), "author_id": "7"}
    return _article(source_id, n, kind="post", extra={"x": x}, **fields)


@pytest.mark.asyncio
async def test_an_x_thread_is_one_card(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth, me = await _sign_up(client)
        sub = await _follow(client, me, kind="x")
        root_id, *_ = await _add(
            client,
            _post(sub.source_id, 1, sequence=1),
            _post(sub.source_id, 2, root=1, sequence=2),
            _post(sub.source_id, 3, root=1, sequence=3),
            _post(sub.source_id, 4, sequence=4),
        )
        feed = await client.get("/feed", headers=auth)
        # The root is outside this window, so its replies stand on their own.
        later = await client.get(
            "/feed", params={"since": (T0 + timedelta(minutes=2)).isoformat()}, headers=auth
        )
        detail = await client.get(f"/articles/{root_id}", headers=auth)

    cards = feed.json()["items"]
    assert [card["title"] for card in cards] == ["Item 4", "Item 1"]
    assert cards[1]["x"]["thread_count"] == 3 and cards[0]["x"]["thread_count"] == 1
    assert _titles(later) == ["Item 4", "Item 3", "Item 2"]
    assert [post["title"] for post in detail.json()["thread"]] == ["Item 2", "Item 3"]
    assert detail.json()["x"]["thread_count"] == 3


@pytest.mark.asyncio
async def test_who_can_open_an_article(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth, me = await _sign_up(client)
        sub = await _follow(client, me)
        kept, dropped = await _add(
            client,
            _article(sub.source_id, 1, body_html="<p>Hi</p>", summary="x" * 500),
            _article(sub.source_id, 2),
        )
        opened = await client.get(f"/articles/{kept}", headers=auth)
        listed = (await client.get("/feed", headers=auth)).json()["items"]
        assert (await client.put(f"/saved/{kept}", headers=auth)).status_code == 204
        await client.delete(f"/subscriptions/{sub.id}", headers=auth)
        saved_after_unfollow = await client.get(f"/articles/{kept}", headers=auth)
        unsaved_after_unfollow = await client.get(f"/articles/{dropped}", headers=auth)
        other_auth, _ = await _sign_up(client, "other@example.com")
        foreign = await client.get(f"/articles/{kept}", headers=other_auth)
        missing = await client.get(f"/articles/{uuid4()}", headers=auth)

    body = opened.json()
    assert body["body_html"] == "<p>Hi</p>" and len(body["summary"]) == 500
    assert len(listed[-1]["summary"]) <= 281  # shortened in the feed
    assert saved_after_unfollow.status_code == 200
    assert saved_after_unfollow.json()["is_saved"] is True
    assert saved_after_unfollow.json()["source"]["subscription_id"] is None
    assert _code(unsaved_after_unfollow) == (404, "article_not_found")
    assert _code(foreign) == (404, "article_not_found")
    assert _code(missing) == (404, "article_not_found")


@pytest.mark.asyncio
async def test_a_saved_thread_root_shows_only_saved_posts_once_unfollowed(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth, me = await _sign_up(client)
        sub = await _follow(client, me, kind="x")
        _, bob = await _sign_up(client, "bob@example.com")
        async with client.app.state.session_factory() as db:
            db.add(Subscription(user_id=bob, source_id=sub.source_id))
            await db.commit()
        root, saved_reply, _ = await _add(
            client,
            _post(sub.source_id, 1),
            _post(sub.source_id, 2, root=1),
            _post(sub.source_id, 3, root=1),  # collected for Bob, never saved by me
        )
        await client.put(f"/saved/{root}", headers=auth)
        await client.put(f"/saved/{saved_reply}", headers=auth)
        following = await client.get(f"/articles/{root}", headers=auth)
        await client.delete(f"/subscriptions/{sub.id}", headers=auth)
        saved_only = await client.get(f"/articles/{root}", headers=auth)

    assert [post["title"] for post in following.json()["thread"]] == ["Item 2", "Item 3"]
    assert following.json()["thread"][0]["is_saved"] is True
    assert [post["title"] for post in saved_only.json()["thread"]] == ["Item 2"]


@pytest.mark.asyncio
async def test_saving(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        auth, me = await _sign_up(client)
        sub = await _follow(client, me)
        ids = await _add(
            client,
            _article(sub.source_id, 1, title="Rust 100%"),
            _article(sub.source_id, 2, title="Python", summary="about rust"),
            _article(sub.source_id, 3, title="Go"),
        )
        for article_id in ids:
            assert (await client.put(f"/saved/{article_id}", headers=auth)).status_code == 204
        again = await client.put(f"/saved/{ids[0]}", headers=auth)
        first = await client.get("/saved", params={"limit": 2}, headers=auth)
        rest = await client.get(
            "/saved", params={"limit": 2, "cursor": first.json()["next_cursor"]}, headers=auth
        )
        search = await client.get("/saved", params={"q": "rust"}, headers=auth)
        literal = await client.get("/saved", params={"q": "0%"}, headers=auth)
        feed = (await client.get("/feed", headers=auth)).json()["items"]
        removed = await client.delete(f"/saved/{ids[2]}", headers=auth)
        removed_again = await client.delete(f"/saved/{ids[2]}", headers=auth)

        other_auth, _ = await _sign_up(client, "other@example.com")
        not_followed = await client.put(f"/saved/{ids[0]}", headers=other_auth)
        async with client.app.state.session_factory() as db:
            rows = await db.scalar(select(func.count()).select_from(SavedArticle))

    assert again.status_code == 204
    assert len(first.json()["items"]) == 2 and len(rest.json()["items"]) == 1
    assert {item["title"] for item in search.json()["items"]} == {"Rust 100%", "Python"}
    assert _titles(literal) == ["Rust 100%"]
    assert all(item["is_saved"] for item in feed)
    assert removed.status_code == removed_again.status_code == 204
    assert _code(not_followed) == (404, "article_not_found")
    assert rows == 2
