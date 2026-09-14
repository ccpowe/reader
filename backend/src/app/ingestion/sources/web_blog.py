"""Native Crawl4AI Web discovery integrated with Reader's scanner contracts."""

from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Tag

from app.domain.enums import ContentKind
from app.ingestion.http import ensure_response_size, raise_for_provider_status
from app.ingestion.models import (
    DiscoveredContent,
    DiscoveredMedia,
    DiscoveredWebLink,
    SourceScanError,
    SourceScanPage,
    SourceScanRequest,
    checkpoint_for_items,
    checkpoint_ids,
)
from app.ingestion.site_icons import discover_site_icon_url
from app.ingestion.source_identity import (
    normalize_web_article_url as _normalize_url,
)
from app.ingestion.source_identity import (
    web_article_native_id as _url_native_id,
)
from app.ingestion.url_safety import safe_get
from app.ingestion.web_crawl import crawl_page
from app.ingestion.web_crawl_types import WebRule

from .base import SourceAdapter

_USER_AGENT = "ReaderAggregator/0.2 (+https://example.invalid)"
_ROBOTS_USER_AGENT = "ReaderAggregator"


@dataclass(frozen=True)
class WebBlogSourceConfig:
    url: str
    resolve_site_avatar: bool = False
    rule: WebRule | None = None
    browser_identity: dict | None = None


