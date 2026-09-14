import httpx
import pytest

from app.ingestion.models import SourceScanError, SourceScanRequest
from app.ingestion.sources.apify_x import ApifyXSourceAdapter, ApifyXSourceConfig


@pytest.mark.asyncio
async def test_apify_compatibility_adapter_uses_server_safety_limit() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json=[
                {
                    "type": "tweet",
                    "tweet_id": "123",
                    "tweet_url": "https://x.com/OpenAI/status/123",
                    "text": "Hello from X",
                    "username": "OpenAI",
                    "date": "Jul 27, 2026 · 10:00 AM UTC",
                }
            ],
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await ApifyXSourceAdapter(
            ApifyXSourceConfig(handle="OpenAI", token="test-token"), client
        ).scan_page(SourceScanRequest(max_raw_items=40))

    assert captured["authorization"] == "Bearer test-token"
    assert '"maxItems":40' in str(captured["body"])
    assert page.items[0].native_id == "123"
    assert page.items[0].author_name == "@OpenAI"


@pytest.mark.asyncio
async def test_apify_adapter_preserves_provider_error_detail() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "invalid input"}}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ApifyXSourceAdapter(ApifyXSourceConfig(handle="OpenAI", token="token"), client)
        with pytest.raises(SourceScanError, match="invalid input"):
            await adapter.scan_page(SourceScanRequest())


@pytest.mark.asyncio
async def test_apify_never_accepts_a_scweet_continuation() -> None:
    async with httpx.AsyncClient() as client:
        adapter = ApifyXSourceAdapter(ApifyXSourceConfig(handle="OpenAI", token="token"), client)
        with pytest.raises(SourceScanError, match="does not expose"):
            await adapter.scan_page(
                SourceScanRequest(continuation={"provider": "scweet", "cursor": "opaque"})
            )
