"""Behavior of local MCP ranking pages, independent of provider refreshes."""

from copy import deepcopy
from datetime import UTC, datetime

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
        response = {
            "kind": path.rsplit("/", 1)[-1],
            "title": "Original board",
            "subtitle": "At collection time",
            "fetched_at": "2026-10-17T08:00:00Z",
            "items": deepcopy(self.items[: params["limit"]]),
            "effective_engine_fingerprint": "must-not-leak",
        }
        if path == "/v1/rankings/product_hunt":
            response.update(
                date=params["date"],
                timezone="America/Los_Angeles",
                order="RANKING",
                truncated=len(self.items) > params["limit"],
                subtitle="Historical launch records queried at fetched_at; not final ranks.",
            )
        if path == "/v1/rankings/github":
            response["period"] = params["period"]
        return response


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
async def test_github_period_defaults_weekly_and_binds_frozen_cursor() -> None:
    client = RankingClient(_items(21))
    pager = rankings.RankingPages()
    first = await pager.list_ranking_snapshot(client, "github")
    client.items = _items(21)[::-1]
    second = await pager.list_ranking_snapshot(
        client, "github", first["next_cursor"], period="weekly"
    )
    third = await pager.list_ranking_snapshot(client, "github", second["next_cursor"])

    assert [len(page["items"]) for page in (first, second, third)] == [10, 10, 1]
    assert [page["period"] for page in (first, second, third)] == ["weekly"] * 3
    assert second["snapshot_id"] == first["snapshot_id"] == third["snapshot_id"]
    assert second["items"][0]["rank"] == 22
    assert third["next_cursor"] is None
    assert client.calls[0] == ("GET", "/v1/rankings/github", {"limit": 100, "period": "weekly"})
    assert [call[1] for call in client.calls[1:]] == ["/v1/auth/user"] * 2


@pytest.mark.asyncio
async def test_github_period_scope_and_other_kind_validation() -> None:
    client = RankingClient(_items(11))
    pager = rankings.RankingPages()
    daily = await pager.list_ranking_snapshot(client, "github", period="daily")
    assert daily["period"] == "daily"
    assert client.calls[0][2] == {"limit": 100, "period": "daily"}
    for substituted in (None, "weekly", "monthly"):
        with pytest.raises(ValueError, match="board"):
            await pager.list_ranking_snapshot(
                client, "github", daily["next_cursor"], period=substituted
            )
    for other in ("hacker_news", "techtwitter", "product_hunt"):
        with pytest.raises(ValueError, match="only supported"):
            await pager.list_ranking_snapshot(client, other, period="daily")
    for invalid in ("yearly", "", 1):
        with pytest.raises(ValueError, match="GitHub period"):
            await pager.list_ranking_snapshot(client, "github", period=invalid)
    assert len(client.calls) == 1
    monthly = await pager.list_ranking_snapshot(client, "github", period="monthly")
    assert monthly["period"] == "monthly"
    assert client.calls[1][2] == {"limit": 100, "period": "monthly"}


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
        await pager.list_ranking_snapshot(client, "hacker_news")
    client.items = []
    assert (await pager.list_ranking_snapshot(client, "hacker_news"))["items"] == []


@pytest.mark.asyncio
async def test_revoked_account_cannot_continue_an_ordinary_cached_board() -> None:
    client = RankingClient(_items(11))
    pager = rankings.RankingPages()
    first = await pager.list_ranking_snapshot(client, "github")
    client.allowed = False
    with pytest.raises(PermissionError):
        await pager.list_ranking_snapshot(client, "github", first["next_cursor"])


