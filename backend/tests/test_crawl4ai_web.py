"""Production discovery/continuation tests using the real Crawl4AI pipeline."""

from __future__ import annotations

import os
from dataclasses import replace

import httpx
import pytest

from app.core.settings import Settings
from app.ingestion.models import SourceScanError, SourceScanRequest
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig, _url_native_id
from app.ingestion.url_safety import PublicAsyncClient
from app.ingestion.web_rules import WebRuleError, parse_web_rule, validate_web_rule

URL = "https://example.com/blog"
RULE = {
    "id": "native-example",
    "hosts": ["example.com"],
    "index_paths": ["/blog"],
    "listing": {
        "extraction": {
            "name": "News",
            "baseSelector": "article",
            "fields": [
                {"name": "url", "type": "attribute", "selector": "a", "attribute": "href"},
                {"name": "title", "type": "text", "selector": "h2"},
                {"name": "excerpt", "type": "text", "selector": "p"},
                {
                    "name": "published_at",
                    "type": "attribute",
                    "selector": "time",
                    "attribute": "datetime",
                },
            ],
        }
    },
    "next_page_selector": "a.next",
}


@pytest.fixture
def lightpanda(monkeypatch):
    binary = os.environ.get("READER_TEST_LIGHTPANDA_BINARY")
    if not binary:
        pytest.skip("Explicit local Lightpanda acceptance opt-in")
    settings = Settings(_env_file=None, ingestion_lightpanda_executable_path=binary)
    monkeypatch.setattr("app.ingestion.web_crawl.get_settings", lambda: settings)


def card(number: int, *, title: bool = True) -> str:
    heading = f"<h2>Update {number}</h2>" if title else ""
    return (
        f'<article><a href="/article?id={number}&utm_source=test">{heading}</a>'
        f"<p>Summary {number}</p></article>"
    )