class WebBlogSourceAdapter(SourceAdapter):
    def __init__(self, config: WebBlogSourceConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client
        self._rule = config.rule
        self._robots_cache: dict[str, RobotFileParser | None] = {}
        self._robots_lock = asyncio.Lock()
        self._listing_rows: dict[str, list[dict]] = {}
        self._listing_items: dict[str, DiscoveredContent] = {}
        self._listing_diagnostics: dict[str, dict] = {}

    @property
    def _provider_mode(self) -> str:
        return "web_crawl4ai"

    async def scan_page(self, request: SourceScanRequest) -> SourceScanPage:
        if self._rule is None:
            raise SourceScanError(
                "web_rule_required",
                "This Web source needs an accepted Crawl4AI rule.",
                long_lived=True,
            )
        listing_url = self._config.url
        listing_offset = 0
        if request.continuation:
            if request.continuation.get("provider") != "web_crawl4ai":
                raise SourceScanError("cursor_invalid", "Web continuation provider changed.")
            try:
                listing_url, listing_offset = await self._validated_next_listing(request)
            except SourceScanError as exc:
                if exc.code == "web_rule_action_failed":
                    exc.evidence = {
                        **exc.evidence,
                        "is_head": False,
                        "phase": "continuation_replay",
                    }
                raise

        if not await self._robots_allows(listing_url, request.max_response_bytes):
            raise SourceScanError(
                "robots_disallowed",
                "The site robots policy disallows this Web source.",
                long_lived=True,
            )
        headers = {"User-Agent": _USER_AGENT}
        validators = request.checkpoint.get("validators")
        if request.conditional and request.continuation is None and isinstance(validators, dict):
            if validators.get("etag"):
                headers["If-None-Match"] = str(validators["etag"])
            if validators.get("last_modified"):
                headers["If-Modified-Since"] = str(validators["last_modified"])
        try:
            response = await self._crawl_listing(
                listing_url, request.max_response_bytes, headers=headers
            )
        except SourceScanError as exc:
            if exc.code == "web_rule_action_failed":
                exc.evidence = {
                    **exc.evidence,
                    "is_head": request.continuation is None,
                    "listing_url": listing_url,
                }
            raise
        if response.status_code == httpx.codes.NOT_MODIFIED:
            return SourceScanPage(
                items=(),
                provider_mode=self._provider_mode,
                raw_items=0,
                completed=True,
                checkpoint=request.checkpoint,
                web_listing_evidence={
                    "is_head": request.continuation is None,
                    "not_modified": True,
                },
            )
        raise_for_provider_status(response, "web")
        ensure_response_size(response, request.max_response_bytes)
        soup = BeautifulSoup(response.text, "html.parser")

        links = self._native_links(str(response.url))
        evidence = {
            **self._listing_diagnostics[str(response.url)],
            "is_head": request.continuation is None,
            "listing_url": str(response.url),
        }
        accepted_urls = [link.normalized_url for link in links if link.accepted]
        article_window = min(request.max_raw_items, 40)
        accepted_window = accepted_urls[listing_offset : listing_offset + article_window]
        # Web reading mode is extracted from the user's current WebView. Old
        # article recipes remain parseable but never schedule per-item requests.
        article_results = [(url, self._listing_items[url]) for url in accepted_window]
        items = [item for _, item in article_results]
        if not items:
            # A verified continuation may legitimately lead to an empty final
            # page. Only the source's fresh head is evidence for rule repair.
            if request.continuation is not None:
                return SourceScanPage(
                    items=(),
                    provider_mode=self._provider_mode,
                    raw_items=0,
                    completed=True,
                    checkpoint=request.checkpoint,
                    web_listing_evidence=evidence,
                )
            raise SourceScanError(
                "web_rule_missing_fields"
                if evidence["missing_required_fields"]
                else "web_structure_changed",
                "Web listing produced candidates but no usable title and article URL.",
                evidence=evidence,
            )
        fetched_by_url = {url: item for url, item in article_results}
        links = [
            replace(
                link,
                fetched=True,
                canonical_url=fetched_by_url[link.normalized_url].external_url,
                content_hash=fetched_by_url[link.normalized_url].material_hash(),
            )
            if link.normalized_url in fetched_by_url
            else link
            for link in links
        ]
        normalized = tuple(items)
        anchors = checkpoint_ids(request.checkpoint)
        visible, overlapped = _through_oldest_anchor(normalized, anchors)
        next_listing = self._next_listing_url(soup, str(response.url))
        next_offset = listing_offset + len(accepted_window)
        same_listing_remaining = next_offset < len(accepted_urls)
        completed = (
            request.initial or overlapped or (not same_listing_remaining and next_listing is None)
        )
        gap_detected = bool(
            not request.initial
            and anchors
            and not overlapped
            and not same_listing_remaining
            and next_listing is None
        )
        validators_out = {
            key: value
            for key, value in {
                "etag": response.headers.get("etag"),
                "last_modified": response.headers.get("last-modified"),
            }.items()
            if value
        }
        checkpoint = checkpoint_for_items(
            normalized,
            validators=validators_out,
            extra={"listing_url": self._config.url},
        )
        next_continuation = None
        if not completed and (same_listing_remaining or next_listing):
            boundary_urls = accepted_window or accepted_urls[:article_window]
            next_continuation = {
                "provider": "web_crawl4ai",
                "replay_url": str(response.url),
                "boundary_ids": [_url_native_id(url) for url in boundary_urls],
                "next_url": str(response.url) if same_listing_remaining else next_listing,
                "same_listing": same_listing_remaining,
                "resume_offset": next_offset if same_listing_remaining else 0,
            }
        site_name = self._meta(soup, "og:site_name")
        frontier_by_url = {link.normalized_url: link for link in links}
        frontier_links = list(links[: request.max_raw_items])
        known_frontier_urls = {link.normalized_url for link in frontier_links}
        for url in accepted_window:
            link = frontier_by_url.get(url)
            if link is not None and url not in known_frontier_urls:
                frontier_links.append(link)
                known_frontier_urls.add(url)
        return SourceScanPage(
            items=visible,
            provider_mode=self._provider_mode,
            raw_items=len(accepted_window),
            completed=completed,
            next_continuation=next_continuation,
            checkpoint=checkpoint,
            boundary_ids=tuple(item.native_id for item in normalized),
            limit_reached=(
                "initial_history_truncated"
                if request.initial and len(accepted_urls) > len(accepted_window)
                else None
            ),
            gap_detected=gap_detected,
            gap_details=(
                {"reason": "listing_exhausted_before_checkpoint"} if gap_detected else None
            ),
            source_display_name=site_name,
            source_avatar_url=(
                discover_site_icon_url(response.text, str(response.url))
                if self._config.resolve_site_avatar or request.initial
                else None
            ),
            web_links=tuple(frontier_links),
            web_listing_evidence=evidence,
            warning_code="web_rule_partial_parse" if evidence["missing_required_fields"] else None,
            warning_message=(
                "Some listing rows have no usable URL/title; existing pairs remain available."
                if evidence["missing_required_fields"]
                else None
            ),
        )

    async def _validated_next_listing(self, request: SourceScanRequest) -> tuple[str, int]:
        continuation = request.continuation or {}
        replay_url = str(continuation.get("replay_url") or "")
        next_url = str(continuation.get("next_url") or "")
        expected = {str(value) for value in continuation.get("boundary_ids", []) if value}
        if not replay_url or not next_url or not expected:
            raise SourceScanError("cursor_invalid", "Web continuation is incomplete.")
        if not await self._robots_allows(replay_url, request.max_response_bytes):
            raise SourceScanError("robots_disallowed", "Replay listing disallows crawling.")
        response = await self._crawl_listing(replay_url, request.max_response_bytes)
        raise_for_provider_status(response, "web")
        ensure_response_size(response, request.max_response_bytes)
        replay_soup = BeautifulSoup(response.text, "html.parser")
        actual = [
            _url_native_id(link.normalized_url) for link in self._native_links(str(response.url))
        ]
        matched_positions = [
            index for index, native_id in enumerate(actual) if native_id in expected
        ]
        if not matched_positions:
            raise SourceScanError(
                "cursor_invalid", "Listing shifted before its boundary was verified."
            )
        current_next = self._next_listing_url(replay_soup, str(response.url))
        if continuation.get("same_listing"):
            # Resume after the oldest surviving boundary URL.  This tolerates
            # insertions at the head while the scheduler's fresh-head lane
            # independently captures those new links.
            return replay_url, max(matched_positions) + 1
        if current_next != next_url:
            raise SourceScanError(
                "cursor_invalid", "Web listing continuation changed after it was saved."
            )
        return next_url, 0

    async def _crawl_listing(
        self, url: str, maximum_bytes: int, *, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        recipe = self._rule.listing
        result = await crawl_page(
            self._client,
            url,
            recipe,
            maximum_bytes=maximum_bytes,
            robots_allows=self._robots_allows,
            headers=None if recipe.render_js else headers,
            fetch=safe_get,
            browser_identity=self._config.browser_identity,
        )
        self._listing_rows[str(result.response.url)] = result.rows
        return result.response

    def _listing_item(self, url: str, title: str, row: dict) -> DiscoveredContent:
        summary = row.get("excerpt") or row.get("summary")
        image_url = row.get("image_url")
        media = ()
        if isinstance(image_url, str) and image_url.strip():
            target = urljoin(url, image_url.strip())
            if urlsplit(target).scheme in {"http", "https"}:
                media = (DiscoveredMedia(original_url=target),)
        return DiscoveredContent(
            native_id=_url_native_id(url),
            kind=ContentKind.ARTICLE,
            title=title[:2000],
            external_url=url,
            published_at=self._parse_datetime(
                str(row.get("published_at") or ""),
                tuple(self._rule.date_formats),
            ),
            author_name=str(row["author"])[:300] if row.get("author") else None,
            excerpt_html=("<p>" + html.escape(str(summary)[:10000]) + "</p>") if summary else None,
            media=media,
            raw_metadata={
                "adapter": "web_crawl4ai",
                "index_url": self._config.url,
                "web_discovery_only": True,
            },
        )

    def _native_links(self, listing_url: str) -> list[DiscoveredWebLink]:
        links = []
        seen = set()
        origin = urlsplit(listing_url)
        rows = self._listing_rows.get(listing_url, [])
        missing = []
        rejected: dict[str, int] = {}

        def reject(reason: str) -> None:
            rejected[reason] = rejected.get(reason, 0) + 1

        for index, row in enumerate(rows):
            raw_url, title = row.get("url"), row.get("title")
            missing_fields = [
                key
                for key in ("url", "title")
                if not isinstance(row.get(key), str) or not row[key].strip()
            ]
            if missing_fields:
                missing.append({"row": index, "fields": missing_fields})
                reject("missing_required_fields")
                continue
            raw_url, title = raw_url.strip(), title.strip()
            if (
                not raw_url
                or not title
                or title.casefold()
                in {
                    "category",
                    "categories",
                    "about",
                    "about us",
                    "read more",
                    "learn more",
                    "home",
                    "next",
                    "previous",
                }
            ):
                reject("navigation_title")
                continue
            original = urljoin(listing_url, raw_url)
            parsed = urlsplit(original)
            if (
                parsed.scheme not in {"https", "http"}
                or parsed.scheme != origin.scheme
                or parsed.netloc.lower() != origin.netloc.lower()
                or parsed.username
                or parsed.password
            ):
                reject("outside_source_boundary")
                continue
            url = _normalize_url(original, keep_query=True)
            if url in seen or url == _normalize_url(listing_url, keep_query=True):
                reject("duplicate" if url in seen else "listing_self_link")
                continue
            seen.add(url)
            self._listing_items[url] = self._listing_item(url, title, row)
            links.append(
                DiscoveredWebLink(
                    normalized_url=url,
                    original_url=original,
                    confidence=1.0,
                    accepted=True,
                    discovered_from=listing_url,
                    evidence={"rule": self._rule.id, "engine": "crawl4ai"},
                )
            )
        self._listing_diagnostics[listing_url] = {
            "extracted_rows": len(rows),
            "valid_pairs": len(links),
            "missing_required_fields": missing[:30],
            "missing_required_count": len(missing),
            "rejected_reasons": rejected,
            "duplicate_rows": rejected.get("duplicate", 0),
        }
        return links

    async def _robots_allows(self, url: str, maximum_bytes: int) -> bool:
        parsed = urlsplit(url)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        if origin not in self._robots_cache:
            async with self._robots_lock:
                if origin not in self._robots_cache:
                    policy: RobotFileParser | None = None
                    try:
                        response = await safe_get(
                            self._client,
                            urljoin(origin, "/robots.txt"),
                            headers={"User-Agent": _USER_AGENT},
                            timeout=20.0,
                            max_response_bytes=maximum_bytes,
                        )
                        if response.status_code == 200:
                            ensure_response_size(response, maximum_bytes)
                            policy = RobotFileParser()
                            policy.set_url(str(response.url))
                            policy.parse(response.text.splitlines())
                    except (httpx.HTTPError, SourceScanError, ValueError):
                        policy = None
                    self._robots_cache[origin] = policy
        policy = self._robots_cache[origin]
        return policy is None or policy.can_fetch(_ROBOTS_USER_AGENT, url)

    def _next_listing_url(self, soup: BeautifulSoup, page_url: str) -> str | None:
        if not self._rule.next_page_selector:
            return None
        anchor = soup.select_one(self._rule.next_page_selector)
        if anchor and anchor.get("href"):
            candidate = _normalize_url(urljoin(page_url, str(anchor["href"])), keep_query=True)
            parsed, origin = urlsplit(candidate), urlsplit(page_url)
            if parsed.scheme == origin.scheme and parsed.netloc.lower() == origin.netloc.lower():
                return candidate if candidate != page_url else None
        return None

    @staticmethod
    def _meta(soup: BeautifulSoup, key: str, *, name: bool = False) -> str | None:
        attribute = "name" if name else "property"
        element = soup.find("meta", attrs={attribute: key})
        value = element.get("content") if isinstance(element, Tag) else None
        return str(value).strip() if value else None

    @classmethod
    def _parse_datetime(
        cls, raw_value: str | None, formats: tuple[str, ...] = ()
    ) -> datetime | None:
        if not raw_value:
            return None
        try:
            return cls._to_utc(datetime.fromisoformat(raw_value.replace("Z", "+00:00")))
        except ValueError:
            pass
        try:
            return cls._to_utc(parsedate_to_datetime(raw_value))
        except (TypeError, ValueError):
            pass
        for date_format in formats:
            try:
                return cls._to_utc(datetime.strptime(raw_value, date_format))
            except ValueError:
                continue
        return None

    @staticmethod
    def _to_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _through_oldest_anchor(
    items: tuple[DiscoveredContent, ...], anchors: set[str]
) -> tuple[tuple[DiscoveredContent, ...], bool]:
    if not anchors:
        return items, False
    matches = [index for index, item in enumerate(items) if item.native_id in anchors]
    return (items[: max(matches) + 1], True) if matches else (items, False)
