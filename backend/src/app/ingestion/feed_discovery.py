"""Bounded RSS/Atom autodiscovery before authoring a website crawling rule."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal
from urllib.parse import urldefrag, urljoin, urlsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.ingestion.http import provider_http_error_code
from app.ingestion.models import SourceScanError
from app.ingestion.url_safety import UnsafeSourceUrl, safe_get, validate_http_url

_FEED_TYPES = {"application/rss+xml", "application/atom+xml"}
_FEED_LABEL = re.compile(r"\b(?:rss|atom)\b", re.IGNORECASE)
_COMMENTS = re.compile(r"(?:^|[^a-z])comments?(?:$|[^a-z])", re.IGNORECASE)
_USER_AGENT = "ReaderAggregator/0.2 (+https://example.invalid)"
Fetch = Callable[..., Awaitable[httpx.Response]]


@dataclass(frozen=True)
class FeedDiscoveryResult:
    status: Literal["found", "not_found", "retry", "blocked"]
    feed_url: str | None
    evidence: dict[str, Any]


@dataclass(frozen=True)
class _Candidate:
    url: str
    origin: str


def _safe_url(value: str, base: str | None = None) -> str | None:
    """Reject unsafe syntax/literal IPs; safe_get validates DNS and redirects."""
    if not value or len(value) > 2048:
        return None
    try:
        result = urldefrag(urljoin(base, value) if base else value).url
        validate_http_url(result)
        host = urlsplit(result).hostname or ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        return result
    except (UnsafeSourceUrl, ValueError):
        return None


def _mime_type(value: str) -> str:
    return value.partition(";")[0].strip().lower()


def _candidates(
    html: bytes, final_url: str, source_url: str, maximum: int
) -> tuple[list[_Candidate], bool]:
    soup = BeautifulSoup(html, "html.parser")
    base_url = final_url
    base = soup.find("base", href=True)
    if base is not None:
        base_url = _safe_url(str(base.get("href")), final_url) or final_url

    candidates: list[_Candidate] = []
    seen = {source_url, final_url}
    truncated = False

    def add(href: str, label: str, origin: str) -> None:
        nonlocal truncated
        if _COMMENTS.search(f"{href} {label}"):
            return
        url = _safe_url(href, base_url)
        if url is None or url in seen:
            return
        seen.add(url)
        if len(candidates) >= maximum:
            truncated = True
        else:
            candidates.append(_Candidate(url, origin))

    # Streamed page metadata can place explicit feed declarations in body.
    # Require the same rel/type proof and validate the fetched feed regardless
    # of placement; script strings and arbitrary body URLs are not declarations.
    for link in soup.find_all("link", href=True):
        rel = link.get("rel", [])
        rel = rel.split() if isinstance(rel, str) else rel
        if (
            "alternate" in {str(value).lower() for value in rel}
            and _mime_type(str(link.get("type", ""))) in _FEED_TYPES
        ):
            origin = "head_alternate" if link.find_parent("head") else "document_alternate"
            add(str(link["href"]), str(link.get("title", "")), origin)

    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"])
        label = f"{anchor.get_text(' ', strip=True)} {anchor.get('title', '')}"
        if _mime_type(str(anchor.get("type", ""))) in _FEED_TYPES or _FEED_LABEL.search(
            f"{label} {href}"
        ):
            add(href, label, "anchor")

    # Declarations retain priority, but their failure must not suppress scoped
    # guesses. All candidates share the same cap; never guess a parent/site feed
    # or drop a query that may be selecting the subscribed column.
    if not (urlsplit(source_url).query or urlsplit(final_url).query):
        original_path = urlsplit(source_url).path.rstrip("/")
        final_path = urlsplit(final_url).path.rstrip("/")
        if (
            not original_path
            or final_path == original_path
            or final_path.startswith(original_path + "/")
        ):
            directory = final_url.split("?", 1)[0].rstrip("/") + "/"
            for suffix in ("feed", "rss.xml", "atom.xml", "rss", "feed.xml", "atom"):
                add(urljoin(directory, suffix), "", "guess")
    return candidates, truncated


def _guess_retains_scope(candidate_url: str, final_url: str) -> bool:
    """A guessed column feed redirect must not silently become a broader feed."""
    candidate, final = urlsplit(candidate_url), urlsplit(final_url)
    directory = candidate.path.rpartition("/")[0].rstrip("/") + "/"
    return candidate.netloc == final.netloc and final.path.startswith(directory)


def parse_usable_web_feed(body: bytes, feed_url: str) -> Any | None:
    """Shared discovery/sync gate: RSS/Atom plus usable links/titles, or valid empty."""
    # These bytes have already been fetched. Supply an XML parsing hint along
    # with the base URL so missing/mislabelled HTTP MIME is not a bozo error;
    # the parsed document's version still has to prove it is RSS or Atom.
    parsed = feedparser.parse(
        body,
        response_headers={
            "content-location": feed_url,
            "content-type": "application/xml",
        },
    )
    version = str(parsed.get("version", ""))
    if not version.startswith(("rss", "atom")):
        return None
    if not parsed.entries and (parsed.get("bozo") or not parsed.feed):
        return None
    if parsed.entries and not any(
        _safe_url(str(entry.get("link") or "")) and str(entry.get("title") or "").strip()
        for entry in parsed.entries
    ):
        return None
    return parsed


def _feed_evidence(parsed: Any, feed_url: str) -> dict[str, Any]:
    samples: list[dict[str, str]] = []
    for entry in parsed.entries[:10]:
        url = _safe_url(str(entry.get("link", "")), feed_url)
        if url is None:
            continue
        title = BeautifulSoup(str(entry.get("title", "")), "html.parser").get_text(" ", strip=True)[
            :256
        ]
        samples.append({"url": url, "title": title})
        if len(samples) == 3:
            break
    return {"feed_version": parsed.version, "entry_count": len(parsed.entries), "samples": samples}


async def discover_web_feed(
    source_url: str,
    client: httpx.AsyncClient,
    *,
    fetch: Fetch | None = None,
    timeout_seconds: float = 25,
    max_response_bytes: int = 2 * 1024 * 1024,
    max_candidates: int = 6,
) -> FeedDiscoveryResult:
    """Verify a page's feed, or return a bounded diagnostic without calling AI.

    Production fetching uses ``safe_get`` and therefore requires a
    ``PublicAsyncClient``. An injected fetch has the same signature and is for
    deterministic tests. The candidate limit excludes the initial page GET.
    A temporary failure of that page or an explicit feed returns ``retry``;
    access challenges on the page or an explicit feed return ``blocked`` unless
    another candidate is valid. Guessed endpoint denials remain diagnostics;
    unsuccessful guesses alone do not prevent subsequent web-rule authoring.
    Cancellation always propagates to the caller.
    """
    if timeout_seconds <= 0 or max_response_bytes < 1 or not 1 <= max_candidates <= 6:
        raise ValueError("Discovery requires positive time/byte limits and 1–6 candidates.")
    started = monotonic()
    deadline = started + timeout_seconds
    fetch = fetch or safe_get
    attempts: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {
        "attempts": attempts,
        "candidate_limit": max_candidates,
        "timeout_seconds": timeout_seconds,
        "max_response_bytes": max_response_bytes,
        "candidate_limit_reached": False,
    }
    transient_failure = False
    blocked_code: str | None = None

    def finish(
        status: Literal["found", "not_found", "retry", "blocked"], url: str | None = None
    ):
        if status != "found" and blocked_code:
            status = "blocked"
            evidence["error_code"] = blocked_code
        evidence["elapsed_ms"] = round((monotonic() - started) * 1000)
        evidence["requests"] = len(attempts)
        return FeedDiscoveryResult(status, url, evidence)

    async def download(
        candidate: _Candidate, *, remaining_candidates: int = 1
    ) -> httpx.Response | None:
        nonlocal transient_failure, blocked_code
        attempt: dict[str, Any] = {"url": candidate.url, "origin": candidate.origin}
        attempts.append(attempt)
        # Preserve useful time for the page and explicitly advertised feeds;
        # speculative fallbacks must not shorten their established request cap.
        # Only guesses share the remaining time between pending candidates.
        remaining_time = deadline - monotonic()
        request_timeout = max(
            0.001,
            min(
                8.0,
                remaining_time,
                timeout_seconds / 3
                if candidate.origin != "guess"
                else remaining_time / remaining_candidates,
            ),
        )
        try:
            async with asyncio.timeout(request_timeout):
                response = await fetch(
                    client,
                    candidate.url,
                    headers={"User-Agent": _USER_AGENT},
                    timeout=request_timeout,
                    max_response_bytes=max_response_bytes,
                )
            attempt["status_code"] = response.status_code
            code = provider_http_error_code(
                response, "web" if candidate.origin == "source" else "web_rss"
            )
            if response.status_code in {401, 403} or code.endswith("_challenge_required"):
                attempt["result"] = "access_blocked"
                attempt["error_code"] = code
                # Guessed endpoints are not evidence that an RSS feed exists.
                # Their denial must not block authoring for a readable page.
                if candidate.origin != "guess":
                    blocked_code = blocked_code or code
                return None
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                attempt["result"] = "temporary_http_error"
                transient_failure |= candidate.origin != "guess"
                return None
            if not 200 <= response.status_code <= 299:
                attempt["result"] = "http_error"
                return None
            if len(response.content) > max_response_bytes:
                attempt["result"] = "response_too_large"
                transient_failure |= candidate.origin != "guess"
                return None
            final_url = _safe_url(str(response.url))
            if final_url is None:
                attempt["result"] = "unsafe_redirect"
                return None
            if candidate.origin == "guess" and not _guess_retains_scope(candidate.url, final_url):
                attempt["result"] = "unscoped_redirect"
                return None
            attempt["final_url"] = final_url
            attempt["result"] = "downloaded"
            return response
        except (TimeoutError, httpx.TimeoutException):
            attempt["result"] = "timeout"
            transient_failure |= candidate.origin != "guess"
        except httpx.RequestError:
            attempt["result"] = "network_error"
            transient_failure |= candidate.origin != "guess"
        except UnsafeSourceUrl as exc:
            # The shared safe fetcher also wraps bounded DNS failures in this
            # exception. Those are unknown discovery results, not absent RSS.
            if str(exc) in {
                "Source hostname resolution timed out.",
                "Source hostname could not be resolved.",
            }:
                attempt["result"] = "dns_error"
                transient_failure |= candidate.origin != "guess"
            else:
                attempt["result"] = "unsafe_url"
        except SourceScanError as exc:
            attempt["result"] = exc.code
            transient_failure |= candidate.origin != "guess"
        return None

    source_url = _safe_url(source_url) or ""
    if not source_url:
        evidence["reason"] = "unsafe_source_url"
        return finish("not_found")
    try:
        async with asyncio.timeout(timeout_seconds):
            root = await download(_Candidate(source_url, "source"))
            final_url = source_url
            if root is not None:
                final_url = str(attempts[-1]["final_url"])
                parsed = parse_usable_web_feed(root.content, final_url)
                if parsed is not None:
                    evidence.update(_feed_evidence(parsed, final_url))
                    attempts[-1]["result"] = "valid_feed"
                    return finish("found", final_url)
                attempts[-1]["result"] = "not_feed"
            elif attempts[-1]["result"] in {"unsafe_url", "unsafe_redirect"}:
                return finish("not_found")
            candidates, truncated = _candidates(
                root.content if root is not None else b"", final_url, source_url, max_candidates
            )
            evidence["candidate_count"] = len(candidates)
            evidence["candidate_limit_reached"] = truncated
            for index, candidate in enumerate(candidates):
                response = await download(
                    candidate, remaining_candidates=len(candidates) - index
                )
                if response is None:
                    continue
                parsed = parse_usable_web_feed(response.content, str(attempts[-1]["final_url"]))
                attempts[-1]["result"] = "valid_feed" if parsed is not None else "not_feed"
                if parsed is not None:
                    feed_url = str(attempts[-1]["final_url"])
                    evidence.update(_feed_evidence(parsed, feed_url))
                    return finish("found", feed_url)
    except TimeoutError:
        evidence["reason"] = "time_budget_exhausted"
        if attempts and attempts[-1].get("result") is None:
            attempts[-1]["result"] = "time_budget_exhausted"
        return finish("retry")
    return finish("retry" if transient_failure else "not_found")
