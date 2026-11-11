"""/mcp end to end: tokens, every tool, and one user never seeing another's data."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from reader.accounts.service import create_mcp_token, revoke_mcp_token
from reader.boards import fetchers
from reader.boards.fetchers import BoardItem
from reader.ingestion.feed_discovery import FeedDiscoveryResult
from reader.models import Article, Source, Subscription
from reader.subscriptions import service as subscriptions
from tests.reader.pg import SERVER_TOKEN, api_client, postgres_dsn

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 11, 10, 8, tzinfo=UTC)
_ids = iter(range(1, 10_000))


async def _user(client, email: str) -> tuple[UUID, str]:
    tokens = (
        await client.post("/auth/register", json={"email": email, "password": "long password 1"})
    ).json()
    me = (
        await client.get("/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    ).json()
    async with client.app.state.session_factory() as db:
        token = await create_mcp_token(db, email)
    return UUID(me["id"]), token


async def _rpc(client, token: str | None, method: str, params: dict | None = None, **headers):
    sent = {"Accept": "application/json, text/event-stream", **headers}
    if token:
        sent["Authorization"] = f"Bearer {token}"
    return await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params or {}},
        headers=sent,
    )


async def _tool(client, token: str, name: str, **arguments) -> dict:
    response = await _rpc(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    if result.get("isError"):
        return {"error": result["content"][0]["text"]}
    if "structuredContent" in result:
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


async def _follow(client, user_id, name, *, kind="rss", **subscription) -> Subscription:
    async with client.app.state.session_factory() as db:
        source = Source(
            kind=kind, canonical_key=f"{kind}:{uuid4()}", url="https://e.com/f", display_name=name
        )
        db.add(source)
        await db.flush()
        row = Subscription(user_id=user_id, source_id=source.id, **subscription)
        db.add(row)
        await db.commit()
        return row


async def _article(client, source_id, n, url=None, **fields) -> UUID:
    async with client.app.state.session_factory() as db:
        article = Article(
            source_id=source_id,
            native_id=f"{n}-{uuid4()}",
            url=url or f"https://e.com/{n}",
            url_hash=str(uuid4()),
            kind="article",
            title=fields.pop("title", f"Item {n}"),
            content_hash="h",
            first_seen_at=T0 + timedelta(minutes=n),
            sequence=n,
            **fields,
        )
        db.add(article)
        await db.commit()
        return article.id


@pytest.mark.asyncio
async def test_tokens_guard_the_endpoint(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        async with client.app.state.mcp.running():
            _, token = await _user(client, "me@example.com")
            missing = await _rpc(client, None, "tools/list")
            wrong = await _rpc(client, "rmcp_wrong", "tools/list")
            no_gate = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": f"Bearer {token}", "X-Reader-Server-Token": "nope"},
            )
            listed = await _rpc(client, token, "tools/list")
            async with client.app.state.session_factory() as db:
                await revoke_mcp_token(db, "me@example.com")
            revoked = await _rpc(client, token, "tools/list")

    assert (missing.status_code, missing.json()["error"]["code"]) == (401, "invalid_mcp_token")
    assert wrong.status_code == 401 and revoked.status_code == 401
    assert no_gate.json()["error"]["code"] == "invalid_server_token"
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert names == {
        "get_subscription_updates_summary",
        "list_updates",
        "get_board",
        "get_item",
        "get_youtube_transcript",
        "add_subscription",
    }
    assert SERVER_TOKEN  # the client sends the deployment token by default


@pytest.mark.asyncio
async def test_failed_tokens_share_one_budget_with_the_api(monkeypatch) -> None:
    async with api_client(
        postgres_dsn(), monkeypatch, auth_rate_limit_requests_per_minute="10"
    ) as client:
        async with client.app.state.mcp.running():
            bad = {"Authorization": "Bearer not-a-token"}
            spent = [(await client.get("/me", headers=bad)).status_code for _ in range(10)]
            mcp = await _rpc(client, "rmcp_wrong", "tools/list")

    assert spent == [401] * 10
    assert (mcp.status_code, mcp.json()["error"]["code"]) == (429, "too_many_attempts")


@pytest.mark.asyncio
async def test_updates_cover_every_subscription_and_merge_links(monkeypatch) -> None:
    async with api_client(postgres_dsn(), monkeypatch) as client:
        async with client.app.state.mcp.running():
            me, token = await _user(client, "me@example.com")
            blog = await _follow(client, me, "Blog", group_name="AI")
            hidden = await _follow(client, me, "Aggregator", include_in_home=False)
            await _article(client, blog.source_id, 1, url="https://same/1")
            # The newer copy is the one kept when links merge.
            await _article(client, hidden.source_id, 2, url="https://same/1", summary="word " * 200)
            for n in range(3, 60):
                await _article(client, hidden.source_id, n)
            window = {"since": "2026-11-10T16:00:00+08:00", "until": "2026-11-10T10:00:00Z"}
            summary = await _tool(client, token, "get_subscription_updates_summary", **window)
            first = await _tool(client, token, "list_updates", **window)
            second = await _tool(
                client, token, "list_updates", **window, cursor=first["next_cursor"]
            )
            grouped = await _tool(client, token, "list_updates", **window, group="AI")
            naive = await _tool(client, token, "list_updates", since="2026-11-10T08:00:00")

            _, other = await _user(client, "other@example.com")
            theirs = await _tool(client, other, "list_updates", **window)

    assert summary["total"] == 59
    assert {row["name"]: row["count"] for row in summary["subscriptions"]} == {
        "Aggregator": 58,
        "Blog": 1,
    }
    items = first["items"] + second["items"]
    assert second["next_cursor"] is None
    same = next(item for item in items if item["url"] == "https://same/1")
    assert {origin["subscription"] for origin in same["from"]} == {"Blog", "Aggregator"}
    assert len(items) == 58  # 59 articles, two share a link
    assert same["summary"].startswith("word word") and len(same["summary"]) <= 161
    assert [item["title"] for item in grouped["items"]] == ["Item 1"]
    assert "invalid_time" in naive["error"]
    assert theirs["items"] == []


@pytest.mark.asyncio
async def test_get_item_and_get_board(monkeypatch) -> None:
    async def fake_hn(limit):
        return [
            BoardItem(
                rank=n,
                title=f"Story {n}",
                url=f"https://story/{n}",
                discussion_url=f"https://news.ycombinator.com/item?id={n}",
                score=n,
            )
            for n in range(1, limit + 1)
        ]

    monkeypatch.setattr(fetchers, "fetch_hacker_news", fake_hn)
    async with api_client(postgres_dsn(), monkeypatch) as client:
        async with client.app.state.mcp.running():
            me, token = await _user(client, "me@example.com")
            sub = await _follow(client, me, "Blog")
            article_id = await _article(
                client, sub.source_id, 1, body_html="<p>Hello <b>world</b></p>"
            )
            item = await _tool(client, token, "get_item", article_id=str(article_id))
            missing = await _tool(client, token, "get_item", article_id=str(uuid4()))
            board = await _tool(client, token, "get_board", board="hn")
            bad = await _tool(client, token, "get_board", board="github", variant="yearly")
            _, other = await _user(client, "other@example.com")
            foreign = await _tool(client, other, "get_item", article_id=str(article_id))

    assert item["text"] == "Hello world" and item["source"] == {"name": "Blog", "type": "rss"}
    assert "article_not_found:" in missing["error"]
    assert len(board["items"]) == 30 and board["items"][0]["discussion_url"].endswith("id=1")
    assert "stale" not in board and board["board"] == "hn"
    assert "invalid_variant:" in bad["error"]
    assert "article_not_found:" in foreign["error"]


@pytest.mark.asyncio
async def test_add_subscription(monkeypatch) -> None:
    async def public(url):
        return url

    async def discover(url, _client, **_kwargs):
        return FeedDiscoveryResult("found", url.rstrip("/") + "/feed.xml", {})

    monkeypatch.setattr(subscriptions, "validate_public_http_url", public)
    monkeypatch.setattr(subscriptions, "discover_web_feed", discover)
    async with api_client(postgres_dsn(), monkeypatch) as client:
        async with client.app.state.mcp.running():
            _, token = await _user(client, "me@example.com")
            added = await _tool(
                client,
                token,
                "add_subscription",
                type="website",
                value="https://blog.e.com/",
                group="AI",
            )
            again = await _tool(
                client, token, "add_subscription", type="website", value="https://blog.e.com/"
            )
            bad = await _tool(client, token, "add_subscription", type="x", value="not a handle!")

    assert (added["type"], added["url"], added["group"]) == (
        "rss",
        "https://blog.e.com/feed.xml",
        "AI",
    )
    assert again["subscription_id"] == added["subscription_id"]
    assert "invalid_x:" in bad["error"]


@pytest.mark.asyncio
async def test_a_real_mcp_client_can_connect_and_call_a_tool(monkeypatch) -> None:
    """The SDK's own client: initialize handshake, list tools, call one."""
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with api_client(postgres_dsn(), monkeypatch) as client:
        async with client.app.state.mcp.running():
            me, token = await _user(client, "me@example.com")
            sub = await _follow(client, me, "Blog")
            await _article(client, sub.source_id, 1)
            http = httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=client.app),
                headers={"X-Reader-Server-Token": SERVER_TOKEN, "Authorization": f"Bearer {token}"},
            )
            async with (
                http,
                streamable_http_client("http://reader.test/mcp", http_client=http) as (
                    read,
                    write,
                    *_,
                ),
            ):
                async with ClientSession(read, write) as session:
                    info = await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool(
                        "list_updates", {"since": "2026-11-10T00:00:00Z"}
                    )

    assert info.server_info.name == "Reader"
    assert len(tools.tools) == 6
    assert not result.is_error
    assert json.loads(result.content[0].text)["items"][0]["title"] == "Item 1"
