import httpx
import pytest

from app.domain.enums import ContentKind
from app.ingestion.models import SourceScanRequest
from app.ingestion.sources.rss import RSSSourceAdapter, RSSSourceConfig


async def test_web_feed_uses_article_urls_across_guid_changes(monkeypatch):
    from app.ingestion.source_identity import web_article_native_id

    guid = "first-guid"

    async def fetch(_client, url, **kwargs):
        return httpx.Response(
            200,
            text=f"""<rss version="2.0"><channel><title>News</title>
            <item><guid>{guid}</guid><title>Article</title>
              <link>/post?lang=en&amp;utm_source=rss#part</link></item>
            <item><guid>bad</guid><title>Bad link</title><link>javascript:alert(1)</link></item>
            <item><guid>missing-title</guid><link>/untitled</link></item>
            </channel></rss>""",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fetch)
    async with httpx.AsyncClient() as client:
        adapter = RSSSourceAdapter(
            RSSSourceConfig(url="https://example.com/news/feed", provider_mode="web_rss"), client
        )
        first = await adapter.scan_page(SourceScanRequest(initial=True))
        guid = "replacement-guid"
        second = await adapter.scan_page(SourceScanRequest(checkpoint=first.checkpoint))
    assert len(first.items) == len(second.items) == 1
    assert first.items[0].external_url == "https://example.com/post?lang=en"
    assert (
        first.items[0].native_id
        == second.items[0].native_id
        == web_article_native_id("https://example.com/post?lang=en")
    )
    assert second.gap_detected is False


@pytest.mark.parametrize(
    "body",
    [
        "<html><title>Login</title></html>",
        '<rss version="2.0"><channel><item><link>/x</link></item></channel></rss>',
        '<rss version="2.0"><channel><item><title>Missing URL</title></item></channel></rss>',
        '<rss version="2.0"/>',
    ],
)
async def test_selected_web_feed_rejects_invalid_content_instead_of_no_updates(monkeypatch, body):
    from app.ingestion.models import SourceScanError

    async def fetch(_client, url, **kwargs):
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fetch)
    async with httpx.AsyncClient() as client:
        adapter = RSSSourceAdapter(
            RSSSourceConfig(url="https://example.com/feed", provider_mode="web_rss"), client
        )
        with pytest.raises(SourceScanError, match="no longer returns a feed"):
            await adapter.scan_page(SourceScanRequest(initial=True))


@pytest.mark.parametrize("authority", ["[2606:4700:4700::1111]", "[2606:4700:4700::1111]:8443"])
async def test_web_feed_keeps_ipv6_article_urls_valid(authority):
    from app.ingestion.source_identity import web_article_native_id

    url = f"https://{authority}/post"
    async with httpx.AsyncClient() as client:
        adapter = RSSSourceAdapter(
            RSSSourceConfig(url="https://example.com/feed", provider_mode="web_rss"), client
        )
        item = adapter._entry_to_content({"title": "Article", "link": url})
    assert item.external_url == url
    assert item.native_id == web_article_native_id(url)


@pytest.mark.asyncio
async def test_rss_scanner_normalises_entries_media_and_validators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = """<?xml version="1.0"?><rss version="2.0"><channel><title>Example</title>
    <image><url>https://cdn.example.com/feed-avatar.png</url></image>
    <item><guid>entry-1</guid><title>Hello</title><link>https://example.com/a</link>
    <pubDate>Tue, 02 Jan 2024 10:00:00 +0000</pubDate>
    <description><![CDATA[<p>Summary</p><img src="https://cdn.example.com/a.jpg">]]></description>
    </item></channel></rss>"""

    async def fake_safe_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            content=feed.encode(),
            headers={"etag": "feed-v1", "last-modified": "Tue, 02 Jan 2024 11:00:00 GMT"},
            request=httpx.Request("GET", "https://example.com/feed.xml"),
        )

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        page = await RSSSourceAdapter(
            RSSSourceConfig(url="https://example.com/feed.xml"), client
        ).scan_page(SourceScanRequest(initial=True))

    assert page.checkpoint["validators"] == {
        "etag": "feed-v1",
        "last_modified": "Tue, 02 Jan 2024 11:00:00 GMT",
    }
    assert page.source_display_name == "Example"
    assert page.source_avatar_url == "https://cdn.example.com/feed-avatar.png"
    assert page.items[0].native_id == "entry-1"
    assert page.items[0].media[0].original_url == "https://cdn.example.com/a.jpg"


