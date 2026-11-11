"""The four source adapters, against canned upstream responses."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from reader.ingestion.adapters import rss
from reader.ingestion.adapters.reddit import RedditHotAdapter, hot_feed_url
from reader.ingestion.adapters.rss import RssAdapter
from reader.ingestion.adapters.x import XAdapter
from reader.ingestion.adapters.youtube import YouTubeAdapter
from reader.ingestion.errors import ScanError
from reader.ingestion.items import Item, Media
from reader.ingestion.x_relations import normalize_x_relations

FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>Example</title>
<link>https://example.com/</link>
<image><url>https://cdn.example.com/feed.png</url></image>
<item><guid>entry-2</guid><title>Second</title><link>https://example.com/b</link>
<pubDate>Wed, 03 Jan 2024 10:00:00 +0000</pubDate>
<description><![CDATA[<p>Body <b>two</b></p><script>alert(1)</script><img src="https://cdn.example.com/b.jpg">]]></description>
</item>
<item><guid>entry-1</guid><title>First</title><link>https://example.com/a</link></item>
<item><title>No link</title></item>
</channel></rss>"""


def _serve(monkeypatch, module, body: str | bytes, status: int = 200, seen: list | None = None):
    async def fake_safe_get(_client, url, **_kwargs):
        if seen is not None:
            seen.append(url)
        content = body.encode() if isinstance(body, str) else body
        return httpx.Response(status, content=content, request=httpx.Request("GET", url))

    monkeypatch.setattr(module, "safe_get", fake_safe_get)


@pytest.mark.asyncio
async def test_rss_reads_items_newest_first_and_sanitizes(monkeypatch) -> None:
    _serve(monkeypatch, rss, FEED)
    async with httpx.AsyncClient() as client:
        page = await RssAdapter(
            client, "https://example.com/feed.xml", max_bytes=1 << 20, find_icon=True
        ).fetch_page(None, 120)

    assert [item.native_id for item in page.items] == ["entry-2", "entry-1"]
    newest = page.items[0]
    assert newest.title == "Second" and newest.url == "https://example.com/b"
    assert newest.published_at == datetime(2024, 1, 3, 10, tzinfo=UTC)
    assert "<script>" not in newest.body_html and "<b>two</b>" in newest.body_html
    assert newest.summary == "Body two"
    assert newest.media == (Media("https://cdn.example.com/b.jpg"),)
    assert page.items[1].title == "First" and page.items[1].published_at is None
    assert page.source_name == "Example"
    assert page.source_icon_url == "https://cdn.example.com/feed.png"
    assert page.next_cursor is None and page.chronological


@pytest.mark.asyncio
async def test_rss_respects_the_item_limit_and_skips_icons_unless_asked(monkeypatch) -> None:
    _serve(monkeypatch, rss, FEED)
    async with httpx.AsyncClient() as client:
        page = await RssAdapter(client, "https://e.com/f", max_bytes=1 << 20).fetch_page(None, 1)

    assert [item.native_id for item in page.items] == ["entry-2"]
    assert page.source_icon_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [(200, "not a feed at all", "invalid_feed"), (404, "", "rss_http_404")],
)
async def test_rss_failures_are_scan_errors(monkeypatch, status, body, code) -> None:
    _serve(monkeypatch, rss, body, status)
    async with httpx.AsyncClient() as client:
        with pytest.raises(ScanError) as raised:
            await RssAdapter(client, "https://e.com/f", max_bytes=1 << 20).fetch_page(None, 10)

    assert raised.value.code == code


@pytest.mark.asyncio
async def test_reddit_hot_is_marked_non_chronological(monkeypatch) -> None:
    seen: list[str] = []
    _serve(monkeypatch, rss, FEED, seen=seen)
    async with httpx.AsyncClient() as client:
        page = await RedditHotAdapter(client, subreddit="LocalLLaMA", max_bytes=1 << 20).fetch_page(
            None, 120
        )

    assert seen == ["https://www.reddit.com/r/LocalLLaMA/hot/.rss?limit=10"]
    assert not page.chronological
    assert page.source_name == "r/LocalLLaMA"
    assert {item.kind for item in page.items} == {"post"}
    assert page.items[0].extra == {"subreddit": "LocalLLaMA"}


