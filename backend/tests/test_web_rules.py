from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.domain.enums import SourceKind
from app.ingestion.models import SourceScanError, SourceScanRequest
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.web_rules import (
    WebRuleError,
    inspect_web_page,
    parse_web_rule,
    validate_web_rule,
    web_rule_schema,
)
from app.workers.sync import _build_adapter

SOURCE_URL = "https://example.com/blog"
RULE = {
    "id": "example_blog",
    "hosts": ["example.com"],
    "index_paths": ["/blog"],
    "listing": {
        "extraction": {
            "baseSelector": "article",
            "fields": [
                {"name": "url", "selector": "a", "type": "attribute", "attribute": "href"},
                {"name": "title", "selector": "h2", "type": "text"},
            ],
        }
    },
}


@pytest.mark.parametrize(
    "field", ["execute", "article_body_selectors", "prefer_html", "article_path_pattern"]
)
def test_rule_rejects_old_or_executable_fields(field):
    with pytest.raises(WebRuleError, match="extra_forbidden"):
        parse_web_rule({**RULE, field: "old format"}, source_url=SOURCE_URL)


def test_rule_must_cover_source_index():
    with pytest.raises(WebRuleError, match="must include the source host"):
        parse_web_rule({**RULE, "index_paths": ["/news"]}, source_url=SOURCE_URL)


def test_native_listing_is_required():
    with pytest.raises(WebRuleError, match="listing"):
        parse_web_rule(
            {key: value for key, value in RULE.items() if key != "listing"}, source_url=SOURCE_URL
        )