@pytest.mark.asyncio
async def test_rss_scanner_uses_server_raw_limit_without_time_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = """<?xml version="1.0"?><rss version="2.0"><channel><title>Example</title>
    <item><guid>first</guid><title>First</title><link>https://example.com/first</link>
      <pubDate>Tue, 02 Jan 2024 10:00:00 +0000</pubDate></item>
    <item><guid>second</guid><title>Second</title><link>https://example.com/second</link></item>
    </channel></rss>"""

    async def fake_safe_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            content=feed.encode(),
            request=httpx.Request("GET", "https://example.com/feed.xml"),
        )

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        page = await RSSSourceAdapter(
            RSSSourceConfig(url="https://example.com/feed.xml"), client
        ).scan_page(SourceScanRequest(max_raw_items=1))

    assert [item.title for item in page.items] == ["First"]
    assert page.limit_reached == "raw_items"
    assert page.gap_detected is False


@pytest.mark.asyncio
async def test_rss_scanner_stops_only_after_a_stable_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = """<?xml version="1.0"?><rss version="2.0"><channel><title>Example</title>
    <item><guid>new</guid><title>New</title><link>https://example.com/new</link></item>
    <item><guid>known</guid><title>Edited known</title><link>https://example.com/known</link></item>
    <item><guid>older</guid><title>Older</title><link>https://example.com/older</link></item>
    </channel></rss>"""

    async def fake_safe_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            content=feed.encode(),
            request=httpx.Request("GET", "https://example.com/feed.xml"),
        )

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        page = await RSSSourceAdapter(
            RSSSourceConfig(url="https://example.com/feed.xml"), client
        ).scan_page(SourceScanRequest(checkpoint={"head_ids": ["known"]}))

    assert [item.native_id for item in page.items] == ["new", "known"]
    assert page.completed is True


@pytest.mark.asyncio
async def test_rss_scanner_checks_through_oldest_rolling_anchor_after_reordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = """<?xml version="1.0"?><rss version="2.0"><channel><title>Example</title>
    <item><guid>pinned</guid><title>Pinned</title><link>https://example.com/pinned</link></item>
    <item><guid>new</guid><title>New</title><link>https://example.com/new</link></item>
    <item><guid>known</guid><title>Known</title><link>https://example.com/known</link></item>
    <item><guid>oldest-anchor</guid><title>Old anchor</title><link>https://example.com/anchor</link></item>
    <item><guid>tail</guid><title>Tail</title><link>https://example.com/tail</link></item>
    </channel></rss>"""

    async def fake_safe_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            content=feed.encode(),
            request=httpx.Request("GET", "https://example.com/feed.xml"),
        )

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        page = await RSSSourceAdapter(
            RSSSourceConfig(url="https://example.com/feed.xml"), client
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


@pytest.mark.asyncio
async def test_rss_raw_limit_is_not_reported_when_anchor_proves_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = """<rss version="2.0"><channel><title>Example</title>
    <item><guid>new</guid><title>New</title><link>https://example.com/new</link></item>
    <item><guid>known</guid><title>Known</title><link>https://example.com/known</link></item>
    <item><guid>older</guid><title>Older</title><link>https://example.com/older</link></item>
    </channel></rss>"""

    async def fake_safe_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            content=feed.encode(),
            request=httpx.Request("GET", "https://example.com/feed.xml"),
        )

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        page = await RSSSourceAdapter(
            RSSSourceConfig(url="https://example.com/feed.xml"), client
        ).scan_page(
            SourceScanRequest(
                checkpoint={"head_ids": ["known"]},
                max_raw_items=2,
            )
        )

    assert page.completed is True
    assert page.limit_reached is None
    assert page.gap_detected is False


