import httpx
import pytest

from app.ingestion.models import SourceScanError, SourceScanRequest
from app.ingestion.sources.scweet_x import ScweetXSourceAdapter, ScweetXSourceConfig


@pytest.mark.asyncio
async def test_scweet_adapter_uses_private_pagination_protocol_and_normalises_tweets() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "tweet_id": "123",
                        "tweet_url": "https://x.com/OpenAI/status/123",
                        "text": "Hello from local Scweet",
                        "timestamp": "2026-07-28T10:00:00Z",
                        "user": {
                            "screen_name": "OpenAI",
                            "profile_image_url": "https://pbs.twimg.com/profile_images/openai.jpg",
                        },
                        "likes": 4,
                        "media": {"image_links": ["https://pbs.twimg.com/image.jpg"]},
                    }
                ],
                "continuation": "opaque-next",
                "completed": False,
                "limit_reached": "page_size",
                "boundary_ids": ["123"],
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await ScweetXSourceAdapter(
            ScweetXSourceConfig(
                handle="OpenAI",
                service_url="http://scweet:8090",
                service_token="internal-token",
            ),
            client,
        ).scan_page(SourceScanRequest())

    assert captured["authorization"] == "Bearer internal-token"
    assert '"usernames":["OpenAI"]' in str(captured["body"])
    assert page.items[0].native_id == "123"
    assert page.items[0].author_name == "@OpenAI"
    assert page.items[0].media[0].original_url == "https://pbs.twimg.com/image.jpg"
    assert page.next_continuation == {
        "provider": "scweet",
        "cursor": "opaque-next",
        "boundary_ids": ["123"],
    }


@pytest.mark.asyncio
async def test_scweet_adapter_surfaces_structured_collector_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={"detail": {"code": "auth_failed", "message": "cookies expired"}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ScweetXSourceAdapter(
            ScweetXSourceConfig(
                handle="OpenAI",
                service_url="http://scweet:8090",
                service_token="token",
            ),
            client,
        )
        with pytest.raises(SourceScanError, match="cookies expired") as error:
            await adapter.scan_page(SourceScanRequest())

    assert error.value.code == "auth_failed"
    assert error.value.long_lived is True


@pytest.mark.asyncio
async def test_scweet_adapter_uses_profile_lookup_for_empty_timeline() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tweets"):
            return httpx.Response(
                200,
                json={"items": [], "continuation": None, "completed": True},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "items": [{"profile_image_url": "https://pbs.twimg.com/profile_images/codex.jpg"}]
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await ScweetXSourceAdapter(
            ScweetXSourceConfig(
                handle="CodexReleases",
                service_url="http://scweet:8090",
                service_token="token",
            ),
            client,
        ).scan_page(SourceScanRequest(initial=True))

    assert page.items == ()
    assert page.source_avatar_url == "https://pbs.twimg.com/profile_images/codex.jpg"


@pytest.mark.asyncio
async def test_scweet_adapter_does_not_stop_at_a_reordered_or_pinned_first_anchor() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "tweet_id": native_id,
                        "tweet_url": f"https://x.com/OpenAI/status/{native_id}",
                        "text": native_id,
                        "is_pinned": native_id == "pinned",
                        "user": {
                            "screen_name": "OpenAI",
                            "profile_image_url": "https://pbs.twimg.com/profile.jpg",
                        },
                    }
                    for native_id in ("pinned", "new", "known", "oldest-anchor", "tail")
                ],
                "continuation": "older-page",
                "completed": False,
                "boundary_ids": ["pinned", "new", "known", "oldest-anchor", "tail"],
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await ScweetXSourceAdapter(
            ScweetXSourceConfig(
                handle="OpenAI",
                service_url="http://scweet:8090",
                service_token="token",
            ),
            client,
        ).scan_page(
            SourceScanRequest(checkpoint={"head_ids": ["pinned", "known", "oldest-anchor"]})
        )

    assert [item.native_id for item in page.items] == [
        "pinned",
        "new",
        "known",
        "oldest-anchor",
    ]
    assert page.completed is True
    assert page.next_continuation is None
    assert set(page.checkpoint["item_hashes"]) == {item.native_id for item in page.items}


@pytest.mark.asyncio
@pytest.mark.parametrize("pin_flag", [True, None])
async def test_pinned_only_overlap_keeps_new_page_and_continues(pin_flag) -> None:
    first_ids = ["pinned", *[f"new-{index}" for index in range(49)]]
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        ids = first_ids if len(requests) == 1 else ["new-older", "old-regular", "tail"]
        items = [
            {
                "tweet_id": value,
                "tweet_url": f"https://x.com/writer/status/{value}",
                "text": value,
                "user": {"profile_image_url": "https://example.com/avatar.png"},
            }
            for value in ids
        ]
        if pin_flag is not None:
            items[0]["is_pinned"] = pin_flag and len(requests) == 1
        return httpx.Response(
            200,
            request=request,
            json={"items": items, "continuation": "next", "completed": False},
        )

    checkpoint = {"head_ids": ["pinned", "old-regular"]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ScweetXSourceAdapter(ScweetXSourceConfig("writer", "http://scweet", "t"), client)
        first = await adapter.scan_page(SourceScanRequest(checkpoint=checkpoint))
        assert [item.native_id for item in first.items] == first_ids
        assert first.completed is False
        assert first.next_continuation is not None
        assert set(first.checkpoint["item_hashes"]) == set(first_ids)
        second = await adapter.scan_page(
            SourceScanRequest(checkpoint=checkpoint, continuation=first.next_continuation)
        )
    assert [item.native_id for item in second.items] == ["new-older", "old-regular"]
    assert second.completed is True
    assert set(second.checkpoint["item_hashes"]) == {"new-older", "old-regular"}


def test_x_engagement_changes_do_not_change_material_hash() -> None:
    adapter = ScweetXSourceAdapter.__new__(ScweetXSourceAdapter)
    adapter._config = ScweetXSourceConfig("OpenAI", "http://scweet", "token")
    first = adapter._to_content(
        {
            "tweet_id": "1",
            "tweet_url": "https://x.com/OpenAI/status/1",
            "text": "Stable text",
            "likes": 1,
            "quoted_tweet": {"id": "quoted", "text": "Quote", "views": 4},
        }
    )
    second = adapter._to_content(
        {
            "tweet_id": "1",
            "tweet_url": "https://x.com/OpenAI/status/1",
            "text": "Stable text",
            "likes": 999,
            "quoted_tweet": {"id": "quoted", "text": "Quote", "views": 9000},
        }
    )

    assert first is not None and second is not None
    assert first.material_hash() == second.material_hash()
