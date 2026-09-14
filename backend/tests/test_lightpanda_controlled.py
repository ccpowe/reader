"""Explicit local-fixture acceptance for Reader's isolated Lightpanda engine.

READER_TEST_LIGHTPANDA_BINARY enables real browser processes. No provider or
public website is contacted: source HTTP is fulfilled by MockTransport.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import time
from pathlib import Path

import httpx
import pytest

from app.ingestion import browser_runtime, url_safety, web_crawl
from app.ingestion.models import SourceScanError
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.web_crawl_types import PageRecipe

BINARY = os.environ.get("READER_TEST_LIGHTPANDA_BINARY")
pytestmark = pytest.mark.skipif(not BINARY, reason="Explicit local Lightpanda acceptance opt-in")
URL = "https://reader-fixture.invalid/blog"


async def test_large_document_actions_use_lightpanda_for_inspection_and_fixed_scan(monkeypatch):
    """Exercise ordinary production entry points without per-call engine overrides."""
    from playwright.async_api import BrowserType

    from app.core.settings import Settings
    from app.ingestion.models import SourceScanRequest
    from app.ingestion.web_crawl_types import WebRule
    from app.ingestion.web_rules import execute_web_rule
    from app.web_rule_agent import browser

    settings = Settings(_env_file=None, ingestion_lightpanda_executable_path=BINARY)
    monkeypatch.setattr(web_crawl, "get_settings", lambda: settings)

    async def no_browser_launch(*_args, **_kwargs):
        pytest.fail("Reader must connect to Lightpanda over CDP, never launch another browser")

    monkeypatch.setattr(BrowserType, "launch", no_browser_launch)
    monkeypatch.setattr(BrowserType, "launch_persistent_context", no_browser_launch)
    processes, requests = [], []
    real_spawn = browser_runtime._spawn

    async def spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        return process

    async def dns(_hostname, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(browser_runtime, "_spawn", spawn)
    monkeypatch.setattr(url_safety._DNS_RESOLVER, "getaddrinfo", dns)
    html = (
        """<html><body><main><h1>Fixture updates</h1>
    <article><a href='/post/one'><h2>Server placeholder</h2></a></article>
    <button id='more'>Load more</button></main><script>
    document.querySelector('h2').textContent = 'Rendered first title';
    document.querySelector('#more').onclick = () => {
      const item = document.createElement('article');
      item.innerHTML = '<a href="/post/two"><h2>Loaded second title</h2></a>';
      document.querySelector('main').appendChild(item);
    };
    </script><!--"""
        + "x" * 1_134_511
        + "--></body></html>"
    )
    assert len(html.encode()) > 1024 * 1024

    async def response(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        assert str(request.url) == URL
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    def public_client(**kwargs):
        return url_safety.PublicAsyncClient(transport=httpx.MockTransport(response), **kwargs)

    monkeypatch.setattr(browser, "PublicAsyncClient", public_client)
    actions = [
        {"kind": "click", "selector": "#more"},
        {"kind": "wait", "selector": "article", "min_count": 2},
        {"kind": "scroll", "steps": 1},
    ]
    rule = WebRule.model_validate(
        {
            "id": "large-lightpanda-fixture",
            "hosts": ["reader-fixture.invalid"],
            "index_paths": ["/blog"],
            "listing": {
                "actions": actions,
                "extraction": {
                    "baseSelector": "article",
                    "fields": [
                        {"name": "url", "type": "attribute", "selector": "a", "attribute": "href"},
                        {"name": "title", "type": "text", "selector": "h2"},
                    ],
                },
            },
        }
    )
    evidence = {"html_bytes": len(html.encode()), "launch_flags": browser_runtime._ENGINE_FLAGS}
    session = browser.SourceBrowserSession(settings, URL)
    try:
        report = await session.inspect(URL, actions=actions, selector="article", mode="nodes")
        evidence["inspection"] = report
        assert report["status"] == "pass" and report["engine"] == "lightpanda"
        assert [node["text"] for node in report["nodes"]] == [
            "Rendered first title",
            "Loaded second title",
        ]
        async with public_client() as client:
            adapter = WebBlogSourceAdapter(WebBlogSourceConfig(url=URL, rule=rule), client)
            page = await adapter.scan_page(SourceScanRequest())
            evidence["items"] = [
                {"title": item.title, "url": item.external_url} for item in page.items
            ]
        assert evidence["items"] == [
            {"title": "Rendered first title", "url": "https://reader-fixture.invalid/post/one"},
            {"title": "Loaded second title", "url": "https://reader-fixture.invalid/post/two"},
        ]
        async with public_client() as client:
            trial = await execute_web_rule(
                source_url=URL, raw_rule=rule.model_dump(), client=client
            )
        evidence["trial"] = trial
        assert trial["status"] == "completed" and trial["first_window_completed"]
        assert trial["items"] == evidence["items"]
        assert len(processes) == 6  # sandboxed version + browser for each production entry point
        assert all(process.returncode is not None for process in processes)
        assert not web_crawl._slot().locked()
    finally:
        await session.aclose()
        evidence.update(
            requests=requests, processes=[{"pid": p.pid, "exit": p.returncode} for p in processes]
        )
        if output := os.environ.get("READER_TEST_BROWSER_EVIDENCE"):
            Path(output).with_name("reader-lightpanda-shared-evidence.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )


async def test_actual_crawl_javascript_redirect_file_and_cancel_cleanup(monkeypatch, tmp_path):
    from bs4 import BeautifulSoup

    evidence = {"http_requests": [], "native_requests": [], "file_probe": {}, "processes": []}
    processes = []
    real_spawn = browser_runtime._spawn

    async def spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(browser_runtime, "_spawn", spawn)

    async def dns(_hostname, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(url_safety._DNS_RESOLVER, "getaddrinfo", dns)

    async def sentinel(reader, writer):
        evidence["native_requests"].append((await reader.read(1000)).decode(errors="replace"))
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(sentinel, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    secret = tmp_path / "host-secret.txt"
    secret.write_text("HOST_PRIVATE_FIXTURE")
    hold_started, hold_cancelled = asyncio.Event(), asyncio.Event()

    async def handler(request):
        target = str(request.url)
        evidence["http_requests"].append(target)
        if target.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if target.endswith("/redirect"):
            return httpx.Response(302, headers={"location": "/blog"})
        if target.endswith("/fixture.js"):
            return httpx.Response(
                200,
                text="document.querySelector('h2').textContent='Rendered title';",
                headers={"content-type": "application/javascript"},
            )
        if target.endswith("/hold.js"):
            hold_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                hold_cancelled.set()
        if target.endswith("/hold"):
            return httpx.Response(
                200,
                text='<html><body><script src="/hold.js"></script></body></html>',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            200,
            text=f"""<html><body>
            <article><a href="/post">Post</a><h2>Initial</h2></article>
            <output id="probe"></output><script src="/fixture.js"></script><script>
            const probe = document.getElementById('probe');
            probe.setAttribute('data-file-attempted', 'true');
            try {{
              fetch({json.dumps(secret.as_uri())}).then(r => r.text()).then(text => {{
                probe.setAttribute('data-file-result', 'resolved');
                probe.setAttribute('data-file-body', text);
              }}).catch(() => probe.setAttribute('data-file-result', 'rejected'));
            }} catch (_) {{ probe.setAttribute('data-file-result', 'rejected'); }}
            probe.setAttribute('data-ws-attempted', 'true');
            try {{ new WebSocket('ws://127.0.0.1:{port}/socket'); }} catch (_) {{}}
            fetch('http://127.0.0.1:{port}/fetch').catch(() => {{}});
            </script></body></html>""",
            headers={"content-type": "text/html"},
        )

    recipe = PageRecipe.model_validate(
        {
            "render_js": True,
            "actions": [{"kind": "wait", "selector": "#probe[data-file-result]"}],
            "extraction": {
                "baseSelector": "article",
                "fields": [
                    {"name": "url", "type": "attribute", "attribute": "href", "selector": "a"},
                    {"name": "title", "type": "text", "selector": "h2"},
                ],
            },
        }
    )

    async def crawl(client, url, active_recipe=recipe):
        adapter = WebBlogSourceAdapter(WebBlogSourceConfig(url=url), client)
        return await web_crawl.crawl_page(
            client,
            url,
            active_recipe,
            maximum_bytes=100000,
            robots_allows=adapter._robots_allows,
            browser_engine="lightpanda",
            lightpanda_executable_path=BINARY,
        )

    try:
        async with url_safety.PublicAsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await crawl(client, URL)
            assert result.rows == [{"url": "/post", "title": "Rendered title"}]
            probe = BeautifulSoup(result.response.text, "html.parser").select_one("#probe")
            evidence["file_probe"] = dict(probe.attrs)
            assert probe["data-file-attempted"] == "true"
            assert probe["data-file-result"] == "rejected"
            assert probe["data-ws-attempted"] == "true"
            assert "HOST_PRIVATE_FIXTURE" not in result.response.text
            with pytest.raises(SourceScanError) as redirect:
                await crawl(client, "https://reader-fixture.invalid/redirect")
            assert redirect.value.code == "browser_redirect_unsupported"
            evidence["redirect_code"] = redirect.value.code
            plain_recipe = recipe.model_copy(update={"actions": []})
            task = asyncio.create_task(
                crawl(client, "https://reader-fixture.invalid/hold", plain_recipe)
            )
            try:
                await asyncio.wait_for(hold_started.wait(), 10)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert hold_cancelled.is_set()
                assert not client.is_closed
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert evidence["native_requests"] == []
        assert not web_crawl._slot().locked()
        assert all(process.returncode is not None for process in processes)
    finally:
        server.close()
        await server.wait_closed()
        evidence["processes"] = [{"pid": p.pid, "exit": p.returncode} for p in processes]
        evidence["slot_locked"] = web_crawl._slot().locked()
        if output := os.environ.get("READER_TEST_BROWSER_EVIDENCE"):
            Path(output).write_text(json.dumps(evidence, indent=2) + "\n")


async def test_native_unintercepted_network_cannot_bypass_engine_blocks():
    from playwright.async_api import async_playwright

    arrivals = []

    async def sentinel(reader, writer):
        arrivals.append(await reader.read(1000))
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(sentinel, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with browser_runtime.browser_endpoint("lightpanda", BINARY) as endpoint:
            async with async_playwright() as playwright:
                engine = await playwright.chromium.connect_over_cdp(endpoint.url)
                context = await engine.new_context()
                page = await context.new_page()

                async def fixture(route):
                    # Only the fixture itself is intercepted. Every attempted
                    # loopback exit below exercises the native fallback policy.
                    await route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=f"""
                        <output id="probe" data-attempted="false"></output><script>
                        document.getElementById('probe').setAttribute('data-attempted','true');
                        try {{ new WebSocket('ws://127.0.0.1:{port}/socket'); }} catch (_) {{}}
                        fetch('http://127.0.0.1:{port}/fetch').catch(()=>{{}});
                        const x = new XMLHttpRequest();
                        x.open('GET','http://127.0.0.1:{port}/xhr'); x.send();
                        </script><script src="http://127.0.0.1:{port}/subresource"></script>""",
                    )

                await context.route(URL, fixture)
                try:
                    await page.goto(URL, wait_until="domcontentloaded", timeout=5000)
                    assert await page.locator("#probe").get_attribute("data-attempted") == "true"
                    await context.unroute_all(behavior="wait")
                    with pytest.raises(Exception):
                        await page.goto(f"http://127.0.0.1:{port}/unrouted", timeout=2000)
                finally:
                    await context.close()
                    await engine.close()
        assert arrivals == []
    finally:
        server.close()
        await server.wait_closed()


async def test_stopped_owned_engine_cannot_block_termination_and_cleanup(monkeypatch):
    """SIGSTOP only this test's isolated browser tree to wedge its CDP socket."""
    owned = []
    signals = []
    real_spawn, real_killpg = browser_runtime._spawn, os.killpg
    ready, drained = asyncio.Event(), asyncio.Event()
    before_tasks = set(asyncio.all_tasks())

    async def spawn(args, *rest, **kwargs):
        process = await real_spawn(args, *rest, **kwargs)
        if "serve" in args:
            owned.append(process)
        return process

    def killpg(pid, sig):
        if any(process.pid == pid for process in owned):
            signals.append({"pid": pid, "signal": sig.name})
        return real_killpg(pid, sig)

    monkeypatch.setattr(browser_runtime, "_spawn", spawn)
    monkeypatch.setattr(browser_runtime.os, "killpg", killpg)

    async def dns(_hostname, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(url_safety._DNS_RESOLVER, "getaddrinfo", dns)

    async def response(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/hold.js":
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()
        return httpx.Response(
            200,
            text='<html><body><script src="/hold.js"></script></body></html>',
            headers={"content-type": "text/html"},
        )

    def descendants(pid):
        path = Path(f"/proc/{pid}/task/{pid}/children")
        children = [int(value) for value in path.read_text().split()] if path.exists() else []
        return [*children, *(child for value in children for child in descendants(value))]

    recipe = PageRecipe.model_validate({"render_js": True, "extraction": {"baseSelector": "html"}})
    stopped = []
    elapsed = None
    try:
        async with url_safety.PublicAsyncClient(transport=httpx.MockTransport(response)) as client:
            adapter = WebBlogSourceAdapter(WebBlogSourceConfig(url=URL), client)
            task = asyncio.create_task(
                web_crawl.crawl_page(
                    client,
                    URL,
                    recipe,
                    maximum_bytes=100000,
                    robots_allows=adapter._robots_allows,
                    browser_engine="lightpanda",
                    lightpanda_executable_path=BINARY,
                )
            )
            try:
                await asyncio.wait_for(ready.wait(), 10)
                assert len(owned) == 1
                stopped = [owned[0].pid, *descendants(owned[0].pid)]
                # Descendants are sampled exclusively beneath our own bwrap.
                assert len(stopped) >= 2
                for pid in reversed(stopped):
                    os.kill(pid, signal.SIGSTOP)
                started = time.monotonic()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(task), 20)
                elapsed = time.monotonic() - started
                assert drained.is_set() and not client.is_closed
            finally:
                if not task.done():
                    for process in owned:
                        if process.returncode is None:
                            real_killpg(process.pid, signal.SIGKILL)
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert owned[0].returncode == -signal.SIGKILL
        assert [item["signal"] for item in signals] == ["SIGTERM", "SIGKILL"]
        assert not web_crawl._slot().locked()
        for _ in range(100):
            if not any(Path(f"/proc/{pid}").exists() for pid in stopped):
                break
            await asyncio.sleep(0.02)
        assert not any(Path(f"/proc/{pid}").exists() for pid in stopped)
        await asyncio.sleep(0)
        assert not [task for task in asyncio.all_tasks() - before_tasks if not task.done()]
    finally:
        if output := os.environ.get("READER_TEST_BROWSER_EVIDENCE"):
            path = Path(output).with_name("reader-lightpanda-stopped-evidence.json")
            path.write_text(
                json.dumps(
                    {
                        "stopped_owned_pids": stopped,
                        "signals": signals,
                        "cleanup_seconds": elapsed,
                        "exit_codes": [process.returncode for process in owned],
                        "remaining_owned_pids": [
                            pid for pid in stopped if Path(f"/proc/{pid}").exists()
                        ],
                        "slot_locked": web_crawl._slot().locked(),
                    },
                    indent=2,
                )
                + "\n"
            )