def test_reddit_rejects_bad_subreddit_names() -> None:
    with pytest.raises(ValueError):
        hot_feed_url("../etc")


def _youtube_api(requests: list, *, quota_exhausted=False):
    def handler(request: httpx.Request) -> httpx.Response:
        resource = request.url.path.rsplit("/", 1)[-1]
        requests.append((resource, dict(request.url.params)))
        if quota_exhausted:
            return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
        if resource == "channels":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
                            "snippet": {
                                "title": "Channel",
                                "thumbnails": {"high": {"url": "https://yt/avatar.jpg"}},
                            },
                        }
                    ]
                },
            )
        if resource == "playlistItems":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"contentDetails": {"videoId": "v2"}},
                        {"contentDetails": {"videoId": "gone"}},
                    ],
                    "nextPageToken": "T2",
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "v2",
                        "snippet": {
                            "title": "Video two",
                            "publishedAt": "2024-01-03T10:00:00Z",
                            "channelTitle": "Channel",
                            "description": "a < b\nline two",
                            "thumbnails": {"maxres": {"url": "https://yt/v2.jpg"}},
                        },
                        "contentDetails": {"duration": "PT1M"},
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_youtube_data_api_resolves_uploads_and_skips_unavailable_videos() -> None:
    requests: list = []
    async with httpx.AsyncClient(transport=_youtube_api(requests)) as client:
        page = await YouTubeAdapter(
            client,
            channel_id="UC1",
            feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC1",
            api_key="key",
            uploads_playlist_id=None,
            max_bytes=1 << 20,
        ).fetch_page(None, 120)

    assert [item.native_id for item in page.items] == ["v2"]  # "gone" has no metadata
    video = page.items[0]
    assert video.kind == "video" and video.url == "https://www.youtube.com/watch?v=v2"
    assert video.summary == "a < b line two"
    assert page.next_cursor == "T2"
    assert page.source_config == {"uploads_playlist_id": "UU1"}
    assert page.source_name == "Channel" and page.source_icon_url == "https://yt/avatar.jpg"
    assert requests[1] == (
        "playlistItems",
        {"part": "contentDetails", "playlistId": "UU1", "maxResults": "50", "key": "key"},
    )


