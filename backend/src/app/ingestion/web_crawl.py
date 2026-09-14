"""Crawl4AI execution with Reader's pinned-public transport and resource policy.

Both HTTP and browser recipes run through AsyncWebCrawler.arun and its native
extraction pipeline. The HTTP strategy replaces only unsafe default networking.
Lightpanda requests are fulfilled through the same transport; native network
blocks prevent the browser from bypassing that policy.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4
from weakref import WeakKeyDictionary

import httpx

from app.core.settings import get_settings
from app.ingestion.browser_runtime import browser_endpoint
from app.ingestion.http import raise_for_provider_status
from app.ingestion.models import SourceScanError
from app.ingestion.url_safety import UnsafeSourceUrl, safe_get
from app.ingestion.web_crawl_types import PageRecipe

_USER_AGENT = "ReaderAggregator/0.2 (+https://example.invalid)"
_CRAWL_TIMEOUT_SECONDS = 60
_BROWSER_CLOSE_TIMEOUT_SECONDS = 3.0
_SLOTS: WeakKeyDictionary = WeakKeyDictionary()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrawledPage:
    response: httpx.Response
    rows: list[dict]


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _SLOTS:
        _SLOTS[loop] = asyncio.Semaphore(1)
    return _SLOTS[loop]


async def _await_cleanup(cleanup: Awaitable[None], primary_error: BaseException | None) -> None:
    """Keep the borrowed client and crawl slot alive until cleanup has finished."""
    task = asyncio.ensure_future(cleanup)
    interrupted: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            # A second cancellation (for example a worker deadline during a
            # cancelled crawl) must not orphan the request cleanup either.
            interrupted = exc
        except Exception:
            break
    try:
        task.result()
    except Exception:
        if primary_error is None and interrupted is None:
            raise
        logger.warning("Crawl4AI cleanup failed while handling a crawl error", exc_info=True)
    if interrupted is not None:
        raise interrupted


async def _stop_isolated_driver(crawler) -> None:
    """Stop only the Playwright driver created for this isolated LP crawl.

    LP never shares a CDP connection cache. Keep its driver's owned process
    handle before stop, so an unresponsive protocol cannot strand that process.
    """
    manager = crawler.crawler_strategy.browser_manager
    playwright = manager.playwright
    if playwright is None:
        return
    connection = getattr(getattr(playwright, "_impl_obj", None), "_connection", None)
    transport = getattr(connection, "_transport", None)
    process = getattr(transport, "_proc", None)
    task = asyncio.create_task(playwright.stop())
    done, _ = await asyncio.wait({task}, timeout=_BROWSER_CLOSE_TIMEOUT_SECONDS)
    if not done:
        try:
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), _BROWSER_CLOSE_TIMEOUT_SECONDS)
                except TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await asyncio.wait_for(process.wait(), _BROWSER_CLOSE_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise SourceScanError("browser_cleanup_failed", "Browser driver did not exit.") from exc
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if process is None or process.returncode is None:
            raise SourceScanError("browser_cleanup_failed", "Browser driver did not stop.")
    else:
        task.result()
    manager.playwright = None


def action_script(recipe: PageRecipe, token: str) -> str | None:
    if not recipe.actions:
        return None
    data = json.dumps([a.model_dump() for a in recipe.actions]).replace("<", "\\u003c")
    return """async () => {
      const actions = ACTIONS;
      const token = TOKEN;
      const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
      for (const [index, action] of actions.entries()) {
        const failed = reason => ({status: 'failed', token, index, reason});
        if (action.kind === 'click') {
          let node;
          try { node = document.querySelector(action.selector); }
          catch (error) {
            if (error.name === 'SyntaxError') return failed('selector_invalid');
            throw error;
          }
          if (!node) return failed('action_target_missing');
          if (node.closest('form')) return failed('form_action_forbidden');
          node.click();
        } else if (action.kind === 'wait') {
          const until = Date.now() + action.timeout_ms;
          while (true) {
            let count;
            try { count = document.querySelectorAll(action.selector).length; }
            catch (error) {
              if (error.name === 'SyntaxError') return failed('selector_invalid');
              throw error;
            }
            if (count >= action.min_count) break;
            if (Date.now() >= until) return failed('action_wait_timeout');
            await sleep(100);
          }
        } else {
          for (let i = 0; i < action.steps; i++) {
            window.scrollBy(0, Math.max(400, window.innerHeight * 0.8));
            await sleep(250);
          }
        }
      }
      return {status: 'complete', token};
    }""".replace("TOKEN", json.dumps(token)).replace("ACTIONS", data)


async def crawl_page(
    client: httpx.AsyncClient,
    url: str,
    recipe: PageRecipe,
    *,
    maximum_bytes: int,
    robots_allows: Callable[[str, int], Awaitable[bool]],
    headers: dict[str, str] | None = None,
    fetch: Callable = safe_get,
    browser_engine: str | None = None,
    lightpanda_executable_path: str | None = None,
    browser_identity: dict | None = None,
) -> CrawledPage:
    if recipe.render_js:
        if browser_engine not in {None, "lightpanda"}:
            raise SourceScanError(
                "browser_unavailable", "Dynamic Web execution requires Lightpanda."
            )
        if browser_engine is None or lightpanda_executable_path is None:
            settings = get_settings()
            browser_engine = browser_engine or settings.ingestion_browser_engine
            lightpanda_executable_path = (
                lightpanda_executable_path or settings.ingestion_lightpanda_executable_path
            )
    # Import lazily: other providers and API startup do not load a browser engine.
    from crawl4ai import (
        AsyncWebCrawler,
        BrowserConfig,
        CacheMode,
        CrawlerRunConfig,
        JsonCssExtractionStrategy,
    )
    from crawl4ai.async_crawler_strategy import AsyncCrawlerStrategy
    from crawl4ai.models import AsyncCrawlResponse

    if not await robots_allows(url, maximum_bytes):
        raise SourceScanError("robots_disallowed", "The site robots policy disallows this page.")
    request_headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    error: Exception | None = None
    action_error: SourceScanError | None = None
    fetched_response: httpx.Response | None = None

    class PublicHTTPStrategy(AsyncCrawlerStrategy):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def crawl(self, target: str, **_kwargs):
            nonlocal error, fetched_response
            try:
                fetched_response = await fetch(
                    client,
                    target,
                    headers=request_headers,
                    timeout=20.0,
                    max_response_bytes=maximum_bytes,
                )
                if not await robots_allows(str(fetched_response.url), maximum_bytes):
                    raise SourceScanError(
                        "robots_disallowed", "Redirect target disallows crawling."
                    )
                # Preserve upstream status before Crawl4AI's anti-bot detector
                # can collapse the response into a generic extraction failure.
                raise_for_provider_status(fetched_response, "web")
                return AsyncCrawlResponse(
                    html=fetched_response.text or "<html></html>",
                    response_headers=dict(fetched_response.headers),
                    status_code=fetched_response.status_code,
                    redirected_url=str(fetched_response.url),
                )
            except Exception as exc:
                error = exc
                raise

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        user_agent=_USER_AGENT,
    )
    strategy = None if recipe.render_js else PublicHTTPStrategy()
    completion_token = uuid4().hex
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.DISABLED,
        extraction_strategy=JsonCssExtractionStrategy(
            recipe.extraction.model_dump(exclude_none=True), input_format="html"
        ),
        page_timeout=20000,
        delay_before_return_html=0.2,
        word_count_threshold=0,
        verbose=False,
        semaphore_count=1,
        check_robots_txt=False,  # Reader's pinned transport fetches robots.
        fetch_ssl_certificate=False,
        process_iframes=False,
    )
    async with (
        _slot(),
        (
            browser_endpoint(
                browser_engine,
                lightpanda_executable_path,
                expected_identity=browser_identity,
            )
            if recipe.render_js
            else nullcontext(None)
        ) as endpoint,
    ):
        if recipe.render_js and endpoint is None:
            # Crawl4AI starts its own browser when cdp_url is absent. A missing
            # controlled endpoint must never reach that startup path.
            raise SourceScanError("browser_unavailable", "Lightpanda did not provide an endpoint.")
        if endpoint is not None:
            browser_config.cdp_url = endpoint.url
            browser_config.use_managed_browser = True
            browser_config.create_isolated_context = True
            browser_config.cdp_cleanup_on_close = True
            browser_config.cache_cdp_connection = False
        with TemporaryDirectory(prefix="reader-crawl-") as directory:
            crawler = AsyncWebCrawler(
                config=browser_config, crawler_strategy=strategy, base_directory=directory
            )
            route_tasks: set[asyncio.Task] = set()
            route_contexts = []
            crawl_task: asyncio.Task | None = None
            closing = False

            async def close_crawler():
                nonlocal closing
                # Playwright starts route callbacks in independent tasks. A
                # browser close does not cancel their HTTPX requests or tasks
                # queued on network_slot; drain them before returning the client.
                closing = True
                pending = (*route_tasks, *((crawl_task,) if crawl_task is not None else ()))
                for task in pending:
                    task.cancel()

                async def remove_routes():
                    from playwright.async_api import Error as PlaywrightError

                    for context in route_contexts:
                        try:
                            await context.unroute_all(behavior="ignoreErrors")
                        except PlaywrightError:
                            logger.debug("Browser routes already unavailable", exc_info=True)

                if endpoint is not None:
                    # Terminate the owned engine BEFORE any CDP close operation.
                    # SIGSTOP or a wedged CDP connection cannot prevent TERM/KILL.
                    try:
                        await endpoint.stop()
                    finally:
                        from playwright.async_api import Error as PlaywrightError

                        try:
                            try:
                                try:
                                    await asyncio.wait_for(
                                        asyncio.gather(*pending, return_exceptions=True),
                                        _BROWSER_CLOSE_TIMEOUT_SECONDS,
                                    )
                                finally:
                                    await asyncio.wait_for(
                                        remove_routes(), _BROWSER_CLOSE_TIMEOUT_SECONDS
                                    )
                            finally:
                                await asyncio.wait_for(
                                    crawler.__aexit__(None, None, None),
                                    _BROWSER_CLOSE_TIMEOUT_SECONDS,
                                )
                        except PlaywrightError:
                            # The engine was deliberately stopped above. A closed
                            # target during protocol disposal is expected.
                            logger.debug("Stopped browser protocol closed", exc_info=True)
                        except TimeoutError as exc:
                            raise SourceScanError(
                                "browser_cleanup_failed", "Browser protocol cleanup timed out."
                            ) from exc
                        finally:
                            await _stop_isolated_driver(crawler)
                    return

                await asyncio.gather(*pending, return_exceptions=True)
                try:
                    if route_contexts:
                        await remove_routes()
                finally:
                    await crawler.__aexit__(None, None, None)

            if recipe.render_js:
                request_count = 0
                response_bytes = 0
                network_slot = asyncio.Semaphore(2)
                origin = (urlsplit(url).scheme, urlsplit(url).netloc.lower())
                navigation_origins = {origin}

                async def setup(page, context, **_kwargs):
                    route_contexts.append(context)

                    async def reject_socket(socket):
                        await socket.close()

                    await context.route_web_socket("**/*", reject_socket)
                    await context.add_init_script("""
                        if (navigator.serviceWorker) {
                          navigator.serviceWorker.register = () => Promise.reject(
                            new Error('Reader disables service workers'));
                        }
                    """)

                    async def handle_request(route):
                        nonlocal error, request_count, response_bytes, fetched_response
                        request = route.request
                        parsed = urlsplit(request.url)
                        if request.method != "GET" or parsed.scheme not in {"http", "https"}:
                            await route.abort()
                            return
                        if request.resource_type in {"image", "media", "font"}:
                            await route.abort()
                            return
                        if request.is_navigation_request() and (
                            request.frame != page.main_frame
                            or (parsed.scheme, parsed.netloc.lower()) not in navigation_origins
                        ):
                            await route.abort()
                            return
                        request_count += 1
                        if request_count > 80:
                            error = SourceScanError(
                                "web_request_limit", "Page exceeded 80 requests."
                            )
                            await route.abort()
                            return
                        try:
                            async with network_slot:
                                if closing:
                                    return
                                if not await robots_allows(request.url, maximum_bytes):
                                    raise SourceScanError(
                                        "robots_disallowed", "Request disallowed."
                                    )
                                if closing:
                                    return
                                response = await fetch(
                                    client,
                                    request.url,
                                    headers={"User-Agent": _USER_AGENT},
                                    timeout=15.0,
                                    max_response_bytes=maximum_bytes,
                                )
                                response_bytes += len(response.content)
                                if response_bytes > min(20 * 1024 * 1024, maximum_bytes * 4):
                                    raise SourceScanError(
                                        "response_too_large", "Page exceeded total transfer limit."
                                    )
                                if not await robots_allows(str(response.url), maximum_bytes):
                                    raise SourceScanError(
                                        "robots_disallowed", "Redirect disallowed."
                                    )
                                # Preserve browser URL/base semantics through safe redirects.
                                if str(response.url) != request.url:
                                    if browser_engine == "lightpanda":
                                        # LP 0.4.0 follows a fulfilled redirect outside
                                        # Fetch interception. Native network remains
                                        # blocked; do not silently change URL semantics.
                                        raise SourceScanError(
                                            "browser_redirect_unsupported",
                                            "Lightpanda cannot safely replay this redirect.",
                                        )
                                    if request.is_navigation_request():
                                        final = urlsplit(str(response.url))
                                        navigation_origins.add((final.scheme, final.netloc.lower()))
                                    await route.fulfill(
                                        status=302, headers={"location": str(response.url)}, body=""
                                    )
                                    return
                                if request.is_navigation_request():
                                    fetched_response = response
                                    raise_for_provider_status(response, "web")
                                safe_headers = dict(response.headers)
                                for key in (
                                    "content-encoding",
                                    "content-length",
                                    "transfer-encoding",
                                    "set-cookie",
                                    "connection",
                                ):
                                    safe_headers.pop(key, None)
                                await route.fulfill(
                                    status=response.status_code,
                                    headers=safe_headers,
                                    body=response.content,
                                )
                        except (SourceScanError, UnsafeSourceUrl, httpx.HTTPError) as exc:
                            if request.is_navigation_request() or isinstance(exc, SourceScanError):
                                error = exc
                            await route.abort()

                    async def route_request(route):
                        # A callback already scheduled by Playwright may only
                        # enter after shutdown begins; it must never use client.
                        if closing:
                            return
                        task = asyncio.current_task()
                        route_tasks.add(task)
                        try:
                            await handle_request(route)
                        finally:
                            route_tasks.discard(task)

                    await context.route("**/*", route_request)
                    return page

                crawler.crawler_strategy.set_hook("on_page_context_created", setup)
                if recipe.actions:

                    async def execute_actions(page, context, **_kwargs):
                        nonlocal error, action_error
                        # Read Reader's generated function result directly; page
                        # HTML, logs and exception text cannot attest an action failure.
                        if error is not None:
                            raise error
                        if fetched_response is not None and fetched_response.is_error:
                            try:
                                raise_for_provider_status(fetched_response, "web")
                            except SourceScanError as exc:
                                error = exc
                                raise
                        outcome = await page.evaluate(action_script(recipe, completion_token))
                        if (
                            not isinstance(outcome, dict)
                            or outcome.get("token") != completion_token
                        ):
                            error = SourceScanError(
                                "web_crawl_failed", "Browser action result was incomplete."
                            )
                            raise error
                        if outcome.get("status") == "failed":
                            index, reason = outcome.get("index"), outcome.get("reason")
                            if (
                                type(index) is not int
                                or not 0 <= index < len(recipe.actions)
                                or reason
                                not in {
                                    "action_target_missing",
                                    "action_wait_timeout",
                                    "form_action_forbidden",
                                    "selector_invalid",
                                }
                            ):
                                error = SourceScanError(
                                    "web_crawl_failed", "Browser action result was invalid."
                                )
                                raise error
                            action = recipe.actions[index]
                            action_error = SourceScanError(
                                "web_rule_action_failed",
                                f"Rule action {index + 1} failed: {reason}.",
                                evidence={
                                    "action_index": index,
                                    "kind": action.kind,
                                    "selector": action.selector,
                                    "reason": reason,
                                },
                            )
                            return page
                        if outcome.get("status") != "complete":
                            error = SourceScanError(
                                "web_crawl_failed", "Browser actions did not complete."
                            )
                            raise error
                        return page

                    # Runs once, before HTML capture and native field extraction.
                    crawler.crawler_strategy.set_hook("before_retrieve_html", execute_actions)
            try:
                primary_error: BaseException | None = None
                try:

                    async def run_crawler():
                        await crawler.__aenter__()
                        return await crawler.arun(url=url, config=run_config)

                    async with asyncio.timeout(_CRAWL_TIMEOUT_SECONDS):
                        if endpoint is not None:
                            # Crawl4AI's own cancellation finally can await CDP.
                            # Keep the host responsive so it can terminate its
                            # engine before draining that library-owned cleanup.
                            crawl_task = asyncio.create_task(run_crawler())
                            result = await asyncio.shield(crawl_task)
                        else:
                            result = await run_crawler()
                    if error:
                        raise error
                    small_page_only = (result.error_message or "").startswith(
                        (
                            "Blocked by anti-bot protection: Structural: minimal_text",
                            "Blocked by anti-bot protection: Near-empty content",
                        )
                    )
                    # Sparse update lists are valid; Reader checks URL/title rows.
                    # Do not override challenge markers, HTTP blocks or JS failures.
                    if not result.success and not small_page_only:
                        raise SourceScanError(
                            "web_crawl_failed", (result.error_message or "Crawl4AI failed")[:500]
                        )
                    html = result.html or ""
                    if len(html.encode("utf-8")) > maximum_bytes:
                        raise SourceScanError(
                            "response_too_large", "Rendered DOM exceeded size limit."
                        )
                    if action_error is not None:
                        # Keep Crawl4AI's challenge and Reader's resource failures
                        # authoritative even when the candidate action also failed.
                        raise action_error
                except BaseException as exc:
                    primary_error = exc
                    raise
                finally:
                    # Cleanup is outside the crawl deadline and shielded from
                    # caller cancellation; the slot is held until it completes.
                    closing = True
                    await _await_cleanup(close_crawler(), primary_error or error)
                response = httpx.Response(
                    status_code=result.status_code or 200,
                    headers=dict(result.response_headers or {}),
                    text=html,
                    request=httpx.Request("GET", result.redirected_url or url),
                )
                if fetched_response is not None:
                    response = httpx.Response(
                        status_code=fetched_response.status_code,
                        headers=dict(fetched_response.headers),
                        text=html,
                        request=httpx.Request("GET", str(fetched_response.url)),
                    )
                rows = json.loads(result.extracted_content or "[]")
                return CrawledPage(response=response, rows=rows)
            except TimeoutError as exc:
                raise SourceScanError("web_crawl_timeout", "Crawl4AI exceeded 60 seconds.") from exc
