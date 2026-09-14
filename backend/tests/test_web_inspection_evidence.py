"""Inspection should reveal reusable card structure without decoration noise."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.ingestion.web_rules import inspect_web_page

SOURCE_URL = "https://example.com/blog"


@pytest.fixture
def inspect_markup(monkeypatch):
    async def inspect(html, **kwargs):
        async def crawl(*args, **crawl_kwargs):
            return SimpleNamespace(
                response=httpx.Response(200, text=html, request=httpx.Request("GET", SOURCE_URL)),
                # Deliberately unrelated page-wide extraction: scoped evidence
                # must come from the real selected DOM, never this global list.
                rows=[{"url": "/unrelated", "title": "Navigation"}],
            )

        monkeypatch.setattr("app.ingestion.web_crawl.crawl_page", crawl)
        async with httpx.AsyncClient() as client:
            return await inspect_web_page(source_url=SOURCE_URL, client=client, **kwargs)

    return inspect


async def test_decorations_do_not_push_card_titles_beyond_first_slice(inspect_markup):
    opaque = "M0 0 " * 8000
    html = (
        '<article class="post-card" id="release" style="display: block"'
        f' data-animation="{opaque}"><svg><path d="{opaque}"/></svg>'
        f"<script>{opaque}</script><style>{opaque}</style><!-- {opaque} -->"
        '<a href="/blog/release"><h2>Visible release title</h2></a>'
        '<time datetime="2026-09-10">September 10</time></article>'
    )
    report = await inspect_markup(html, selector='article[style="display: block"]', limit=1000)

    assert report["status"] == "pass"
    assert report["matched_elements"] == 1  # Match happens before removing style.
    assert "Visible release title" in report["html"]
    assert 'class="post-card"' in report["html"]
    assert 'id="release"' in report["html"]
    assert 'datetime="2026-09-10"' in report["html"]
    assert all(noise not in report["html"] for noise in ("<svg", "<path", "<script", "<style"))
    assert "data-animation" not in report["html"]
    assert report["next_offset"] is None
    assert report["evidence"]["raw_total_chars"] > 100_000
    assert report["total_chars"] < 1000


async def test_empty_overlay_link_exposes_its_real_card_and_title_sibling(inspect_markup):
    html = (
        '<main><div class="posts"><article class="post-card" id="release">'
        '<a class="cover-link" href="/blog/release"></a>'
        '<div class="copy"><h2>Title beside the overlay</h2>'
        "<p>Short description</p></div></article></div></main>"
    )
    report = await inspect_markup(html, selector="a.cover-link")

    assert report["matched_elements"] == 1
    assert "Title beside" not in report["html"]  # Still only the matched anchor.
    context = report["evidence"]["node_context"][0]
    assert context["matched_index"] == 0
    assert context["ancestors_nearest_first"][0] == {
        "tag": "article",
        "id": "release",
        "classes": ["post-card"],
    }
    parent = context["parent_sample"]
    assert parent["truncated"] is False
    assert parent["total_chars"] == len(parent["html"])
    assert "<h2>Title beside the overlay</h2>" in parent["html"]
    assert 'class="cover-link"' in parent["html"]
    assert report["links"] == [{"url": "/blog/release", "title": ""}]


async def test_selected_card_links_exclude_unrelated_navigation_and_other_cards(inspect_markup):
    html = (
        '<nav><a href="/account">Account</a></nav>'
        '<article id="first"><a href="/blog/first">First post</a></article>'
        '<article id="second"><a href="/blog/second">Second post</a></article>'
    )
    scoped = await inspect_markup(html, selector="#first, #first a")
    assert scoped["links"] == [{"url": "/blog/first", "title": "First post"}]
    assert scoped["total_links"] == 1  # Overlapping selectors do not duplicate links.
    page = await inspect_markup(html)
    assert page["total_links"] == 3
    assert {row["url"] for row in page["links"]} == {"/account", "/blog/first", "/blog/second"}


async def test_inspection_slices_share_the_same_compact_markup_offsets(inspect_markup):
    html = (
        '<article data-decoration="'
        + "x" * 3000
        + '">'
        + "".join(f'<a href="/blog/{index}">标题 {index}</a>' for index in range(12))
        + "</article>"
    )
    full = await inspect_markup(html, selector="article")
    offset = 0
    slices = []
    while True:
        report = await inspect_markup(html, selector="article", offset=offset, limit=47)
        assert report["total_chars"] == len(full["html"])
        assert report["offset"] == offset
        slices.append(report["html"])
        if report["next_offset"] is None:
            break
        assert report["next_offset"] == offset + len(report["html"])
        offset = report["next_offset"]
    assert "".join(slices) == full["html"]


async def test_missing_selector_has_no_page_wide_link_fallback(inspect_markup):
    html = '<a href="/blog/first">First post</a>'
    report = await inspect_markup(html, selector=".missing")
    assert report["matched_elements"] == 0
    assert report["html"] == "" and report["total_chars"] == 0
    assert report["next_offset"] is None
    assert report["links"] == [] and report["total_links"] == 0
    assert report["evidence"]["node_context"] == []
    assert report["next_action"] == "refine_selector_or_use_existing_evidence"
    invalid = await inspect_markup(html, selector=".missing", offset=50)
    assert invalid["status"] == "fail" and invalid["code"] == "invalid_inspection"
    assert invalid["suggested_offset"] == 0


async def test_actions_report_effective_browser_mode_without_explicit_render_flag(inspect_markup):
    report = await inspect_markup(
        '<article><a href="/one">One</a></article>',
        actions=[{"kind": "click", "selector": ".more"}],
    )
    assert report["evidence"]["requested_render_js"] is False
    assert report["evidence"]["render_js"] is True


async def test_context_does_not_add_unseen_links_from_truncated_parent(inspect_markup):
    html = (
        '<article class="card"><a class="overlay" href="/blog/first"></a>'
        "<h2>First title</h2><p>"
        + "Long visible description. " * 200
        + '</p><a href="/unseen">Late sibling link</a></article>'
    )
    report = await inspect_markup(html, selector=".overlay")
    sample = report["evidence"]["node_context"][0]["parent_sample"]
    assert sample["truncated"] is True
    assert sample["total_chars"] > len(sample["html"])
    assert "/unseen" not in sample["html"]
    assert report["links"] == [{"url": "/blog/first", "title": ""}]


async def test_empty_links_are_prioritized_in_context_samples(inspect_markup):
    html = (
        "".join(f'<a href="/nav/{index}">Navigation {index}</a>' for index in range(6))
        + '<article><a class="overlay" href="/blog/latest"></a><h2>Latest title</h2></article>'
    )
    report = await inspect_markup(html, selector="a")
    context = report["evidence"]["node_context"][0]
    assert context["matched_index"] == 6
    assert "Latest title" in context["parent_sample"]["html"]
    assert len(report["evidence"]["node_context"]) <= 3


async def test_extra_context_has_a_small_utf8_budget_even_with_long_locator_names(inspect_markup):
    classes = " ".join(f"class-{index}-" + "x" * 80 for index in range(8))
    html = "".join(
        f'<section class="{classes}"><div class="{classes}"><article class="{classes}">'
        f'<a class="overlay {classes}" href="/blog/{index}"></a>'
        f"<h2>中文标题 {index}</h2><p>" + "内容" * 300 + "</p></article></div></section>"
        for index in range(4)
    )
    report = await inspect_markup(html, selector="a.overlay")
    contexts = report["evidence"]["node_context"]
    assert contexts  # Bounds must not discard all of the most relevant context.
    assert "parent_sample" in contexts[0]
    assert len(json.dumps(contexts, ensure_ascii=False).encode("utf-8")) <= 5000
    assert report["matched_elements"] == 4
    # Locator hints can be partial; selector HTML retains the actual classes.
    assert classes in report["html"]
