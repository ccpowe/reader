"""Native rule codec, DOM inspection and factual production execution."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import soupsieve
from bs4 import BeautifulSoup, Comment, Tag

from app.ingestion.models import SourceScanError, SourceScanRequest
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.url_safety import PublicAsyncClient
from app.ingestion.web_crawl_types import PageRecipe, WebRule


class WebRuleError(ValueError):
    """A submitted rule cannot be executed by Reader."""


def parse_web_rule(raw: object, *, source_url: str) -> WebRule:
    try:
        if len(json.dumps(raw, ensure_ascii=False)) > 60000:
            raise ValueError("rule exceeds 60000 characters")
        rule = WebRule.model_validate(raw)
    except (TypeError, ValueError) as exc:
        raise WebRuleError(str(exc)) from exc
    parsed = urlsplit(source_url)
    if (parsed.hostname or "").rstrip(".").lower() not in rule.hosts or (
        parsed.path.rstrip("/") or "/"
    ) not in {path.rstrip("/") or "/" for path in rule.index_paths}:
        required_location = f"{parsed.hostname}{parsed.path or '/'}"
        raise WebRuleError(f"rule must include the source host and index path: {required_location}")
    return rule


def _continuation_summary(value: dict | None) -> dict | None:
    if not value:
        return None
    return {
        "kind": "same_listing_window" if value.get("same_listing") else "next_page_url",
        "next_url": value.get("next_url"),
    }


def _execution_item(item) -> dict:
    result = {"title": item.title, "url": item.external_url}
    if item.published_at:
        result["published_at"] = item.published_at.isoformat()
    if item.author_name:
        result["author"] = item.author_name
    if item.media:
        result["image_url"] = item.media[0].original_url
    return result


def _window_facts(evidence: dict) -> dict:
    return {
        key: evidence[key]
        for key in (
            "listing_url",
            "extracted_rows",
            "valid_pairs",
            "missing_required_count",
            "rejected_reasons",
            "is_head",
        )
        if key in evidence
    }


def execution_summary(report: dict) -> dict:
    """Keep execution facts independent of item paging and conversation history."""
    return {key: value for key, value in report.items() if key != "items"}


def bounded_execution_report(report: dict, maximum_bytes: int = 256 * 1024) -> dict:
    """Bound stored evidence by removing whole items, never by slicing JSON strings."""
    result = json.loads(json.dumps(report, ensure_ascii=False, default=str))
    result["items"] = list(result.get("items", []))
    total = len(result["items"])
    while result["items"] and len(json.dumps(result, ensure_ascii=False).encode()) > maximum_bytes:
        result["items"].pop()
    if len(result["items"]) != total:
        result["omitted_items"] = total - len(result["items"])
        result["evidence_truncated"] = True
    if len(json.dumps(result, ensure_ascii=False).encode()) > maximum_bytes:
        # A provider may emit an enormous URL even after all items were removed.
        # Preserve execution authority and explicitly omit the oversized evidence.
        result = {
            key: report[key]
            for key in (
                "status",
                "phase",
                "execution_id",
                "first_window_completed",
                "returned_items",
                "completed_windows",
            )
            if key in report
        }
        result.update(
            items=[],
            omitted_items=total,
            evidence_truncated=True,
            evidence_error="execution_facts_too_large",
        )
    return result


async def execute_web_rule(
    *,
    source_url: str,
    raw_rule: object,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = 180,
    browser_identity: dict | None = None,
) -> dict[str, Any]:
    """Return ordinary scanner facts, never a semantic acceptance verdict.

    Continuations retain normal cursor checks. No extra first-window replay or
    inferred article/pagination obligations are introduced here.
    """
    report = {
        "status": "error",
        "phase": "execute",
        "execution_id": None,
        "first_window_completed": False,
        "items": [],
        "windows": [],
        "errors": [],
        "next_continuation": None,
        "scope": {
            "max_windows": 3,
            "max_items_per_window": 15,
            "history": "Only returned windows were sampled; full history is unverified.",
        },
    }
    try:
        rule = parse_web_rule(raw_rule, source_url=source_url)
    except WebRuleError as exc:
        report["errors"] = [{"code": "WebRuleError", "message": str(exc)[:2000], "window": 0}]
        return report
    report["execution_id"] = str(uuid4())
    active_client = client or PublicAsyncClient(follow_redirects=True)
    adapter = WebBlogSourceAdapter(
        WebBlogSourceConfig(url=source_url, rule=rule, browser_identity=browser_identity),
        active_client,
    )
    continuation = None
    window = 0
    try:
        async with asyncio.timeout(timeout_seconds):
            for window in range(3):
                page = await adapter.scan_page(
                    SourceScanRequest(
                        initial=False,
                        conditional=False,
                        max_raw_items=15,
                        continuation=continuation,
                    )
                )
                if window == 0:
                    report["first_window_completed"] = True
                report["items"].extend(_execution_item(item) for item in page.items)
                report["windows"].append(
                    {
                        "window": window,
                        **_window_facts(page.web_listing_evidence),
                        "returned_items": len(page.items),
                        "provider_mode": page.provider_mode,
                        "request_continuation": _continuation_summary(continuation),
                        "next_continuation": _continuation_summary(page.next_continuation),
                    }
                )
                continuation = page.next_continuation
                report["next_continuation"] = _continuation_summary(continuation)
                if not continuation:
                    break
        report["status"] = "completed"
    except (SourceScanError, httpx.HTTPError, ValueError, TimeoutError) as exc:
        failure = web_rule_failure_feedback(exc, phase="execute")
        report["errors"].append(
            {
                "code": failure["code"],
                "message": failure["message"],
                "window": window,
                "retryable": failure["retryable"] or isinstance(exc, TimeoutError),
                **_window_facts(getattr(exc, "evidence", {})),
            }
        )
        report["status"] = "partial" if report["first_window_completed"] else "error"
    finally:
        if client is None:
            await active_client.aclose()
    report["returned_items"] = len(report["items"])
    report["completed_windows"] = len(report["windows"])
    return report


async def validate_web_rule(**kwargs) -> dict[str, Any]:
    """Compatibility name for factual execution, without pass/fail verdicts."""
    return await execute_web_rule(**kwargs)


def web_rule_schema() -> dict[str, Any]:
    schema = WebRule.model_json_schema()
    schema["description"] = (
        "New rules require listing.extraction: a Crawl4AI JsonCssExtractionStrategy native schema. "
        "Required listing output fields: url (attribute) and title (text). "
        "Optional consumed fields: published_at (a parseable date; "
        "configure date_formats if needed), "
        "author, excerpt or summary "
        "(plain text), image_url. A field named date is not consumed as published_at. "
        "The article recipe is accepted only for legacy rule compatibility and is not executed. "
        "Other top-level extracted fields are not consumed by Reader. "
        "next_page_selector selects a real next-page link; "
        "same-URL batches are not website pagination. "
        "Bounded click/wait/scroll actions can load content, "
        "but their presence alone does not prove load-more coverage. "
        "Reading mode is extracted from the user's current WebView. "
        "Only native Crawl4AI schemas are accepted."
    )
    schema["required"] = list(dict.fromkeys([*schema.get("required", []), "listing"]))
    return schema


def _compact_inspection_dom(soup: BeautifulSoup) -> None:
    """Remove transport/decoration noise only after selectors matched the real DOM."""
    for node in soup.find_all(["script", "style", "svg", "path"]):
        if node.name is not None:
            node.decompose()
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for node in soup.find_all(True):
        for name, value in list(node.attrs.items()):
            # Class/id remain exact so the agent can use them as real selectors.
            # Long opaque data/style/image payloads do not help locate article cards.
            if name in {"class", "id"}:
                continue
            encoded = " ".join(value) if isinstance(value, list) else str(value)
            if (
                name == "style"
                or name.startswith("on")
                or name in {"srcset", "imagesrcset"}
                or encoded.startswith("data:")
                or len(encoded) > (240 if name.startswith("data-") else 2000)
            ):
                del node.attrs[name]
    for value in list(soup.find_all(string=True)):
        compacted = re.sub(r"\s+", " ", str(value))
        if compacted != str(value):
            value.replace_with(compacted)


def _inspection_locator(node: Tag) -> dict[str, Any]:
    """Small, explicitly partial locator hints; never synthesize a CSS selector."""
    result: dict[str, Any] = {"tag": node.name}
    identifier = str(node.get("id") or "")
    if identifier and len(identifier) <= 200:
        result["id"] = identifier
    classes = list(node.get("class") or [])
    kept = [name for name in classes[:8] if len(name) <= 100]
    if kept:
        result["classes"] = kept
    if len(kept) < len(classes) or len(identifier) > 200:
        result["locator_truncated"] = True
    return result


def _inspection_node_context(nodes: list[Tag]) -> tuple[list[dict], list[Tag]]:
    """Show a few real parent cards when overlay anchors have no own title text."""
    live_nodes = [(index, node) for index, node in enumerate(nodes) if node.name]
    # Empty overlay links need sibling evidence most. This is a sample of the
    # selected nodes, not an alternate interpretation of extraction selectors.
    live_nodes.sort(key=lambda row: not (row[1].name == "a" and not row[1].get_text(strip=True)))
    contexts: list[dict] = []
    parent_scopes: list[Tag] = []
    remaining_parent_bytes = 3000
    remaining_context_bytes = 5000 - 2  # JSON list brackets.
    sampled_parents: set[int] = set()
    for index, node in live_nodes[:3]:
        ancestors = []
        parent = node.parent
        content_parent = None
        for _ in range(4):
            if not isinstance(parent, Tag) or parent.name in {"[document]", "html", "body"}:
                break
            ancestors.append(_inspection_locator(parent))
            if content_parent is None and parent.get_text(" ", strip=True):
                content_parent = parent
            parent = parent.parent
        context: dict[str, Any] = {
            "matched_index": index,
            "node": _inspection_locator(node),
            "ancestors_nearest_first": ancestors,
        }
        if (
            node.name == "a"
            and not node.get_text(" ", strip=True)
            and content_parent is not None
            and id(content_parent) not in sampled_parents
            and remaining_parent_bytes > 0
        ):
            parent_html = str(content_parent)
            shown_html = parent_html.encode("utf-8")[: min(remaining_parent_bytes, 1500)].decode(
                "utf-8", errors="ignore"
            )
            context["parent_sample"] = {
                "node": _inspection_locator(content_parent),
                "html": shown_html,
                "total_chars": len(parent_html),
                "truncated": len(shown_html) < len(parent_html),
                "next_action": "inspect_parent_selector_for_more_context",
            }
            sampled_parents.add(id(content_parent))

        def context_bytes() -> int:
            return len(json.dumps(context, ensure_ascii=False).encode("utf-8")) + 2

        # Keep the extra evidence within a small UTF-8 budget, including JSON
        # escaping and long class names. Prefer the nearest parent sample over
        # distant ancestor hints; all original markup is still pageable above.
        while context_bytes() > remaining_context_bytes and context["ancestors_nearest_first"]:
            context["ancestors_nearest_first"].pop()
        sample = context.get("parent_sample")
        if sample:
            while context_bytes() > remaining_context_bytes and sample["html"]:
                sample["html"] = sample["html"][: len(sample["html"]) // 2]
                sample["truncated"] = True
        if context_bytes() > remaining_context_bytes:
            break
        if sample:
            remaining_parent_bytes -= len(sample["html"].encode("utf-8"))
            # Link evidence follows exactly the displayed parent sample. A
            # truncated sample must not authorize unseen sibling links.
            if not sample["truncated"]:
                parent_scopes.append(content_parent)
        remaining_context_bytes -= context_bytes()
        contexts.append(context)
    return contexts, parent_scopes


def _inspection_links(scopes: list[Tag]) -> tuple[list[dict[str, str]], int]:
    links = []
    seen: set[int] = set()
    total = 0
    for scope in scopes:
        if not scope.name:
            continue
        anchors = ([scope] if scope.name == "a" and scope.has_attr("href") else []) + list(
            scope.select("a[href]")
        )
        for anchor in anchors:
            if id(anchor) in seen:
                continue
            seen.add(id(anchor))
            total += 1
            if len(links) < 80:
                links.append(
                    {
                        "url": str(anchor.get("href") or "")[:2000],
                        "title": anchor.get_text(" ", strip=True)[:500],
                    }
                )
    return links, total


async def inspect_web_page(
    *,
    source_url: str,
    render_js: bool = False,
    actions: list[dict] | None = None,
    selector: str | None = None,
    offset: int = 0,
    limit: int = 16000,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch bounded, untrusted page evidence through the production engine."""
    from app.ingestion.http import raise_for_provider_status
    from app.ingestion.web_crawl import crawl_page
    from app.ingestion.web_crawl_types import INSPECTION_RECIPE

    if not 0 <= offset <= 5_000_000 or not 1 <= limit <= 30000:
        raise WebRuleError("offset must be 0..5000000 and limit 1..30000")
    recipe = PageRecipe.model_validate(
        {**INSPECTION_RECIPE, "render_js": render_js, "actions": actions or []}
    )
    if selector:
        if len(selector) > 400:
            raise WebRuleError("selector exceeds 400 characters")
        try:
            soupsieve.compile(selector)
        except soupsieve.SelectorSyntaxError as exc:
            raise WebRuleError(str(exc)) from exc
    owns_client = client is None
    active_client = client or PublicAsyncClient(follow_redirects=True)
    try:
        adapter = WebBlogSourceAdapter(WebBlogSourceConfig(url=source_url), active_client)
        page = await crawl_page(
            active_client,
            source_url,
            recipe,
            maximum_bytes=5 * 1024 * 1024,
            robots_allows=adapter._robots_allows,
        )
        raise_for_provider_status(page.response, "web")
        raw_html = page.response.text
        soup = BeautifulSoup(raw_html, "html.parser")
        # Selection precedes evidence compaction. Rules always execute against
        # Crawl4AI's untouched page; discarded attributes cannot change matches.
        nodes = list(soup.select(selector)) if selector else [soup]
        matched_elements = len(nodes) if selector else None
        raw_total_chars = sum(len(str(node)) for node in nodes) if selector else len(raw_html)
        _compact_inspection_dom(soup)
        html_text = "\n".join(str(node) for node in nodes if node.name)
        node_context, parent_scopes = _inspection_node_context(nodes) if selector else ([], [])
        links, total_links = _inspection_links([*nodes, *parent_scopes])
        if offset and offset >= len(html_text):
            return {
                "status": "fail",
                "code": "invalid_inspection",
                "retryable": False,
                "phase": "inspect",
                "next_action": "reset_offset_or_refine_selector",
                "url": str(page.response.url),
                "selector": selector,
                "offset": offset,
                "total_chars": len(html_text),
                "matched_elements": matched_elements,
                "suggested_offset": 0,
                "message": (
                    "Offset is outside the selected markup. Offsets are relative to the "
                    "current selector, not the full page or a previous selection. Reset "
                    "offset to 0 when changing selector/page; if matched_elements is 0, "
                    "refine the selector. Only use next_offset from the same selection."
                ),
            }
        return {
            "status": "pass",
            "code": "web_page_inspected",
            "retryable": False,
            "phase": "inspect",
            "next_action": (
                "refine_selector_or_use_existing_evidence"
                if matched_elements == 0
                else "write_or_refine_native_rule"
            ),
            "url": str(page.response.url),
            "engine": "crawl4ai",
            "content_is_untrusted": True,
            "evidence_mode": "compact_dom",
            "selector": selector,
            "offset": offset,
            "matched_elements": matched_elements,
            "html": html_text[offset : offset + limit],
            "total_chars": len(html_text),
            "next_offset": offset + limit if offset + limit < len(html_text) else None,
            "links": links,
            "total_links": total_links,
            "evidence": {
                "http_status": page.response.status_code,
                "extracted_rows": len(page.rows),
                "raw_total_chars": raw_total_chars,
                "requested_render_js": render_js,
                "render_js": recipe.render_js,
                "actions": [
                    {key: action[key] for key in ("kind", "selector") if key in action}
                    for action in actions or []
                ],
                "node_context": node_context,
                "omitted_markup": "scripts, styles, SVG/path, comments and decorative attributes",
            },
        }
    except (SourceScanError, httpx.HTTPError, ValueError) as exc:
        return web_rule_failure_feedback(exc, phase="inspect")
    finally:
        if owns_client:
            await active_client.aclose()


def web_rule_failure_feedback(exc: Exception, *, phase: str) -> dict[str, Any]:
    """Bounded provider evidence; retrying network errors is not rule repair."""
    code = str(getattr(exc, "code", type(exc).__name__))
    structural = code in {
        "web_structure_changed",
        "web_rule_missing_fields",
        "web_rule_unstable",
        "web_rule_action_failed",
    }
    retryable = (
        isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
        or code
        in {
            "source_timeout",
            "rate_limited",
            "upstream_error",
            "web_rate_limited",
            "web_upstream_error",
        }
        or code == "web_http_429"
        or code.startswith("web_http_5")
    )
    return {
        "status": "fail",
        "code": code,
        "message": str(exc)[:2000],
        "retryable": retryable,
        "phase": phase,
        "evidence": getattr(exc, "evidence", {}),
        "next_action": (
            "inspect_page_and_fix_actions"
            if code == "web_rule_action_failed"
            else "inspect_listing_and_fix_required_fields"
            if structural
            else "retry_after_backoff"
            if retryable
            else "inspect_failure_before_retry"
        ),
    }