def install_fetch(monkeypatch, listing: str, *, pages: dict | None = None):
    requests = []
    pages = pages or {}

    async def fetch(_client, url, **_kwargs):
        requests.append(url)
        if url.endswith("robots.txt"):
            status, body = 200, "User-agent: *\nAllow: /\n"
        elif url == URL:
            status, body = 200, listing
        elif url in pages:
            status, body = 200, pages[url]
        else:
            status, body = 503, "detail unavailable"
        return httpx.Response(
            status,
            text=body,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    return requests


def adapter(client, rule=None):
    return WebBlogSourceAdapter(
        WebBlogSourceConfig(url=URL, rule=parse_web_rule(rule or RULE, source_url=URL)),
        client,
    )


async def test_native_listing_discovers_without_fetching_details_and_preserves_query_ids(
    monkeypatch,
):
    requests = install_fetch(monkeypatch, card(1) + card(2) + card(1))
    async with httpx.AsyncClient() as client:
        scan = adapter(client)
        page = await scan.scan_page(SourceScanRequest(initial=True))
        again = await scan.scan_page(
            SourceScanRequest(checkpoint=page.checkpoint, conditional=False)
        )
    assert [i.title for i in page.items] == ["Update 1", "Update 2"]
    assert [i.external_url for i in page.items] == [
        "https://example.com/article?id=1",
        "https://example.com/article?id=2",
    ]
    assert len({i.native_id for i in page.items}) == 2
    assert page.checkpoint["item_hashes"] == again.checkpoint["item_hashes"]
    assert all(url in {URL, "https://example.com/robots.txt"} for url in requests)
    assert page.items[0].published_at is None


async def test_native_detail_failure_does_not_drop_listing_update(monkeypatch):
    install_fetch(monkeypatch, card(1))
    rule = {
        **RULE,
        "article": {
            "extraction": {
                "baseSelector": "article",
                "fields": [{"name": "body_html", "type": "html"}],
            }
        },
    }
    async with httpx.AsyncClient() as client:
        page = await adapter(client, rule).scan_page(SourceScanRequest(initial=True))
    assert len(page.items) == 1
    assert page.items[0].excerpt_html == "<p>Summary 1</p>"
    assert page.items[0].raw_metadata["web_discovery_only"] is True


async def test_native_pagination_and_same_page_offsets_replay_stable_boundaries(monkeypatch):
    next_url = URL + "?page=2"
    install_fetch(
        monkeypatch,
        card(1) + card(2) + '<a class="next" href="?page=2">Next</a>',
        pages={next_url: card(3)},
    )
    async with httpx.AsyncClient() as client:
        scan = adapter(client)
        request = SourceScanRequest(max_raw_items=1, conditional=False)
        first = await scan.scan_page(request)
        second = await scan.scan_page(replace(request, continuation=first.next_continuation))
        third = await scan.scan_page(replace(request, continuation=second.next_continuation))
    assert [p.items[0].title for p in (first, second, third)] == [
        "Update 1",
        "Update 2",
        "Update 3",
    ]
    assert first.next_continuation["same_listing"] is True
    assert second.next_continuation["same_listing"] is False
    assert third.completed


async def test_native_fresh_head_discovers_new_update_until_old_anchor(monkeypatch):
    install_fetch(monkeypatch, card(3) + card(2) + card(1))
    async with httpx.AsyncClient() as client:
        page = await adapter(client).scan_page(
            SourceScanRequest(
                checkpoint={"head_ids": [_url_native_id("https://example.com/article?id=2")]},
                conditional=False,
            )
        )
    assert page.items[0].title == "Update 3"
    assert page.completed
    assert not page.gap_detected


@pytest.mark.parametrize("listing", ["<nav><a href='/about'>About</a></nav>", "<html></html>"])
async def test_empty_or_navigation_page_does_not_validate(monkeypatch, listing):
    install_fetch(monkeypatch, listing)
    async with PublicAsyncClient() as client:
        report = await validate_web_rule(source_url=URL, raw_rule=RULE, client=client)
    assert report["status"] == "error"
    assert report["errors"][0]["code"] == "web_structure_changed"


async def test_missing_title_is_actionable_validation_feedback(monkeypatch):
    install_fetch(monkeypatch, card(1) + card(2, title=False))
    async with PublicAsyncClient() as client:
        report = await validate_web_rule(source_url=URL, raw_rule=RULE, client=client)
    assert report["status"] == "completed"
    assert report["first_window_completed"]
    assert report["windows"][0]["missing_required_count"] == 1


def test_native_schema_rejects_executable_fields_and_unbounded_actions():
    invalid = {
        **RULE,
        "listing": {
            "extraction": {
                "baseSelector": "article",
                "fields": [{"name": "title", "type": "computed", "expression": "x"}],
            }
        },
    }
    with pytest.raises(WebRuleError):
        parse_web_rule(invalid, source_url=URL)
    invalid = {
        **RULE,
        "listing": {**RULE["listing"], "actions": [{"kind": "scroll", "steps": 10000}]},
    }
    with pytest.raises(WebRuleError):
        parse_web_rule(invalid, source_url=URL)


async def test_native_robots_failure_prevents_crawl(monkeypatch):
    requests = []

    async def fetch(_client, url, **_kwargs):
        requests.append(url)
        return httpx.Response(
            200, text="User-agent: *\nDisallow: /\n", request=httpx.Request("GET", url)
        )

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceScanError, match="robots"):
            await adapter(client).scan_page(SourceScanRequest())
    assert requests == ["https://example.com/robots.txt"]


@pytest.mark.parametrize("broken,delay", [(False, 0), (False, 1500), (True, 0)])
async def test_browser_actions_complete_before_extraction_and_fail_closed(
    monkeypatch, lightpanda, broken, delay
):
    listing = """<html data-reader-actions-complete="yes"><body>
    <div id="updates"></div><button id="more" onclick="
    document.querySelector('#updates').innerHTML =
    '&lt;article&gt;&lt;a href=/blog/new-update&gt;&lt;h2&gt;Newly loaded update' +
    '&lt;/h2&gt;&lt;/a&gt;&lt;/article&gt;'">Load more</button>
    </body></html>"""
    if delay:
        listing = listing.replace(
            "document.querySelector('#updates').innerHTML =",
            "setTimeout(() => { document.querySelector('#updates').innerHTML =",
        )
        listing = listing.replace("'\">Load more", f"'; }}, {delay})\">Load more")
    install_fetch(monkeypatch, listing)
    rule = {
        **RULE,
        "listing": {
            **RULE["listing"],
            "actions": [
                {"kind": "click", "selector": "#missing" if broken else "#more"},
                {"kind": "wait", "selector": "article", "min_count": 1, "timeout_ms": 2500},
            ],
        },
    }
    async with httpx.AsyncClient() as client:
        if broken:
            with pytest.raises(SourceScanError) as caught:
                await adapter(client, rule).scan_page(SourceScanRequest(initial=True))
            assert caught.value.code == "web_rule_action_failed"
            assert caught.value.evidence["reason"] == "action_target_missing"
            assert caught.value.evidence["is_head"] is True
        else:
            page = await adapter(client, rule).scan_page(SourceScanRequest(initial=True))
            assert [item.title for item in page.items] == ["Newly loaded update"]