@pytest.mark.asyncio
async def test_youtube_falls_back_to_atom_when_the_quota_is_used_up(monkeypatch) -> None:
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"><title>Channel</title>
      <entry><id>yt:video:abc</id><yt:videoId>abc</yt:videoId><title>Atom video</title>
      <link rel="alternate" href="https://www.youtube.com/watch?v=abc"/></entry></feed>"""
    _serve(monkeypatch, rss, atom)
    requests: list = []
    async with httpx.AsyncClient(transport=_youtube_api(requests, quota_exhausted=True)) as client:
        adapter = YouTubeAdapter(
            client,
            channel_id="UC1",
            feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC1",
            api_key="key",
            uploads_playlist_id="UU1",
            max_bytes=1 << 20,
        )
        page = await adapter.fetch_page(None, 120)

    assert adapter.used_fallback
    assert [(item.native_id, item.kind) for item in page.items] == [("abc", "video")]
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_youtube_without_a_key_uses_atom_directly(monkeypatch) -> None:
    seen: list[str] = []
    _serve(monkeypatch, rss, FEED, seen=seen)
    async with httpx.AsyncClient() as client:
        await YouTubeAdapter(
            client,
            channel_id="UC1",
            feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC1",
            api_key=None,
            uploads_playlist_id=None,
            max_bytes=1 << 20,
        ).fetch_page(None, 10)

    assert seen == ["https://www.youtube.com/feeds/videos.xml?channel_id=UC1"]


def _scweet(bodies: list, *, payload=None, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"items": [{"profile_image_url": "https://x/a.jpg"}]})
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def _tweet(tweet_id: str, **extra) -> dict:
    return {
        "tweet_id": tweet_id,
        "tweet_url": f"https://x.com/writer/status/{tweet_id}",
        "text": f"tweet {tweet_id}\nsecond line",
        "timestamp": "2024-01-03T10:00:00Z",
        "user": {"screen_name": "writer"},
        **extra,
    }


def _x(transport) -> XAdapter:
    return XAdapter(
        httpx.AsyncClient(transport=transport),
        handle="writer",
        service_url="http://scweet:8090/",
        service_token="secret",
        max_bytes=1 << 20,
        find_icon=True,
    )


@pytest.mark.asyncio
async def test_x_pages_carry_the_boundary_for_the_next_request() -> None:
    bodies: list = []
    payload = {
        "items": [_tweet("3", is_pinned=True), _tweet("2", is_pinned=False), {"bad": "row"}],
        "continuation": "C2",
        "completed": False,
    }
    adapter = _x(_scweet(bodies, payload=payload))

    page = await adapter.fetch_page(None, 120)
    await adapter.fetch_page(page.next_cursor, 120)

    assert [item.native_id for item in page.items] == ["3", "2"]
    assert [item.pinned for item in page.items] == [True, False]
    first = page.items[1]
    assert first.author == "@writer" and first.kind == "post"
    assert first.title == "tweet 2 second line"
    assert first.body_html == "tweet 2<br>second line"
    assert first.extra["x"]["tweet_id"] == "2"
    assert bodies[0] == ("/v1/profiles/tweets", {"usernames": ["writer"], "limit": 50})
    assert bodies[2][1]["cursor"] == "C2" and bodies[2][1]["boundary_ids"] == ["3", "2"]
    assert page.source_icon_url == "https://x/a.jpg"  # looked up: rows carried no image


@pytest.mark.asyncio
async def test_x_unflagged_first_row_is_treated_as_possibly_pinned() -> None:
    adapter = _x(_scweet([], payload={"items": [_tweet("2"), _tweet("1")], "completed": True}))

    page = await adapter.fetch_page(None, 10)

    assert [item.pinned for item in page.items] == [True, False]
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_x_service_errors_keep_their_code() -> None:
    adapter = _x(
        _scweet([], payload={"detail": {"code": "protected_account", "message": "no"}}, status=403)
    )

    with pytest.raises(ScanError) as raised:
        await adapter.fetch_page(None, 10)

    assert raised.value.code == "protected_account" and raised.value.long_lived


@pytest.mark.asyncio
async def test_x_rejects_a_tampered_cursor() -> None:
    with pytest.raises(ScanError) as raised:
        await _x(_scweet([], payload={"items": []})).fetch_page("not json", 10)

    assert raised.value.code == "cursor_invalid"


def test_engagement_counters_do_not_change_the_content_hash() -> None:
    base = Item(
        native_id="1", kind="post", title="t", url="https://x/1", extra={"stats": {"likes": 1}}
    )
    liked = Item(
        native_id="1", kind="post", title="t", url="https://x/1", extra={"stats": {"likes": 99}}
    )
    edited = Item(
        native_id="1", kind="post", title="t2", url="https://x/1", extra={"stats": {"likes": 1}}
    )

    assert base.content_hash() == liked.content_hash()
    assert base.content_hash() != edited.content_hash()


def test_x_relations_are_parsed_from_graphql_records() -> None:
    node = {
        "rest_id": "200",
        "legacy": {
            "user_id_str": "42",
            "full_text": "text",
            "in_reply_to_status_id_str": "199",
            "in_reply_to_user_id_str": "42",
            "quoted_status_id_str": "100",
        },
    }
    parsed = normalize_x_relations({"tweet_id": "200", "raw": node})

    assert parsed["reply_to"]["tweet_id"] == "199"
    assert parsed["quote"]["availability"] == "unavailable"
    assert parsed["completeness"] == "parsed"
