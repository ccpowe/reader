import httpx
import pytest

from app.ingestion.models import SourceScanRequest
from app.ingestion.sources.youtube_data import YouTubeSourceAdapter, YouTubeSourceConfig


@pytest.mark.asyncio
async def test_youtube_data_api_initial_scan_uses_15_item_window_and_video_metadata() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/channels"):
            payload = {
                "items": [
                    {
                        "snippet": {
                            "title": "Example Channel",
                            "thumbnails": {"high": {"url": "https://img.example/avatar.jpg"}},
                        },
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
                    }
                ]
            }
        elif request.url.path.endswith("/playlistItems"):
            payload = {
                "items": [
                    {
                        "contentDetails": {"videoId": "video-1"},
                        "snippet": {"publishedAt": "2026-08-01T12:00:00Z"},
                    }
                ]
            }
        else:
            payload = {
                "items": [
                    {
                        "id": "video-1",
                        "snippet": {
                            "title": "A video",
                            "description": "Description",
                            "channelTitle": "Example Channel",
                            "publishedAt": "2026-08-01T12:00:00Z",
                            "liveBroadcastContent": "none",
                            "thumbnails": {"high": {"url": "https://img.example/video-1.jpg"}},
                        },
                        "contentDetails": {"duration": "PT5M"},
                        "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                    }
                ]
            }
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await YouTubeSourceAdapter(
            YouTubeSourceConfig(
                channel_id="UCXZCJLdBC09xxGZ6gcdrc6A",
                feed_url="https://youtube.com/feeds/videos.xml?channel_id=UCXZCJLdBC09xxGZ6gcdrc6A",
                api_key="key",
            ),
            client,
        ).scan_page(SourceScanRequest(initial=True))

    playlist_request = next(
        request for request in requests if request.url.path.endswith("playlistItems")
    )
    assert playlist_request.url.params["maxResults"] == "15"
    assert page.provider_mode == "youtube_data_api"
    assert page.items[0].native_id == "video-1"
    assert page.items[0].raw_metadata["duration"] == "PT5M"
    assert page.source_config_updates == {"uploads_playlist_id": "UU123"}
    assert page.checkpoint["data_api"]["head_ids"] == ["video-1"]


@pytest.mark.asyncio
async def test_youtube_atom_fallback_never_claims_data_api_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"><title>Channel</title><entry>
      <id>yt:video:abc</id><yt:videoId>abc</yt:videoId><title>Fallback video</title>
      <link rel="alternate" href="https://youtube.com/watch?v=abc"/></entry></feed>"""

    async def fake_safe_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            content=feed.encode(),
            request=httpx.Request("GET", "https://youtube.com/feeds/videos.xml"),
        )

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        page = await YouTubeSourceAdapter(
            YouTubeSourceConfig(
                channel_id="UCXZCJLdBC09xxGZ6gcdrc6A",
                feed_url="https://youtube.com/feeds/videos.xml",
                api_key=None,
            ),
            client,
        ).scan_page(SourceScanRequest(initial=True))

    assert page.provider_mode == "youtube_atom"
    assert page.authoritative is False
    assert page.items[0].native_id == "abc"
    assert "data_api" not in page.checkpoint
    assert page.checkpoint["atom"]["head_ids"] == ["abc"]


@pytest.mark.asyncio
async def test_initial_unavailable_video_still_establishes_a_playlist_anchor() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/playlistItems"):
            payload = {
                "items": [
                    {
                        "contentDetails": {"videoId": "private-video"},
                        "snippet": {"title": "Private video"},
                    }
                ]
            }
        else:
            payload = {"items": []}
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await YouTubeSourceAdapter(
            YouTubeSourceConfig(
                channel_id="UC123",
                feed_url="https://youtube.com/feeds/videos.xml?channel_id=UC123",
                api_key="key",
                uploads_playlist_id="UU123",
            ),
            client,
        ).scan_page(SourceScanRequest(initial=True))

    assert page.items == ()
    assert page.checkpoint["data_api"]["head_ids"] == ["private-video"]


@pytest.mark.asyncio
async def test_first_recovered_data_api_scan_uses_atom_video_ids_as_baseline() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/playlistItems"):
            payload = {
                "items": [
                    {
                        "contentDetails": {"videoId": video_id},
                        "snippet": {"publishedAt": "2026-08-01T12:00:00Z"},
                    }
                    for video_id in ("new-video", "atom-baseline", "older-video")
                ],
                "nextPageToken": "older-page",
            }
        else:
            ids = str(request.url.params["id"]).split(",")
            payload = {
                "items": [
                    {
                        "id": video_id,
                        "snippet": {
                            "title": video_id,
                            "publishedAt": "2026-08-01T12:00:00Z",
                        },
                        "contentDetails": {},
                        "status": {"privacyStatus": "public"},
                    }
                    for video_id in ids
                ]
            }
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await YouTubeSourceAdapter(
            YouTubeSourceConfig(
                channel_id="UC123",
                feed_url="https://youtube.com/feeds/videos.xml?channel_id=UC123",
                api_key="key",
                uploads_playlist_id="UU123",
            ),
            client,
        ).scan_page(
            SourceScanRequest(
                checkpoint={"atom": {"head_ids": ["atom-baseline"], "item_hashes": {}}}
            )
        )

    assert [item.native_id for item in page.items] == ["new-video", "atom-baseline"]
    assert page.completed is True
    assert page.next_continuation is None
    assert page.checkpoint["data_api"]["head_ids"][:2] == ["new-video", "atom-baseline"]
    assert len([request for request in requests if request.url.path.endswith("playlistItems")]) == 1


@pytest.mark.asyncio
async def test_known_unavailable_video_is_retained_as_a_status_update() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = (
            {
                "items": [
                    {
                        "contentDetails": {"videoId": "known-video"},
                        "snippet": {
                            "title": "Deleted video",
                            "publishedAt": "2026-07-01T12:00:00Z",
                        },
                    }
                ]
            }
            if request.url.path.endswith("/playlistItems")
            else {"items": []}
        )
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await YouTubeSourceAdapter(
            YouTubeSourceConfig(
                channel_id="UC123",
                feed_url="https://youtube.com/feeds/videos.xml?channel_id=UC123",
                api_key="key",
                uploads_playlist_id="UU123",
            ),
            client,
        ).scan_page(
            SourceScanRequest(
                checkpoint={
                    "data_api": {
                        "head_ids": ["known-video"],
                        "item_hashes": {"known-video": "old-hash"},
                    }
                }
            )
        )

    assert page.completed is True
    assert page.items[0].native_id == "known-video"
    assert page.items[0].raw_metadata["availability"] == "unavailable"


@pytest.mark.asyncio
async def test_youtube_continuation_replays_with_its_original_page_size() -> None:
    playlist_requests: list[tuple[str | None, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/playlistItems"):
            token = request.url.params.get("pageToken")
            page_size = request.url.params["maxResults"]
            playlist_requests.append((token, page_size))
            if token == "replay-page":
                payload = {
                    "items": [
                        {
                            "contentDetails": {"videoId": "saved-boundary"},
                            "snippet": {"publishedAt": "2026-07-01T12:00:00Z"},
                        }
                    ],
                    "nextPageToken": "next-page",
                }
            else:
                payload = {
                    "items": [
                        {
                            "contentDetails": {"videoId": "old-anchor"},
                            "snippet": {"publishedAt": "2026-06-01T12:00:00Z"},
                        }
                    ]
                }
        else:
            payload = {
                "items": [
                    {
                        "id": "old-anchor",
                        "snippet": {
                            "title": "Old anchor",
                            "publishedAt": "2026-06-01T12:00:00Z",
                        },
                        "contentDetails": {},
                        "status": {"privacyStatus": "public"},
                    }
                ]
            }
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await YouTubeSourceAdapter(
            YouTubeSourceConfig(
                channel_id="UC123",
                feed_url="https://youtube.com/feeds/videos.xml?channel_id=UC123",
                api_key="key",
                uploads_playlist_id="UU123",
            ),
            client,
        ).scan_page(
            SourceScanRequest(
                checkpoint={"data_api": {"head_ids": ["old-anchor"]}},
                continuation={
                    "provider": "youtube_data_api",
                    "playlist_id": "UU123",
                    "replay_page_token": "replay-page",
                    "page_size": 20,
                    "boundary_ids": ["saved-boundary"],
                    "next_page_token": "next-page",
                },
                max_raw_items=50,
            )
        )

    assert playlist_requests == [("replay-page", "20"), ("next-page", "50")]
    assert page.completed is True
    assert [item.native_id for item in page.items] == ["old-anchor"]
