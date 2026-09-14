import httpx
import pytest

from app.api.sources import AddYouTubeSourceRequest
from app.ingestion.youtube import resolve_youtube_channel_id


@pytest.mark.asyncio
async def test_resolves_a_youtube_handle_url_from_its_canonical_browse_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_safe_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            text=('"browseId":"UCLKPca3kwwd-B59HNr-_lvA","canonicalBaseUrl":"/@aiDotEngineer"'),
            request=httpx.Request("GET", "https://www.youtube.com/@aiDotEngineer"),
        )

    monkeypatch.setattr("app.ingestion.youtube.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        result = await resolve_youtube_channel_id("https://www.youtube.com/@aiDotEngineer", client)

    assert result == "UCLKPca3kwwd-B59HNr-_lvA"


@pytest.mark.asyncio
async def test_accepts_a_youtube_channel_id_without_a_lookup() -> None:
    async with httpx.AsyncClient() as client:
        result = await resolve_youtube_channel_id("UCLKPca3kwwd-B59HNr-_lvA", client)

    assert result == "UCLKPca3kwwd-B59HNr-_lvA"


@pytest.mark.asyncio
async def test_resolves_a_bare_youtube_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_safe_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            text='"externalId":"UCLKPca3kwwd-B59HNr-_lvA"',
            request=httpx.Request("GET", "https://www.youtube.com/@aiDotEngineer"),
        )

    monkeypatch.setattr("app.ingestion.youtube.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        result = await resolve_youtube_channel_id("aiDotEngineer", client)

    assert result == "UCLKPca3kwwd-B59HNr-_lvA"


def test_youtube_request_accepts_a_handle_before_resolution() -> None:
    assert AddYouTubeSourceRequest(channel_id="@aiDotEngineer").channel_id == "@aiDotEngineer"
