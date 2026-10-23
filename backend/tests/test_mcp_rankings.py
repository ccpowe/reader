"""Behavior of local MCP ranking pages, independent of provider refreshes."""

from copy import deepcopy

import pytest

from app.mcp import rankings


class RankingClient:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.calls: list[tuple[str, str, dict]] = []
        self.allowed = True

    async def request(self, method, path, *, params=None, json=None):
        self.calls.append((method, path, params))
        if not self.allowed:
            raise PermissionError("Subscription inactive")
        if path == "/v1/auth/user":
            return {"id": "current-user"}
        return {
            "kind": path.rsplit("/", 1)[-1],
            "title": "Original board",
            "subtitle": "At collection time",
            "fetched_at": "2026-10-17T08:00:00Z",
            "items": deepcopy(self.items[: params["limit"]]),
            "effective_engine_fingerprint": "must-not-leak",
        }


def _items(count: int) -> list[dict]:
    return [
        {
            "rank": index * 2,
            "title": f"Original {index}",
            "url": f"https://example.org/{index}",
            "description": f"Body {index}",
            "score": index,
            "translated_title": "not original",
            "translation_key": f"ranking:test:{index}",
        }
        for index in range(1, count + 1)
    ]


@pytest.mark.asyncio
async def test_frozen_pages_keep_provider_order_rank_metadata_and_originals() -> None:
    client = RankingClient(_items(23))
    pager = rankings.RankingPages()
    first = await pager.list_ranking_snapshot(client, "hacker_news")
    first["items"][0]["title"] = "mutated by caller"
    client.items = _items(23)[::-1]
    refreshed = await pager.list_ranking_snapshot(client, "hacker_news")
    second = await pager.list_ranking_snapshot(client, "hacker_news", first["next_cursor"])
    third = await pager.list_ranking_snapshot(client, "hacker_news", second["next_cursor"])

    assert [len(page["items"]) for page in (first, second, third)] == [10, 10, 3]
    assert [item["rank"] for item in second["items"]] == list(range(22, 42, 2))
    assert second["items"][0] == {
        "rank": 22,
        "title": "Original 11",
        "url": "https://example.org/11",
        "description": "Body 11",
        "score": 11,
    }
    assert {page["snapshot_id"] for page in (first, second, third)} == {first["snapshot_id"]}
    assert refreshed["snapshot_id"] != first["snapshot_id"]
    assert refreshed["items"][0]["rank"] == 46
    assert third["next_cursor"] is None
    assert first["fetched_at"] == second["fetched_at"] == "2026-10-17T08:00:00Z"


@pytest.mark.asyncio
async def test_empty_and_exact_page_boundary_have_no_cursor() -> None:
    pager = rankings.RankingPages()
    empty = await pager.list_ranking_snapshot(RankingClient([]), "github")
    exact = await pager.list_ranking_snapshot(RankingClient(_items(10)), "techtwitter")
    assert empty["items"] == [] and empty["next_cursor"] is None
    assert len(exact["items"]) == 10 and exact["next_cursor"] is None


@pytest.mark.asyncio
async def test_cursor_is_signed_and_bound_to_instance_and_board() -> None:
    client = RankingClient(_items(11))
    pager = rankings.RankingPages()
    first = await pager.list_ranking_snapshot(client, "github")
    cursor = first["next_cursor"]
    with pytest.raises(ValueError, match="board"):
        await pager.list_ranking_snapshot(client, "hacker_news", cursor)
    with pytest.raises(ValueError, match="Invalid ranking cursor"):
        tampered = cursor[:-1] + ("0" if cursor[-1] != "0" else "1")
        await pager.list_ranking_snapshot(client, "github", tampered)
    with pytest.raises(ValueError, match="Invalid ranking cursor"):
        await rankings.RankingPages().list_ranking_snapshot(client, "github", cursor)
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_expiry_and_capacity_eviction_do_not_switch_to_a_new_snapshot(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(rankings, "monotonic", lambda: now[0])
    client = RankingClient(_items(11))
    pager = rankings.RankingPages(ttl_seconds=5, max_snapshots=1)
    first = await pager.list_ranking_snapshot(client, "github")
    second = await pager.list_ranking_snapshot(client, "hacker_news")
    with pytest.raises(rankings.RankingCursorExpired, match="expired"):
        await pager.list_ranking_snapshot(client, "github", first["next_cursor"])
    now[0] = 106.0
    with pytest.raises(rankings.RankingCursorExpired, match="expired"):
        await pager.list_ranking_snapshot(client, "hacker_news", second["next_cursor"])
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_reddit_renormalizes_scope_and_rechecks_subscription_on_each_followup() -> None:
    client = RankingClient(_items(21))
    pager = rankings.RankingPages()
    first = await pager.list_reddit_ranking_snapshot(client, "r/MachineLearning", "top")
    client.items = _items(21)[::-1]
    second = await pager.list_reddit_ranking_snapshot(
        client, "machinelearning", "top", "week", first["next_cursor"]
    )
    assert [item["rank"] for item in second["items"]] == list(range(22, 42, 2))
    assert second["items"][0]["discussion_url"] == "https://example.org/11"
    assert [call[2]["limit"] for call in client.calls] == [100, 1]
    assert client.calls[0][2]["time_filter"] == "week"
    client.allowed = False
    with pytest.raises(PermissionError, match="inactive"):
        await pager.list_reddit_ranking_snapshot(
            client, "machinelearning", "top", "week", second["next_cursor"]
        )


@pytest.mark.asyncio
async def test_reddit_argument_substitution_is_rejected_before_access_check() -> None:
    client = RankingClient(_items(11))
    pager = rankings.RankingPages()
    first = await pager.list_reddit_ranking_snapshot(client, "Python")
    cursor = first["next_cursor"]
    for subreddit, sort, time_filter in (
        ("other", "hot", None),
        ("Python", "rising", None),
        ("Python", "top", "week"),
    ):
        with pytest.raises(ValueError, match="board"):
            await pager.list_reddit_ranking_snapshot(client, subreddit, sort, time_filter, cursor)
    for sort, time_filter in (("hot", "day"), ("top", "all"), ("latest", None)):
        with pytest.raises(ValueError):
            await pager.list_reddit_ranking_snapshot(client, "Python", sort, time_filter)
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_oversized_snapshot_is_not_cached() -> None:
    client = RankingClient(_items(11))
    pager = rankings.RankingPages(max_cached_bytes=16)
    with pytest.raises(ValueError, match="size limit"):
        await pager.list_ranking_snapshot(client, "github")
    client.items = []
    assert (await pager.list_ranking_snapshot(client, "github"))["items"] == []


@pytest.mark.asyncio
async def test_revoked_account_cannot_continue_an_ordinary_cached_board() -> None:
    client = RankingClient(_items(11))
    pager = rankings.RankingPages()
    first = await pager.list_ranking_snapshot(client, "github")
    client.allowed = False
    with pytest.raises(PermissionError):
        await pager.list_ranking_snapshot(client, "github", first["next_cursor"])