async def test_no_rule_never_fetches_or_falls_back_to_feed_or_sitemap():
    def unexpected(_request):
        pytest.fail("A source without a rule must not issue any request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        adapter = WebBlogSourceAdapter(WebBlogSourceConfig(url=SOURCE_URL), client)
        with pytest.raises(SourceScanError) as exc:
            await adapter.scan_page(
                SourceScanRequest(checkpoint={"feed_url": "https://example.com/feed"})
            )
        assert exc.value.code == "web_rule_required"
        source = SimpleNamespace(
            kind=SourceKind.WEB, canonical_url=SOURCE_URL, config={}, avatar_url=None
        )
        with pytest.raises(SourceScanError) as exc:
            _build_adapter(source, client)
        assert exc.value.code == "web_rule_required"


async def test_worker_uses_native_stored_rule():
    source = SimpleNamespace(
        kind=SourceKind.WEB, canonical_url=SOURCE_URL, config={"web_rule": RULE}, avatar_url=None
    )
    async with httpx.AsyncClient() as client:
        adapter = _build_adapter(source, client)
        assert adapter._rule.id == "example_blog"
        assert adapter._provider_mode == "web_crawl4ai"


async def test_missing_rule_failure_stops_retries_until_authoring(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.domain.enums import SyncPhase
    from app.workers.sync import SourceClaim, _record_scan_failure

    token = uuid4()
    state = SimpleNamespace(lease_token=token, lease_expires_at=None, next_scan_at="scheduled")
    source = SimpleNamespace(
        id=uuid4(), kind=SourceKind.WEB, canonical_url="https://example.com/blog", config={}
    )
    run = SimpleNamespace()
    session = AsyncMock()
    session.scalar.return_value = state
    session.get.side_effect = [source, run]
    enqueue = AsyncMock()
    monkeypatch.setattr("app.workers.sync.ensure_web_rule_job", enqueue)

    @asynccontextmanager
    async def factory():
        yield session

    await _record_scan_failure(
        factory,
        SourceClaim(source.id, token),
        uuid4(),
        SourceScanError("web_rule_required", "Needs native rule", long_lived=True),
    )
    assert state.next_scan_at is None
    assert state.phase == SyncPhase.DEGRADED
    assert state.lease_token is None
    assert state.last_error_code == "web_rule_required"
    enqueue.assert_awaited_once()


async def _validate_pages(monkeypatch, listing, *, rule=RULE, pages=None):
    calls = []

    async def fetch(_client, url, **_kwargs):
        calls.append(url)
        body = (
            "User-agent: *\nAllow: /\n"
            if url.endswith("robots.txt")
            else listing
            if url == SOURCE_URL
            else (pages or {}).get(url, "<html></html>")
        )
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    async with httpx.AsyncClient() as client:
        result = await validate_web_rule(source_url=SOURCE_URL, raw_rule=rule, client=client)
    return result, calls


def _card(index, metadata=""):
    return f'<article><a href="/post-{index}"><h2>Post {index}</h2></a>{metadata}</article>'


def test_rule_schema_documents_consumed_output_names():
    description = web_rule_schema()["description"]
    assert "published_at" in description and "date is not consumed" in description
    assert "image_url" in description and "same-URL batches" in description


@pytest.mark.parametrize("selector,offset", [("footer", 80000), (".missing", 80000)])
async def test_inspection_rejects_offset_from_a_different_selection(monkeypatch, selector, offset):
    async def crawl(*args, **kwargs):
        return SimpleNamespace(
            response=httpx.Response(
                200,
                text="<article>Article</article><footer>Footer</footer>",
                request=httpx.Request("GET", SOURCE_URL),
            ),
            rows=[],
        )

    monkeypatch.setattr("app.ingestion.web_crawl.crawl_page", crawl)
    async with httpx.AsyncClient() as client:
        report = await inspect_web_page(
            source_url=SOURCE_URL, selector=selector, offset=offset, client=client
        )
        assert report["code"] == "invalid_inspection" and report["status"] == "fail"
        assert report["suggested_offset"] == 0
        assert report["total_chars"] < offset
        corrected = await inspect_web_page(
            source_url=SOURCE_URL, selector=selector, offset=0, client=client
        )
    assert corrected["status"] == "pass"
    if selector == "footer":
        assert corrected["html"] == "<footer>Footer</footer>"
        assert corrected["matched_elements"] == 1
    else:
        assert corrected["matched_elements"] == 0
        assert corrected["next_action"] == "refine_selector_or_use_existing_evidence"


async def test_execute_reports_real_rows_without_replay_or_article_coverage_gate(monkeypatch):
    html = (
        _card(1)
        + '<article><a href="/bad"></a></article>'
        + '<a href="/author/alice">Alice</a><a href="/2026/">Archive</a>'
    )
    report, calls = await _validate_pages(monkeypatch, html)
    assert report["status"] == "completed" and report["first_window_completed"]
    assert report["items"] == [{"url": "https://example.com/post-1", "title": "Post 1"}]
    assert report["windows"][0]["rejected_reasons"] == {"missing_required_fields": 1}
    assert calls.count(SOURCE_URL) == 1  # No validator-only independent replay.
    assert not ({"protected_coverage", "replay", "inspection_locators"} & report.keys())


async def test_three_windows_are_real_scanner_batches_not_distinct_page_claim(monkeypatch):
    report, _ = await _validate_pages(monkeypatch, "".join(_card(i) for i in range(50)))
    assert report["status"] == "completed"
    assert len(report["items"]) == 45 and report["completed_windows"] == 3
    assert {w["listing_url"] for w in report["windows"]} == {SOURCE_URL}
    assert report["next_continuation"]["kind"] == "same_listing_window"


@pytest.mark.parametrize("failure", ["http", "timeout"])
async def test_later_failure_preserves_actual_first_window(monkeypatch, failure):
    import asyncio

    from app.ingestion.web_rules import execute_web_rule

    async def fetch(_client, url, **kwargs):
        if url.endswith("robots.txt"):
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", request=httpx.Request("GET", url)
            )
        if url.endswith("/page/2"):
            if failure == "timeout":
                await asyncio.sleep(10)
            return httpx.Response(503, text="Unavailable", request=httpx.Request("GET", url))
        return httpx.Response(
            200,
            text=_card(1) + '<a class="next" href="/page/2">Next</a>',
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    async with httpx.AsyncClient() as client:
        report = await execute_web_rule(
            source_url=SOURCE_URL,
            raw_rule={**RULE, "next_page_selector": ".next"},
            client=client,
            timeout_seconds=0.1 if failure == "timeout" else 2,
        )
    assert report["status"] == "partial" and report["first_window_completed"]
    assert len(report["items"]) == 1 and report["completed_windows"] == 1
    assert report["errors"][0]["window"] == 1
    assert report["errors"][0]["code"] == (
        "TimeoutError" if failure == "timeout" else "web_http_503"
    )


async def test_empty_head_is_actual_scanner_error_with_attempt_id(monkeypatch):
    report, _ = await _validate_pages(monkeypatch, "<html></html>")
    assert report["status"] == "error" and not report["first_window_completed"]
    assert report["execution_id"] and not report["items"]
    assert report["errors"][0]["code"] == "web_structure_changed"


async def test_cancelled_execution_propagates_instead_of_partial_success(monkeypatch):
    import asyncio

    from app.ingestion.web_rules import execute_web_rule

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(WebBlogSourceAdapter, "scan_page", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await execute_web_rule(source_url=SOURCE_URL, raw_rule=RULE)


def test_oversized_execution_facts_keep_authority_and_mark_omitted_evidence():
    from app.ingestion.web_rules import bounded_execution_report

    report = bounded_execution_report(
        {
            "status": "partial",
            "execution_id": "real-attempt",
            "first_window_completed": True,
            "items": [{"title": "Article", "url": "https://example.com/article"}],
            "next_continuation": {"next_url": "https://example.com/" + "x" * 300000},
        }
    )
    assert len(json.dumps(report).encode()) <= 256 * 1024
    assert report["first_window_completed"] and report["status"] == "partial"
    assert report["evidence_error"] == "execution_facts_too_large"
    assert report["omitted_items"] == 1
