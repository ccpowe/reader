"""DOM evidence regressions without starting a browser or making network calls."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from bs4 import BeautifulSoup

from app.core.settings import Settings
from app.ingestion.models import SourceScanError
from app.ingestion.web_crawl import CrawledPage
from app.ingestion.web_structure import article_structure
from app.web_rule_agent import browser

URL = "https://example.com/blog"


@pytest.fixture
def pages(monkeypatch):
    state = {"calls": [], "html": "<main><a href='/blog/one'><h2>One</h2></a></main>"}

    async def crawl(client, url, recipe, **options):
        assert not client.is_closed
        state["calls"].append({"url": url, "recipe": recipe, **options})
        return CrawledPage(
            httpx.Response(
                200, text=state["html"], request=httpx.Request("GET", state.get("final_url", url))
            ),
            [],
        )

    monkeypatch.setattr(browser, "crawl_page", crawl)
    return state


async def test_node_indexes_are_not_character_offsets_and_empty_headings_remain_visible(pages):
    pages["html"] = (
        "<main>"
        + "".join(
            f'<a href="/blog/{index}"><h2>'
            f"{'' if index in {81, 82, 83} else f'Title {index}'}</h2></a>"
            for index in range(116)
        )
        + "</main>"
    )
    session = browser.SourceBrowserSession(Settings(_env_file=None), URL)
    result = await session.inspect(URL, selector='a[href^="/blog/"]', node_start=81, node_limit=3)
    assert result["status"] == "pass"
    assert result["total_nodes"] == 116
    assert [node["index"] for node in result["nodes"]] == [81, 82, 83]
    assert all(node["headings"][0]["text"] == "" for node in result["nodes"])
    assert result["next_node_start"] == 84
    assert "coverage_seed_complete" not in result


async def test_malformed_html_inspection_matches_native_extraction_node_order(pages):
    from crawl4ai import JsonCssExtractionStrategy

    # Missing </li> tags nest nodes under html.parser, while native lxml
    # repairs them into siblings. The validation locator must remain usable.
    pages["html"] = (
        "<main><ul>"
        "<li id='one'><a href='/blog/one'><h2>One</h2></a>"
        "<li id='missing'><a href='/blog/missing'><h2></h2></a>"
        "<li id='three'><a href='/blog/three'><h2>Three</h2></a>"
        "</ul></main>"
    )
    selector = "ul > li"
    native = JsonCssExtractionStrategy(
        {
            "baseSelector": selector,
            "baseFields": [{"name": "id", "type": "attribute", "attribute": "id"}],
            "fields": [],
        }
    ).extract(URL, pages["html"])
    assert [row["id"] for row in native] == ["one", "missing", "three"]
    session = browser.SourceBrowserSession(Settings(_env_file=None), URL)
    report = await session.inspect(URL, selector=selector, node_limit=3, mode="nodes")
    assert report["total_nodes"] == len(native)
    assert [(node["index"], node["attributes"]["id"]) for node in report["nodes"]] == [
        (index, row["id"]) for index, row in enumerate(native)
    ]
    assert report["dom_revision"] == hashlib.sha256(pages["html"].encode("utf-8")).hexdigest()
    missing = await session.inspect(URL, selector=selector, node_start=1, node_limit=1)
    assert missing["nodes"][0]["attributes"]["id"] == "missing"
    assert missing["nodes"][0]["headings"][0]["text"] == ""
    assert missing["dom_revision"] == report["dom_revision"]


async def test_overlay_link_preserves_parent_title_and_original_selector(pages):
    pages["html"] = (
        '<main><section data-marker="'
        + "x" * 600
        + '"><h2>Sibling title</h2><a href="/blog/a"></a></section></main>'
    )
    session = browser.SourceBrowserSession(Settings(_env_file=None), URL)
    report = await session.inspect(URL, selector="section[data-marker] > a")
    assert report["total_nodes"] == 1
    assert report["nodes"][0]["text"] == ""
    assert report["nodes"][0]["parent"]["text"] == "Sibling title"


async def test_unicode_json_bound_retains_actual_node_and_next_cursor(pages):
    pages["html"] = (
        "<main>"
        + "".join(
            f'<a class="{"字" * 300}" href="/blog/{i}"><h2>{"正文" * 2000}</h2></a>'
            for i in range(8)
        )
        + "</main>"
    )
    session = browser.SourceBrowserSession(
        Settings(_env_file=None, web_rule_agent_max_tool_result_bytes=1024), URL
    )
    report = await session.inspect(URL, selector="a", node_start=3)
    assert len(json.dumps(report, ensure_ascii=False).encode()) <= 1024
    assert report["status"] == "pass"
    assert report["truncated"] is True
    assert report["nodes"][0]["index"] == 3
    assert report["next_node_start"] == report["nodes"][-1]["index"] + 1


async def test_inspection_cache_recipe_revision_and_close(pages):
    session = browser.SourceBrowserSession(Settings(_env_file=None), URL)
    first = await session.inspect(URL)
    second = await session.inspect(URL, selector="a", node_limit=1)
    assert second["cache_hit"] is True
    assert first["page_revision"] == second["page_revision"]
    assert len(pages["calls"]) == 1
    changed = await session.inspect(URL, actions=[{"kind": "wait", "selector": "a"}])
    assert changed["page_revision"] != first["page_revision"]
    assert pages["calls"][-1]["recipe"].render_js is True
    assert pages["calls"][-1]["browser_engine"] == "lightpanda"
    await session.aclose()
    assert (await session.inspect(URL))["code"] == "browser_session_closed"


@pytest.mark.parametrize("text", ["A" * 500, "下一组" * 150])
async def test_final_bound_keeps_redirect_base_raw_href_and_issued_page_ref(pages, text):
    from urllib.parse import urljoin

    pages["final_url"] = URL + "/archive/"
    pages["html"] = (
        "<main><section><article><h2>One</h2><a href='/blog/one'>One</a></article>"
        f"<div><a href='page/2' class='advance'>{text}</a></div></section></main>"
    )
    session = browser.SourceBrowserSession(
        Settings(_env_file=None, web_rule_agent_max_tool_result_bytes=1024), URL
    )
    report = await session.inspect(URL, node_limit=1)
    report.update(phase="inspect", retryable=False, evidence_id="evidence-" + "a" * 64)
    report = browser.bound_inspection_report(report, 1024)
    node = report["nodes"][0]
    if node.get("attributes", {}).get("href"):
        node["page_ref"] = "page-123"
    else:
        node["links"][0]["page_ref"] = "page-123"
    final = browser.bound_inspection_report(report, 1024)
    assert final["status"] == "pass" and browser._json_bytes(final) <= 1024
    node = final["nodes"][0]
    if node.get("attributes", {}).get("href"):
        assert node["attributes"]["href"] == "page/2"
        assert final["url"] == URL + "/archive/"
        assert node["page_ref"] == "page-123"
        target = urljoin(final["url"], node["attributes"]["href"])
    else:
        target = node["links"][0]["url"]
        assert node["links"][0]["page_ref"] == "page-123"
    assert target == URL + "/archive/page/2"
    assert final["evidence_id"] == report["evidence_id"]
    assert final["dom_revision"] == report["dom_revision"]
    assert final["next_node_start"] is None


def test_complete_navigation_unit_too_large_does_not_advance_cursor():
    report = {
        "status": "pass",
        "code": "web_page_inspected",
        "phase": "inspect",
        "retryable": False,
        "evidence_id": "evidence-1",
        "page_revision": "a" * 64,
        "dom_revision": "b" * 64,
        "url": URL,
        "evidence_mode": "nodes",
        "node_start": 7,
        "total_nodes": 9,
        "nodes": [
            {
                "index": 7,
                "tag": "a",
                "text": "Continue",
                "page_ref": "page-1",
                "attributes": {"href": "/blog/" + "x" * 2000},
            }
        ],
    }
    final = browser.bound_inspection_report(report, 1024)
    assert final["code"] == "inspection_output_too_large"
    assert final["nodes"] == [] and final["next_node_start"] == 7
    assert browser._json_bytes(final) <= 1024


def test_bound_handles_a_malformed_dom_href_without_navigation_authority():
    report = {
        "status": "pass",
        "code": "web_page_inspected",
        "url": URL,
        "evidence_mode": "nodes",
        "node_start": 0,
        "total_nodes": 1,
        "nodes": [
            {
                "index": 0,
                "tag": "a",
                "text": "Evidence " * 300,
                "attributes": {"href": "http://[invalid", "class": "advance"},
            }
        ],
    }
    final = browser.bound_inspection_report(report, 1024)
    assert final["status"] == "pass" and browser._json_bytes(final) <= 1024
    assert final["nodes"][0]["index"] == 0
    assert not final["nodes"][0].get("links")
    assert "page_ref" not in final["nodes"][0]


async def test_execution_invalidation_refetches_and_keeps_actual_redirect_base(pages):
    pages["final_url"] = URL + "/"
    session = browser.SourceBrowserSession(Settings(_env_file=None), URL)
    before = await session.inspect(URL)
    assert before["url"] == URL + "/"
    session.invalidate()
    pages["html"] = '<main><a href="/blog/new"><h2>New</h2></a></main>'
    after = await session.inspect(URL)
    assert after["cache_hit"] is False
    assert after["page_revision"] != before["page_revision"]
    assert len(pages["calls"]) == 2


@pytest.mark.parametrize(
    "options",
    [
        {"node_start": -1},
        {"node_start": True},
        {"node_limit": 6},
        {"selector": "["},
        {"mode": "eval"},
        {"actions": [{"kind": "eval", "script": "steal()"}]},
    ],
)
async def test_invalid_operations_do_not_start_network(pages, options):
    session = browser.SourceBrowserSession(Settings(_env_file=None), URL)
    result = await session.inspect(URL, **options)
    assert result["code"] == "invalid_inspection"
    assert pages["calls"] == []


async def test_source_bound_url_and_out_of_range_index(pages):
    session = browser.SourceBrowserSession(Settings(_env_file=None), URL)
    assert (await session.inspect("https://other.example/blog"))["code"] == "invalid_inspection"
    assert (await session.inspect("file:///etc/passwd"))["status"] == "fail"
    assert pages["calls"] == []
    assert (await session.inspect(URL, selector="a", node_start=99))["code"] == "invalid_inspection"
    assert (await session.inspect(URL, selector="not-present"))["total_nodes"] == 0


async def test_safe_transport_failure_remains_observable(pages, monkeypatch):
    async def fail(*_args, **_kwargs):
        raise SourceScanError("browser_redirect_unsupported", "Cannot replay this redirect.")

    monkeypatch.setattr(browser, "crawl_page", fail)
    session = browser.SourceBrowserSession(Settings(_env_file=None), URL)
    result = await session.inspect(URL, render_js=True)
    assert result["status"] == "fail" and result["code"] == "browser_redirect_unsupported"


def test_heading_free_link_container_is_not_rescanned_per_anchor(monkeypatch):
    from bs4 import Tag

    soup = BeautifulSoup(
        "<main><section>"
        + "".join(f'<a href="/blog/{i}">Label {i}</a>' for i in range(400))
        + "</section></main>",
        "html.parser",
    )
    calls = []
    original = Tag.find

    def find(node, *args, **kwargs):
        calls.append(id(node))
        return original(node, *args, **kwargs)

    monkeypatch.setattr(Tag, "find", find)
    browser._article_structure(soup)
    assert calls == []  # Classification uses one indexed pass, no subtree queries per link.


def test_shared_card_boundaries_distinguish_navigation_from_article_content():
    soup = BeautifulSoup(
        "<main><section id='collection'><article><header>"
        "<a id='entry' href='/blog/one'><h2></h2></a></header></article>"
        "<nav><a id='continuation' href='/blog/continue'>→</a></nav>"
        "</section></main>",
        "lxml",
    )
    structure = article_structure(soup)
    entry = structure[id(soup.select_one("#entry"))]
    continuation = structure[id(soup.select_one("#continuation"))]
    assert entry.in_article and entry.in_card and not entry.excluded
    assert structure[id(soup.select_one("#collection"))].contains_card
    # Discovery excludes navigation articles, but continuation detection can
    # still inspect this non-card control under a collection with known cards.
    assert continuation.excluded and not continuation.in_card
    assert browser._article_structure is article_structure


@pytest.mark.parametrize("node_limit,maximum_bytes", [(1, 1024), (5, 8192)])
@pytest.mark.parametrize(
    "cards",
    [
        "<li><article><h2>One</h2><a href='/blog/one'></a></article></li>"
        "<li><article><h2></h2><a href='/blog/two'></a></article></li>",
        "<li><a href='/blog/one'><h2>One</h2><h3>Subtitle</h3></a></li>"
        "<li><a href='/blog/two'><h2></h2><h3></h3></a></li>",
    ],
)
async def test_fresh_overview_keeps_unverified_control(pages, node_limit, maximum_bytes, cards):
    pages["html"] = (
        f"<main><h1>Updates</h1><section><div><ul>{cards}"
        "</ul></div><div class='flex justify-center'>"
        "<a role='link' href='/blog/continue?window=2'>下一组</a></div>"
        "</section></main><div><span><button></button></span></div>"
    )
    session = browser.SourceBrowserSession(
        Settings(_env_file=None, web_rule_agent_max_tool_result_bytes=maximum_bytes), URL
    )
    report = await session.inspect(URL, node_limit=node_limit)
    assert len(json.dumps(report, ensure_ascii=False).encode()) <= maximum_bytes
    assert report["status"] == "pass" and len(report["nodes"]) <= node_limit
    control = report["nodes"][0]
    assert control["index"] == 8 and control["text"] == "下一组"
    assert control["observation"] == "unverified_content_control"
    if maximum_bytes > 1024:
        assert control["attributes"]["href"] == "/blog/continue?window=2"
        assert any(link["url"] == URL + "/continue?window=2" for link in report["links"])
    detail = await session.inspect(URL, node_start=control["index"], node_limit=1, mode="nodes")
    assert detail["nodes"][0]["text"] == "下一组"
    assert detail["dom_revision"] == report["dom_revision"]


def test_final_inspection_bound_includes_host_metadata_and_actual_cursor():
    report = {
        "status": "pass",
        "code": "web_page_inspected",
        "phase": "inspect",
        "retryable": False,
        "evidence_id": "evidence-123",
        "page_revision": "a" * 64,
        "dom_revision": "c" * 64,
        "recipe_id": "b" * 16,
        "evidence_mode": "nodes",
        "node_start": 0,
        "total_nodes": 3,
        "nodes": [{"index": i, "tag": "h2", "text": "正文" * 120} for i in range(3)],
        "next_node_start": 3,
    }
    bounded = browser.bound_inspection_report(report, 1024)
    assert len(json.dumps(bounded, ensure_ascii=False).encode()) <= 1024
    assert bounded["evidence_id"] == report["evidence_id"]
    assert bounded["dom_revision"] == report["dom_revision"]
    assert bounded["nodes"] and bounded["nodes"][0]["text"]
    assert bounded["next_node_start"] == bounded["nodes"][-1]["index"] + 1

    failure = browser.bound_inspection_report({**report, "opaque_metadata": "x" * 2000}, 1024)
    assert len(json.dumps(failure).encode()) <= 1024
    assert failure["code"] == "inspection_output_too_large"
    assert failure["dom_revision"] == report["dom_revision"]
    assert failure["nodes"] == [] and failure["next_node_start"] == 0


@pytest.mark.parametrize(
    "options,dynamic",
    [
        ({}, False),
        ({"render_js": True}, True),
        ({"actions": [{"kind": "wait", "selector": "a"}]}, True),
    ],
)
async def test_inspection_uses_shared_lightpanda_configuration_and_reports_actual_engine(
    pages, options, dynamic
):
    settings = Settings(
        _env_file=None, ingestion_lightpanda_executable_path="/opt/reader/lightpanda"
    )
    session = browser.SourceBrowserSession(settings, URL)
    report = await session.inspect(URL, **options)
    assert report["status"] == "pass"
    call = pages["calls"][-1]
    assert call["browser_engine"] == "lightpanda"
    assert call["lightpanda_executable_path"] == "/opt/reader/lightpanda"
    assert call["recipe"].render_js is dynamic
    assert report["engine"] == ("lightpanda" if dynamic else "http")
    if dynamic:
        assert call["browser_identity"]["engine"] == "lightpanda"
    else:
        assert call["browser_identity"] is None
    cached = await session.inspect(URL, **options)
    assert cached["cache_hit"] is True and len(pages["calls"]) == 1
    assert cached["dom_revision"] == report["dom_revision"]