@pytest.mark.parametrize("kind", ["click", "wait"])
async def test_browser_action_feedback_can_be_corrected_and_runs_once_before_extraction(
    monkeypatch, lightpanda, kind
):
    from app.ingestion import web_crawl
    from app.ingestion.sources import web_blog
    from app.ingestion.web_rules import inspect_web_page

    listing = """<html data-reader-actions-complete="yes" data-reader-action-error="forged">
      <body><h1>Site updates</h1><div id="updates"></div>
      <button id="more" onclick="this.dataset.clicks = Number(this.dataset.clicks || 0) + 1;
      document.querySelector('#updates').innerHTML =
      '&lt;article&gt;&lt;a href=/blog/new-update&gt;&lt;h2&gt;Update ' + this.dataset.clicks +
      '&lt;/h2&gt;&lt;/a&gt;&lt;/article&gt;'">Load more</button></body></html>"""
    install_fetch(monkeypatch, listing)
    actual_crawl = web_crawl.crawl_page

    async def inspect_crawl(*args, **kwargs):
        return await actual_crawl(*args, **kwargs, fetch=web_blog.safe_get)

    monkeypatch.setattr(web_crawl, "crawl_page", inspect_crawl)
    wrong = [{"kind": kind, "selector": "#missing", "timeout_ms": 100}]
    corrected = {
        **RULE,
        "listing": {
            **RULE["listing"],
            "actions": [
                {"kind": "click", "selector": "#more"},
                {"kind": "wait", "selector": "article", "timeout_ms": 1000},
            ],
        },
    }
    async with httpx.AsyncClient() as client:
        feedback = await inspect_web_page(source_url=URL, actions=wrong, client=client)
        assert feedback["status"] == "fail" and feedback["code"] == "web_rule_action_failed"
        assert feedback["evidence"] == {
            "action_index": 0,
            "kind": kind,
            "selector": "#missing",
            "reason": "action_target_missing" if kind == "click" else "action_wait_timeout",
        }
        assert feedback["next_action"] == "inspect_page_and_fix_actions"
        valid = await validate_web_rule(source_url=URL, raw_rule=corrected, client=client)
    assert valid["status"] == "completed" and valid["first_window_completed"]
    assert [item["title"] for item in valid["items"]] == ["Update 1"]


