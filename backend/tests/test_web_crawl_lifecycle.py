"""Controlled regressions for Playwright callbacks outliving a borrowed client."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from app.core.settings import Settings
from app.ingestion import web_crawl
from app.ingestion.models import SourceScanError
from app.ingestion.web_crawl_types import PageRecipe

URL = "https://example.com/blog"
RECIPE = PageRecipe.model_validate(
    {
        "render_js": True,
        "extraction": {
            "baseSelector": "article",
            "fields": [{"name": "title", "type": "text", "selector": "h2"}],
        },
    }
)


class BrowserHarness:
    def __init__(self):
        self.instances = []
        self.callbacks = []
        self.requests = []
        self.active_fetches = 0
        self.cancelled_fetches = 0
        self.ready = asyncio.Event()
        self.finish = asyncio.Event()
        self.fetch_cleanup_started = asyncio.Event()
        self.fetch_cleanup_release = asyncio.Event()
        self.fetch_cleanup_release.set()
        self.never_finish_fetch = asyncio.Event()
        self.failure = None
        self.result_error = ""
        self.close_failure = None
        self.callback_count = 8
        self.closed = 0
        self.endpoints = []

    async def robots_allows(self, *_args):
        return True

    async def fetch(self, client, target, **_kwargs):
        assert not client.is_closed, "A route callback outlived the HTTP client"
        self.requests.append(target)
        if target != URL:
            self.active_fetches += 1
            if self.active_fetches == 2:
                self.ready.set()
            try:
                await self.never_finish_fetch.wait()
            except asyncio.CancelledError:
                self.cancelled_fetches += 1
                self.fetch_cleanup_started.set()
                await self.fetch_cleanup_release.wait()
                raise
            finally:
                self.active_fetches -= 1
        return httpx.Response(
            200,
            text="<article><h2>Update</h2></article>",
            request=httpx.Request("GET", target),
        )

    def route(self, target, page, *, navigation=False):
        async def noop(**_kwargs):
            return None

        return SimpleNamespace(
            request=SimpleNamespace(
                url=target,
                method="GET",
                resource_type="document" if navigation else "script",
                frame=page.main_frame,
                is_navigation_request=lambda: navigation,
            ),
            abort=noop,
            fulfill=noop,
        )

    def crawler_type(self):
        harness = self

        class FakeCrawler:
            def __init__(self, **_kwargs):
                assert _kwargs["config"].cdp_url
                assert _kwargs["config"].use_managed_browser is True
                self.hooks = {}
                self.crawler_strategy = SimpleNamespace(
                    set_hook=self.hooks.__setitem__,
                    browser_manager=SimpleNamespace(playwright=None),
                )
                self.page = SimpleNamespace(main_frame=object(), evaluate=self.evaluate)
                self.context = SimpleNamespace(
                    route_web_socket=self.noop,
                    add_init_script=self.noop,
                    route=self.register_route,
                    unroute_all=self.unroute_all,
                )
                self.handler = None
                self.tasks = []
                harness.instances.append(self)

            async def noop(self, *_args, **_kwargs):
                return None

            async def evaluate(self, token):
                return {
                    "token": token,
                    "status": "failed",
                    "index": 0,
                    "reason": "action_target_missing",
                }

            async def register_route(self, _pattern, handler):
                self.handler = handler

            async def unroute_all(self, **kwargs):
                assert kwargs == {"behavior": "ignoreErrors"}
                assert all(task.done() for task in self.tasks)
                # Playwright can already have queued another callback before
                # unroute reaches the browser. It must not start a fresh fetch.
                await self.handler(harness.route(URL + "/late.js", self.page))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                assert all(task.done() for task in self.tasks)
                assert harness.active_fetches == 0
                harness.closed += 1
                if harness.close_failure is not None:
                    raise harness.close_failure

            async def arun(self, **_kwargs):
                await self.hooks["on_page_context_created"](self.page, self.context)
                await self.handler(harness.route(URL, self.page, navigation=True))
                self.tasks = [
                    asyncio.create_task(self.handler(harness.route(f"{URL}/{index}.js", self.page)))
                    for index in range(harness.callback_count)
                ]
                harness.callbacks.extend(self.tasks)
                if self.tasks:
                    await harness.ready.wait()
                await harness.finish.wait()
                if harness.failure is not None:
                    raise harness.failure
                if hook := self.hooks.get("before_retrieve_html"):
                    await hook(self.page, self.context)
                return SimpleNamespace(
                    success=not harness.result_error,
                    error_message=harness.result_error,
                    html="<article><h2>Update</h2></article>",
                    status_code=200,
                    response_headers={},
                    redirected_url=URL,
                    extracted_content='[{"title":"Update"}]',
                )

        return FakeCrawler

    def install(self, monkeypatch):
        monkeypatch.setattr(web_crawl, "get_settings", lambda: Settings(_env_file=None))

        @asynccontextmanager
        async def endpoint(engine, executable_path, **kwargs):
            self.endpoints.append({"engine": engine, "path": executable_path, **kwargs})

            async def stop():
                pass

            yield SimpleNamespace(url="ws://fixture", stop=stop)

        monkeypatch.setattr(web_crawl, "browser_endpoint", endpoint)
        package = ModuleType("crawl4ai")
        package.AsyncWebCrawler = self.crawler_type()
        package.BrowserConfig = SimpleNamespace
        package.CacheMode = SimpleNamespace(DISABLED="disabled")
        package.CrawlerRunConfig = SimpleNamespace
        package.JsonCssExtractionStrategy = lambda *_args, **_kwargs: None
        strategy = ModuleType("crawl4ai.async_crawler_strategy")
        strategy.AsyncCrawlerStrategy = object
        models = ModuleType("crawl4ai.models")
        models.AsyncCrawlResponse = SimpleNamespace
        monkeypatch.setitem(sys.modules, "crawl4ai", package)
        monkeypatch.setitem(sys.modules, "crawl4ai.async_crawler_strategy", strategy)
        monkeypatch.setitem(sys.modules, "crawl4ai.models", models)

    async def crawl(self, client, recipe=RECIPE):
        return await web_crawl.crawl_page(
            client,
            URL,
            recipe,
            maximum_bytes=100_000,
            robots_allows=self.robots_allows,
            fetch=self.fetch,
        )


@pytest.mark.parametrize("outcome", ["success", "navigation_error", "timeout", "cancel"])
async def test_crawl_drains_active_and_queued_callbacks_before_return(monkeypatch, outcome):
    harness = BrowserHarness()
    harness.install(monkeypatch)
    if outcome == "navigation_error":
        harness.failure = SourceScanError("web_crawl_failed", "Navigation failed")
    if outcome == "timeout":
        monkeypatch.setattr(web_crawl, "_CRAWL_TIMEOUT_SECONDS", 0.1)
    async with httpx.AsyncClient() as client:
        crawl = asyncio.create_task(harness.crawl(client))
        try:
            await asyncio.wait_for(harness.ready.wait(), timeout=2)
            assert harness.active_fetches == 2
            assert len(harness.callbacks) == 8
            if outcome == "cancel":
                crawl.cancel()
            elif outcome != "timeout":
                harness.finish.set()
            if outcome == "success":
                result = await asyncio.wait_for(crawl, timeout=2)
                assert result.rows == [{"title": "Update"}]
            elif outcome == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(crawl, timeout=2)
            else:
                with pytest.raises(SourceScanError) as caught:
                    await asyncio.wait_for(crawl, timeout=2)
                if outcome == "navigation_error":
                    assert caught.value is harness.failure
                else:
                    assert caught.value.code == "web_crawl_timeout"
            assert harness.cancelled_fetches == 2
            assert harness.active_fetches == 0
            assert all(task.done() for task in harness.callbacks)
            assert harness.closed == 1
            assert len(harness.requests) == 3
            assert not web_crawl._slot().locked()
        finally:
            if not crawl.done():
                crawl.cancel()
            await asyncio.gather(crawl, return_exceptions=True)
    # Even a callback dispatched after the caller closes its borrowed client
    # cannot re-enter the network path.
    first = harness.instances[0]
    await first.handler(harness.route(URL + "/after-close.js", first.page))
    assert len(harness.requests) == 3
    # A cancelled/failed crawl must also release the singleton browser slot.
    harness.callback_count = 0
    harness.failure = None
    harness.finish.set()
    async with httpx.AsyncClient() as next_client:
        result = await asyncio.wait_for(harness.crawl(next_client), timeout=2)
    assert result.rows == [{"title": "Update"}]
    assert harness.closed == 2


async def test_default_dynamic_crawl_uses_shared_settings_endpoint(monkeypatch):
    harness = BrowserHarness()
    harness.install(monkeypatch)
    settings = Settings(_env_file=None, ingestion_lightpanda_executable_path="/opt/reader/browser")
    monkeypatch.setattr(web_crawl, "get_settings", lambda: settings)
    harness.callback_count = 0
    harness.finish.set()
    async with httpx.AsyncClient() as client:
        await harness.crawl(client)
    assert harness.endpoints == [
        {"engine": "lightpanda", "path": "/opt/reader/browser", "expected_identity": None}
    ]


async def test_repeated_cancellation_waits_for_fetch_cleanup_and_keeps_slot(monkeypatch):
    harness = BrowserHarness()
    harness.install(monkeypatch)
    harness.fetch_cleanup_release.clear()
    async with httpx.AsyncClient() as client:
        crawl = asyncio.create_task(harness.crawl(client))
        try:
            await asyncio.wait_for(harness.ready.wait(), timeout=2)
            crawl.cancel()
            await asyncio.wait_for(harness.fetch_cleanup_started.wait(), timeout=2)
            crawl.cancel()
            await asyncio.sleep(0)
            assert not crawl.done()
            assert web_crawl._slot().locked()
            assert not client.is_closed
            assert harness.closed == 0
            harness.fetch_cleanup_release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(crawl, timeout=2)
            assert all(task.done() for task in harness.callbacks)
            assert harness.closed == 1
            assert not web_crawl._slot().locked()
        finally:
            harness.fetch_cleanup_release.set()
            if not crawl.done():
                crawl.cancel()
            await asyncio.gather(crawl, return_exceptions=True)


async def test_cleanup_error_preserves_navigation_failure(monkeypatch):
    harness = BrowserHarness()
    harness.install(monkeypatch)
    harness.failure = SourceScanError("robots_disallowed", "Redirect target disallowed")
    harness.close_failure = RuntimeError("Browser close failed")
    harness.finish.set()
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceScanError) as caught:
            await asyncio.wait_for(harness.crawl(client), timeout=2)
    assert caught.value is harness.failure
    assert all(task.done() for task in harness.callbacks)
    assert harness.active_fetches == 0
    assert not web_crawl._slot().locked()


@pytest.mark.parametrize("failure", ["returned_failure", "action_failure"])
async def test_cleanup_error_preserves_returned_page_and_action_failures(monkeypatch, failure):
    harness = BrowserHarness()
    harness.install(monkeypatch)
    harness.close_failure = RuntimeError("Browser close failed")
    harness.finish.set()
    recipe = RECIPE
    if failure == "returned_failure":
        harness.result_error = "Blocked by anti-bot protection: challenge"
        expected = "web_crawl_failed"
    else:
        recipe = PageRecipe.model_validate(
            {**RECIPE.model_dump(), "actions": [{"kind": "click", "selector": ".missing"}]}
        )
        monkeypatch.setattr(web_crawl, "action_script", lambda _recipe, token: token)
        expected = "web_rule_action_failed"
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceScanError) as caught:
            await asyncio.wait_for(harness.crawl(client, recipe), timeout=2)
    assert caught.value.code == expected
    if failure == "returned_failure":
        assert harness.result_error in str(caught.value)
    else:
        assert caught.value.evidence["reason"] == "action_target_missing"
    assert all(task.done() for task in harness.callbacks)
    assert harness.active_fetches == 0
    assert harness.closed == 1
    assert not web_crawl._slot().locked()


@pytest.mark.skipif(
    not os.environ.get("READER_TEST_LIGHTPANDA_BINARY"),
    reason="Explicit local Lightpanda acceptance opt-in",
)
async def test_real_browser_drains_background_fetches_before_client_close():
    started = []
    cancelled = []
    active = set()
    pending = asyncio.Event()
    html = """<!doctype html><html><head><title>News</title></head><body>
      <main><h1>Engineering updates</h1><article><h2>New release</h2>
      <p>This release improves the article reader and its source subscriptions.
      Follow the engineering team for product announcements and progress.</p>
      </article></main><script>
        for (let index = 0; index < 8; index++) {
          fetch('/background/' + index).catch(() => {});
        }
      </script></body></html>"""

    async def allows(*_args):
        return True

    async def fetch(client, target, **_kwargs):
        assert not client.is_closed
        if target != URL:
            task = asyncio.current_task()
            active.add(task)
            started.append(target)
            try:
                await pending.wait()
            except asyncio.CancelledError:
                cancelled.append(target)
                raise
            finally:
                active.discard(task)
        return httpx.Response(
            200,
            text=html,
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", target),
        )

    async with httpx.AsyncClient() as client:
        result = await web_crawl.crawl_page(
            client,
            URL,
            RECIPE,
            maximum_bytes=100_000,
            robots_allows=allows,
            fetch=fetch,
            lightpanda_executable_path=os.environ["READER_TEST_LIGHTPANDA_BINARY"],
        )
        assert result.rows == [{"title": "New release"}]
        assert len(started) == 2
        assert set(cancelled) == set(started)
        assert not active
    await asyncio.sleep(0)
    assert len(started) == 2
    assert not web_crawl._slot().locked()