@pytest.mark.asyncio
async def test_product_hunt_preserves_date_provenance_items_and_metadata_across_pages() -> None:
    products = _items(23)
    for index, product in enumerate(products, 1):
        product.update(
            product_id=str(index),
            website_url=f"https://product.example/{index}",
            image_urls=[f"https://images.example/{index}"],
            comments=index * 3,
        )
    products[1]["product_id"] = None
    products[1]["website_url"] = None
    client = RankingClient(products)
    pager = rankings.RankingPages()
    first = await pager.list_ranking_snapshot(client, "product_hunt", date="2026-10-16")
    first["items"][0]["image_urls"].append("mutated")
    first["items"][0]["title"] = "mutated"
    client.items = products[::-1]
    second = await pager.list_ranking_snapshot(
        client, "product_hunt", first["next_cursor"], date="2026-10-16"
    )
    third = await pager.list_ranking_snapshot(client, "product_hunt", second["next_cursor"])
    original = await pager.list_ranking_snapshot(client, "product_hunt", date="2026-10-16")

    assert [len(page["items"]) for page in (first, second, third)] == [10, 10, 3]
    assert [item["rank"] for item in second["items"]] == list(range(22, 42, 2))
    assert second["items"][0] == {
        "rank": 22,
        "title": "Original 11",
        "url": "https://example.org/11",
        "description": "Body 11",
        "score": 11,
        "comments": 33,
        "image_urls": ["https://images.example/11"],
        "product_id": "11",
        "website_url": "https://product.example/11",
    }
    assert original["items"][0]["image_urls"] == ["https://images.example/23"]
    assert first["items"][1]["product_id"] is None
    assert first["items"][1]["website_url"] is None
    assert "translated_title" not in second["items"][0]
    for page in (first, second, third):
        assert {
            key: page[key]
            for key in ("date", "timezone", "order", "truncated", "fetched_at", "subtitle")
        } == {
            "date": "2026-10-16",
            "timezone": "America/Los_Angeles",
            "order": "RANKING",
            "truncated": False,
            "fetched_at": "2026-10-17T08:00:00Z",
            "subtitle": "Historical launch records queried at fetched_at; not final ranks.",
        }
    assert third["next_cursor"] is None
    assert client.calls[0] == (
        "GET",
        "/v1/rankings/product_hunt",
        {"limit": 100, "date": "2026-10-16"},
    )
    assert [call[1] for call in client.calls[1:3]] == ["/v1/auth/user"] * 2


@pytest.mark.asyncio
async def test_product_hunt_omitted_date_stays_bound_across_la_midnight(monkeypatch) -> None:
    now = [datetime(2026, 10, 22, 6, 30, tzinfo=UTC)]
    monkeypatch.setattr(
        rankings,
        "datetime",
        type("Clock", (), {"now": staticmethod(lambda tz: now[0].astimezone(tz))}),
    )
    client = RankingClient(_items(11))
    pager = rankings.RankingPages()
    first = await pager.list_ranking_snapshot(client, "product_hunt")
    assert first["date"] == "2026-10-21"
    now[0] = datetime(2026, 10, 22, 7, 30, tzinfo=UTC)
    second = await pager.list_ranking_snapshot(client, "product_hunt", first["next_cursor"])
    fresh = await pager.list_ranking_snapshot(client, "product_hunt")
    assert second["date"] == "2026-10-21"
    assert second["snapshot_id"] == first["snapshot_id"]
    assert fresh["date"] == "2026-10-22"
    assert client.calls[0][2] == {"limit": 100, "date": "2026-10-21"}
    assert client.calls[2][2] == {"limit": 100, "date": "2026-10-22"}


@pytest.mark.asyncio
async def test_product_hunt_date_validation_and_cursor_scope(monkeypatch) -> None:
    now = datetime(2026, 10, 22, 6, 30, tzinfo=UTC)
    monkeypatch.setattr(
        rankings,
        "datetime",
        type("Clock", (), {"now": staticmethod(lambda tz: now.astimezone(tz))}),
    )
    client = RankingClient(_items(11))
    pager = rankings.RankingPages()
    for invalid in ("2026-10-22", "2026-02-30", "20261021", "2026-1-01"):
        with pytest.raises(ValueError, match="date|future"):
            await pager.list_ranking_snapshot(client, "product_hunt", date=invalid)
    with pytest.raises(ValueError, match="only supported"):
        await pager.list_ranking_snapshot(client, "github", date="2026-10-20")
    assert client.calls == []
    first = await pager.list_ranking_snapshot(client, "product_hunt", date="2026-10-20")
    with pytest.raises(ValueError, match="board"):
        await pager.list_ranking_snapshot(
            client, "product_hunt", first["next_cursor"], date="2026-10-19"
        )
    with pytest.raises(ValueError, match="board"):
        await pager.list_ranking_snapshot(client, "github", first["next_cursor"])
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_product_hunt_limit_marks_truncated_board() -> None:
    client = RankingClient(_items(101))
    pager = rankings.RankingPages()
    page = await pager.list_ranking_snapshot(client, "product_hunt", date="2026-10-16")
    assert page["truncated"] is True
    for _ in range(9):
        page = await pager.list_ranking_snapshot(
            client, "product_hunt", page["next_cursor"], date="2026-10-16"
        )
        assert len(page["items"]) == 10
        assert page["truncated"] is True
    assert page["items"][-1]["rank"] == 200
    assert page["next_cursor"] is None