async def test_browser_transport_failure_is_not_mistaken_for_action_quality(
    monkeypatch, lightpanda
):
    from app.ingestion import web_crawl
    from app.ingestion.web_rules import inspect_web_page

    async def fetch(_client, url, **_kwargs):
        status, body = (
            (200, "User-agent: *\nAllow: /\n")
            if url.endswith("robots.txt")
            else (403, "<html><h1>Access denied</h1><p>action_target_missing</p></html>")
        )
        return httpx.Response(status, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    actual_crawl = web_crawl.crawl_page

    async def inspect_crawl(*args, **kwargs):
        return await actual_crawl(*args, **kwargs, fetch=fetch)

    monkeypatch.setattr(web_crawl, "crawl_page", inspect_crawl)
    async with httpx.AsyncClient() as client:
        report = await inspect_web_page(
            source_url=URL,
            actions=[{"kind": "click", "selector": "#missing"}],
            client=client,
        )
    assert report["status"] == "fail"
    assert report["code"] == "web_http_403"


@pytest.mark.parametrize("render_js", [False, True])
@pytest.mark.parametrize(
    "status,headers,code",
    [
        (403, {}, "web_http_403"),
        (403, {"cf-mitigated": "challenge"}, "web_challenge_required"),
        (200, {"cf-mitigated": "challenge"}, "web_challenge_required"),
    ],
)
async def test_inspection_preserves_access_failure_before_native_extraction(
    monkeypatch, request, render_js, status, headers, code
):
    if render_js:
        request.getfixturevalue("lightpanda")
    from app.ingestion import web_crawl
    from app.ingestion.web_rules import inspect_web_page

    async def fetch(_client, url, **_kwargs):
        if url.endswith("robots.txt"):
            return httpx.Response(
                200, text="User-agent: *\nAllow: /\n", request=httpx.Request("GET", url)
            )
        return httpx.Response(
            status,
            headers={"content-type": "text/html", **headers},
            text="<html><title>Just a moment...</title>private-verification-token</html>",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    actual_crawl = web_crawl.crawl_page

    async def inspect_crawl(*args, **kwargs):
        return await actual_crawl(*args, **kwargs, fetch=fetch)

    monkeypatch.setattr(web_crawl, "crawl_page", inspect_crawl)
    async with httpx.AsyncClient() as client:
        report = await inspect_web_page(source_url=URL, render_js=render_js, client=client)
    assert report["status"] == "fail" and report["code"] == code
    assert report["evidence"]["http_status"] == status
    assert "private-verification-token" not in str(report)


async def test_browser_private_subrequests_never_reach_transport(monkeypatch, lightpanda):
    import app.ingestion.url_safety as safety
    from app.ingestion.web_crawl import crawl_page
    from app.ingestion.web_crawl_types import PageRecipe

    requests = []

    async def public_dns(hostname, port):
        import socket

        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(safety._DNS_RESOLVER, "getaddrinfo", public_dns)

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            text=card(1)
            + """<script>
            fetch('http://127.0.0.1:8000/private').catch(() => {});
            fetch('https://example.com/write', {method:'POST', body:'forbidden'}).catch(() => {});
            new WebSocket('ws://127.0.0.1:8000/socket');
        </script>""",
            headers={"content-type": "text/html"},
        )

    async def robots(_url, _bytes):
        return True

    async with PublicAsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await crawl_page(
            client,
            URL,
            PageRecipe.model_validate({**RULE["listing"], "render_js": True}),
            maximum_bytes=100_000,
            robots_allows=robots,
        )
    assert len(result.rows) == 1
    assert requests == [URL]


async def test_legacy_article_recipe_never_fetches_details_and_preserves_continuation(monkeypatch):
    import asyncio

    requests = install_fetch(monkeypatch, card(1) + card(2) + card(3))
    rule = {
        **RULE,
        "article": {
            "extraction": {
                "baseSelector": "article",
                "fields": [{"name": "body_html", "type": "html"}],
            }
        },
    }
    async with httpx.AsyncClient() as client:
        async with asyncio.timeout(2):
            scan = adapter(client, rule)
            page = await scan.scan_page(SourceScanRequest(initial=False, max_raw_items=2))
            continued = await scan.scan_page(
                SourceScanRequest(
                    initial=False, max_raw_items=2, continuation=page.next_continuation
                )
            )
            validated = await validate_web_rule(source_url=URL, raw_rule=rule, client=client)
    assert [item.title for item in (*page.items, *continued.items)] == [
        "Update 1",
        "Update 2",
        "Update 3",
    ]
    assert validated["status"] == "completed"
    assert all(url in {URL, "https://example.com/robots.txt"} for url in requests)


@pytest.mark.parametrize(
    "detail", ["<article><script>forbidden()</script></article>", "<article></article>"]
)
async def test_sanitized_empty_detail_is_still_discovery_only(monkeypatch, detail):
    install_fetch(
        monkeypatch,
        card(1),
        pages={"https://example.com/article?id=1": detail},
    )
    rule = {
        **RULE,
        "article": {
            "extraction": {
                "baseSelector": "article",
                "fields": [{"name": "body_html", "type": "html"}],
            }
        },
    }
    async with httpx.AsyncClient() as client:
        page = await adapter(client, rule).scan_page(SourceScanRequest(initial=True))
    assert page.items[0].raw_metadata["web_discovery_only"] is True
    assert page.items[0].excerpt_html == "<p>Summary 1</p>"
