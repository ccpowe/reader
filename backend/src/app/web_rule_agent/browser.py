"""Bounded source-page evidence over Reader's controlled browser transport.

The session keeps DOM snapshots, not a live browser or model-visible evaluator.
Every action list is replayed as a native PageRecipe, exactly as rule validation
will replay it. Node indexes address matched elements, never character offsets.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
import soupsieve
from bs4 import BeautifulSoup, NavigableString, Tag

from app.core.settings import Settings
from app.ingestion.browser_runtime import browser_execution_identity
from app.ingestion.http import raise_for_provider_status
from app.ingestion.models import SourceScanError
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.url_safety import PublicAsyncClient, validate_http_url
from app.ingestion.web_crawl import crawl_page
from app.ingestion.web_crawl_types import PageRecipe
from app.ingestion.web_rules import web_rule_failure_feedback
from app.ingestion.web_structure import (
    HEADINGS as _HEADINGS,
)
from app.ingestion.web_structure import (
    ArticleStructure as _ArticleStructure,
)
from app.ingestion.web_structure import (
    article_structure as _article_structure,
)

_MAX_PAGE_BYTES = 5 * 1024 * 1024
_MAX_CACHED_PAGES = 2
_ATTRIBUTES = ("id", "class", "href", "title", "role", "aria-label", "data-testid")


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())


def bound_inspection_report(report: dict, maximum_bytes: int) -> dict:
    """Bound the final tool object, including host envelope and evidence identity.

    The context calls this again after adding its metadata. It must cache that
    delivered result, not an earlier wider snapshot. Cursor changes happen in
    the same pass as node trimming and cannot acknowledge undisclosed nodes.
    """
    result = copy.deepcopy(report)
    result.setdefault("truncated", False)
    start = result.get("node_start", 0)

    def cursor() -> None:
        if "nodes" not in result:
            return
        nodes = result["nodes"]
        if result.get("evidence_mode") == "overview":
            result["next_node_start"] = None
        elif nodes:
            end = nodes[-1].get("index", start) + 1
            total = result.get("total_nodes", end)
            result["next_node_start"] = end if end < total else None
        else:
            result["next_node_start"] = start if start < result.get("total_nodes", start) else None

    def absolute_http_url(value: object) -> bool:
        try:
            parsed = urlsplit(value) if isinstance(value, str) else None
            return bool(parsed and parsed.scheme in {"http", "https"} and parsed.netloc)
        except ValueError:
            return False

    def resolve_navigation(base: object, href: object) -> str | None:
        if not absolute_http_url(base) or not isinstance(href, str):
            return None
        try:
            target = urljoin(base, href)
        except ValueError:
            return None
        return target if absolute_http_url(target) else None

    while True:
        cursor()
        if _json_bytes(result) <= maximum_bytes:
            return result
        result["truncated"] = True
        nodes = result.get("nodes", [])
        if len(nodes) > 1:
            nodes.pop()
            continue
        if result.get("links"):
            result["links"].pop()
            continue
        if nodes:
            node = nodes[0]
            attributes = node.get("attributes", {})
            href = attributes.get("href") if isinstance(attributes, dict) else None
            navigation_url = resolve_navigation(result.get("url"), href)
            raw_navigation = (
                node.get("tag") == "a"
                and not node.get("href_truncated")
                and navigation_url is not None
            )
            absolute_link = next(
                (
                    link
                    for link in node.get("links", [])
                    if isinstance(link, dict) and absolute_http_url(link.get("url"))
                ),
                None,
            )
            for key in ("parent", "headings", "path"):
                if key in node:
                    del node[key]
                    break
            else:
                if "attributes" in node:
                    if raw_navigation:
                        if attributes != {"href": href}:
                            node["attributes"] = {"href": href}
                            continue
                    else:
                        del node["attributes"]
                        continue
                if "links" in node:
                    if raw_navigation or absolute_link is None:
                        del node["links"]
                        continue
                    minimal_link = {
                        key: absolute_link[key]
                        for key in ("url", "page_ref")
                        if key in absolute_link
                    }
                    if node["links"] != [minimal_link]:
                        node["links"] = [minimal_link]
                        continue
                text = node.get("text")
                if isinstance(text, str) and len(text) > 32:
                    node["text"] = text[: max(32, len(text) // 2)]
                    node["text_truncated"] = True
                    continue
                if (raw_navigation or absolute_link) and text:
                    node["text"] = ""
                    node["text_truncated"] = True
                    continue
                for key in ("cache_hit", "engine", "selector"):
                    if key in result:
                        del result[key]
                        break
                else:
                    if raw_navigation:
                        # Last resort: one absolute link needs less space than
                        # raw href plus its actual redirected page base. Keep
                        # the existing link shape, never rewrite DOM attributes.
                        link = {"url": navigation_url}
                        if "page_ref" in node:
                            link["page_ref"] = node.pop("page_ref")
                        node["links"] = [link]
                        node.pop("attributes")
                        result.pop("url")
                        continue
                    if "url" in result:
                        del result["url"]
                        continue
                    break
            continue
        for key in ("message", "evidence", "url", "selector"):
            if key in result:
                del result[key]
                break
        else:
            break
    # No meaningful node fits alongside required host metadata. Do not claim
    # success or advance a cursor merely because an index could fit by itself.
    failure = {
        key: result[key]
        for key in (
            "phase",
            "retryable",
            "evidence_id",
            "page_revision",
            "dom_revision",
            "recipe_id",
        )
        if key in result
    }
    failure.update(
        status="fail",
        code="inspection_output_too_large",
        truncated=True,
        nodes=[],
        node_start=start,
        total_nodes=result.get("total_nodes", start),
        next_node_start=start,
        next_action="inspect_a_narrower_selector",
    )
    return failure


def _text(node: Tag, limit: int = 500) -> str:
    # No page script, CSS or injected code is included as model instructions.
    values = (
        str(value).strip()
        for value in node.descendants
        if isinstance(value, NavigableString)
        and value.parent
        and value.parent.name not in {"script", "style", "noscript"}
    )
    result = ""
    for value in values:
        result += (" " if result else "") + value
        if len(result) >= limit:
            return result[:limit]
    return result


def _path(node: Tag) -> str:
    parts = []
    current = node
    for _ in range(12):
        if not isinstance(current, Tag) or current.name == "[document]":
            break
        index = 1 + sum(
            1 for _ in current.previous_siblings if isinstance(_, Tag) and _.name == current.name
        )
        parts.append(f"{current.name}:nth-of-type({index})")
        current = current.parent
    # Descendant ancestry can be partial on unusually deep documents. This is a
    # locator hint, not a generated rule; original attributes are also supplied.
    return " > ".join(reversed(parts))


def _attributes(node: Tag) -> dict:
    result = {}
    for key in _ATTRIBUTES:
        value = node.get(key)
        if value is not None:
            complete = " ".join(value) if isinstance(value, list) else str(value)
            if len(complete) <= 400:
                result[key] = complete
    return result


def _links(node: Tag, limit: int = 4) -> list[dict]:
    anchors = [node] if node.name == "a" and node.has_attr("href") else []
    anchors.extend(node.find_all("a", href=True, limit=limit))
    return [
        {"url": str(anchor["href"]), "title": _text(anchor, 120)}
        for anchor in anchors[:limit]
        if len(str(anchor["href"])) <= 2048
    ]


def _node_evidence(node: Tag, index: int) -> dict:
    result = {
        "index": index,
        "path": _path(node),
        "tag": node.name,
        "attributes": _attributes(node),
        "text": _text(node),
        "headings": [
            {"tag": heading.name, "text": _text(heading, 160)}
            for heading in node.find_all(_HEADINGS, limit=4)
        ],
        "links": _links(node),
    }
    if isinstance(node.parent, Tag) and node.parent.name not in {"[document]", "html", "body"}:
        result["parent"] = {
            "path": _path(node.parent),
            "attributes": _attributes(node.parent),
            "text": _text(node.parent, 400),
            "links": _links(node.parent, 2),
        }
    return result


def _collection_control(node: Tag, structure: dict[int, _ArticleStructure]) -> bool:
    """A control beside content cards is a clue to inspect, not proven pagination."""
    info = structure[id(node)]
    if info.excluded or info.in_card or node.name not in {"a", "button"}:
        return False
    parent = node.parent
    for _ in range(3):
        if not isinstance(parent, Tag) or parent.name in {"body", "html"}:
            break
        if structure[id(parent)].contains_card:
            return True
        if parent.name == "main":
            break
        parent = parent.parent
    return False


@dataclass(frozen=True)
class _Page:
    url: str
    html: str
    revision: str
    dom_revision: str
    recipe_id: str
    render_js: bool
    browser_engine: str


class SourceBrowserSession:
    def __init__(
        self, settings: Settings, source_url: str, *, expected_identity: dict | None = None
    ) -> None:
        self.settings = settings
        self.source_url = source_url
        self._expected_identity = expected_identity
        self._pages: OrderedDict[str, _Page] = OrderedDict()
        self._closed = False

    def invalidate(self) -> None:
        """A rule execution fetched a newer page; subsequent inspection must refetch."""
        self._pages.clear()

    async def aclose(self) -> None:
        self._closed = True
        self._pages.clear()

    async def _load(self, url: str, recipe: PageRecipe) -> tuple[_Page, bool]:
        recipe_id = hashlib.sha256(recipe.model_dump_json().encode()).hexdigest()[:16]
        key = url + "\n" + recipe_id
        if key in self._pages:
            self._pages.move_to_end(key)
            return self._pages[key], True
        browser_engine = self.settings.ingestion_browser_engine
        if recipe.render_js and self._expected_identity is None:
            self._expected_identity = browser_execution_identity(
                browser_engine,
                self.settings.ingestion_lightpanda_executable_path,
            )
        async with PublicAsyncClient(follow_redirects=True) as client:
            adapter = WebBlogSourceAdapter(WebBlogSourceConfig(url=url), client)
            crawled = await crawl_page(
                client,
                url,
                recipe,
                maximum_bytes=_MAX_PAGE_BYTES,
                robots_allows=adapter._robots_allows,
                browser_engine=browser_engine,
                lightpanda_executable_path=self.settings.ingestion_lightpanda_executable_path,
                browser_identity=self._expected_identity,
            )
        raise_for_provider_status(crawled.response, "web")
        html, final_url = crawled.response.text, str(crawled.response.url)
        revision = hashlib.sha256((final_url + "\n" + recipe_id + "\n" + html).encode()).hexdigest()
        page = _Page(
            url=final_url,
            html=html,
            revision=revision,
            dom_revision=hashlib.sha256(html.encode("utf-8")).hexdigest(),
            recipe_id=recipe_id,
            render_js=recipe.render_js,
            browser_engine=browser_engine,
        )
        if len(html.encode()) > _MAX_PAGE_BYTES:
            raise SourceScanError("response_too_large", "Rendered DOM exceeded size limit.")
        if self._closed:
            raise SourceScanError("browser_session_closed", "The inspection session is closed.")
        self._pages[key] = page
        while len(self._pages) > _MAX_CACHED_PAGES:
            self._pages.popitem(last=False)
        return page, False

    def _bound(self, report: dict) -> dict:
        return bound_inspection_report(report, self.settings.web_rule_agent_max_tool_result_bytes)

    async def inspect(
        self,
        url: str,
        *,
        render_js: bool = False,
        actions: list[dict] | None = None,
        selector: str | None = None,
        node_start: int = 0,
        node_limit: int = 5,
        mode: str = "overview",
    ) -> dict:
        try:
            if self._closed:
                raise SourceScanError("browser_session_closed", "The inspection session is closed.")
            validate_http_url(url)
            source, target = urlsplit(self.source_url), urlsplit(url)
            if (source.scheme, source.netloc) != (target.scheme, target.netloc):
                raise ValueError("Inspection requires a host-authorized same-origin page")
            if (
                type(node_start) is not int
                or not 0 <= node_start <= 100000
                or type(node_limit) is not int
                or not 1 <= node_limit <= 5
                or mode not in {"overview", "nodes"}
            ):
                raise ValueError(
                    "Use node_start 0..100000, node_limit 1..5 and overview/nodes mode"
                )
            if selector is not None:
                if not isinstance(selector, str) or not 1 <= len(selector) <= 400:
                    raise ValueError("A CSS selector must contain 1..400 characters")
                soupsieve.compile(selector)
            recipe = PageRecipe.model_validate(
                {
                    "extraction": {"name": "Reader DOM inspection", "baseSelector": "html"},
                    "render_js": render_js,
                    "actions": actions or [],
                }
            )
            page, cache_hit = await self._load(url, recipe)
            # Match the native extractor's repaired DOM node order.
            soup = BeautifulSoup(page.html, "lxml")
            effective_selector = selector or "main, article, h1, h2, h3, a[href], button"
            nodes = list(soup.select(effective_selector))
            if node_start and node_start >= len(nodes):
                raise ValueError("node_start exceeds the selected nodes; reset it to 0")
            overview = mode == "overview" and selector is None and node_start == 0
            control_index = None
            if overview:
                # Cover semantic groups and positions, not just header/navigation.
                structure = _article_structure(soup)
                eligible = [
                    (i, node) for i, node in enumerate(nodes) if not structure[id(node)].excluded
                ]
                selected = []
                for tags in ({"h1"}, {"main", "article"}, {"h2", "h3"}, {"a"}, {"button"}):
                    group = [(i, node) for i, node in eligible if node.name in tags]
                    if group:
                        selected.append(group[len(group) // 2])
                selected = sorted({i: node for i, node in selected}.items())
                controls = [
                    (i, node) for i, node in eligible if _collection_control(node, structure)
                ]
                if controls:
                    # Keep a tail control even with node_limit=1 or JSON
                    # trimming. A source-order prefix would hide pagination.
                    control_index, control = controls[-1]
                    selected = [
                        (control_index, control),
                        *((i, node) for i, node in selected if i != control_index),
                    ]
                selected = selected[:node_limit]
            else:
                selected = list(enumerate(nodes[node_start : node_start + node_limit], node_start))
            evidence = [_node_evidence(node, index) for index, node in selected]
            if control_index is not None:
                evidence[0]["observation"] = "unverified_content_control"
            links = []
            for node in evidence:
                links.extend(node["links"])
                links.extend(node.get("parent", {}).get("links", []))
            for link in links:
                try:
                    link["url"] = urljoin(page.url, link["url"])
                except ValueError:
                    link["url"] = ""
            report = {
                "status": "pass",
                "code": "web_page_inspected",
                "url": page.url,
                "page_revision": page.revision,
                "dom_revision": page.dom_revision,
                "recipe_id": page.recipe_id,
                "engine": page.browser_engine if page.render_js else "http",
                "evidence_mode": "overview" if overview else "nodes",
                "cache_hit": cache_hit,
                "content_is_untrusted": True,
                "selector": effective_selector,
                "node_start": node_start,
                "total_nodes": len(nodes),
                "nodes": evidence,
                "next_node_start": None,
                "links": links,
                "next_action": "inspect_an_observed_selector_or_write_candidate",
            }
            return self._bound(report)
        except (ValueError, soupsieve.SelectorSyntaxError) as exc:
            return self._bound(
                {
                    "status": "fail",
                    "code": "invalid_inspection",
                    "message": str(exc)[:400],
                    "next_action": "reset_node_start_or_refine_selector",
                }
            )
        except (SourceScanError, httpx.HTTPError) as exc:
            return self._bound(web_rule_failure_feedback(exc, phase="inspect"))