@pytest.mark.asyncio
async def test_rss_scanner_backfills_site_favicon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
    <title>Example</title><link>https://example.com/blog</link>
    <item><guid>first</guid><title>First</title><link>https://example.com/first</link></item>
    </channel></rss>"""

    async def fake_safe_get(*args: object, **_kwargs: object) -> httpx.Response:
        url = str(args[1])
        content = feed if url.endswith("feed.xml") else '<link rel="icon" href="/brand.svg">'
        return httpx.Response(200, content=content.encode(), request=httpx.Request("GET", url))

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        page = await RSSSourceAdapter(
            RSSSourceConfig(
                url="https://example.com/feed.xml",
                site_url="https://example.com/",
                resolve_site_avatar=True,
            ),
            client,
        ).scan_page(SourceScanRequest(initial=True))

    assert page.source_avatar_url == "https://example.com/brand.svg"


@pytest.mark.asyncio
async def test_youtube_atom_identity_matches_data_api_video_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
      <title>OpenAI</title><entry><id>yt:video:abc123</id><yt:videoId>abc123</yt:videoId>
      <title>Video title</title><link rel="alternate" href="https://youtube.com/watch?v=abc123"/>
      <media:group><media:thumbnail url="https://i.ytimg.com/vi/abc123/hqdefault.jpg"/></media:group>
      </entry></feed>"""

    async def fake_safe_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            content=feed.encode(),
            request=httpx.Request("GET", "https://example.com/feed.xml"),
        )

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        page = await RSSSourceAdapter(
            RSSSourceConfig(
                url="https://example.com/feed.xml",
                content_kind=ContentKind.VIDEO,
                provider_mode="youtube_atom",
            ),
            client,
        ).scan_page(SourceScanRequest(initial=True))

    assert page.items[0].native_id == "abc123"
    assert page.items[0].media[0].original_url.endswith("hqdefault.jpg")


@pytest.mark.asyncio
async def test_rss_archive_continuation_replays_and_validates_its_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = """<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>
    <title>Archive</title><atom:link rel="prev-archive" href="https://example.com/archive-2.xml" />
    <item><guid>new-1</guid><title>New</title><link>https://example.com/new-1</link></item>
    </channel></rss>"""
    archive = """<rss version="2.0"><channel><title>Archive</title>
    <item><guid>known</guid><title>Known</title><link>https://example.com/known</link></item>
    </channel></rss>"""
    calls: list[str] = []

    async def fake_safe_get(
        _client: httpx.AsyncClient, url: str, **_kwargs: object
    ) -> httpx.Response:
        calls.append(url)
        body = archive if url.endswith("archive-2.xml") else current
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.ingestion.sources.rss.safe_get", fake_safe_get)
    async with httpx.AsyncClient() as client:
        adapter = RSSSourceAdapter(RSSSourceConfig(url="https://example.com/feed.xml"), client)
        first = await adapter.scan_page(SourceScanRequest(checkpoint={"head_ids": ["known"]}))
        second = await adapter.scan_page(
            SourceScanRequest(
                checkpoint={"head_ids": ["known"]},
                continuation=first.next_continuation,
                conditional=False,
            )
        )

    assert first.completed is False
    assert first.next_continuation is not None
    assert second.completed is True
    assert [item.native_id for item in second.items] == ["known"]
    assert calls == [
        "https://example.com/feed.xml",
        "https://example.com/feed.xml",
        "https://example.com/archive-2.xml",
    ]
