"""Opt-in real CLI + Lightpanda tests using Reader MockTransport, never public sites."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

import httpx
import pytest

from app.core.settings import Settings
from app.ingestion import url_safety
from app.ingestion.models import SourceScanError
from app.web_rule_agent import cli_browser, cli_process

CLI = os.environ.get("READER_TEST_AGENT_BROWSER_BINARY")
LIGHTPANDA = os.environ.get("READER_TEST_LIGHTPANDA_BINARY")
pytestmark = pytest.mark.skipif(
    not CLI or not LIGHTPANDA, reason="Explicit CLI/Lightpanda fixture opt-in"
)
URL = "https://reader-fixture.invalid/blog"


@pytest.fixture
def fixture_http(monkeypatch):
    requests = []

    async def dns(_hostname, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(url_safety._DNS_RESOLVER, "getaddrinfo", dns)

    async def response(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /blocked\n")
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "/blog"})
        if request.url.path in {"/large", "/oversized"}:
            size = 1_200_000 if request.url.path == "/large" else 6_000_000
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body><main>Large fixture</main><!--"
                + "x" * size
                + "--></body></html>",
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="""
        <html><head><title>Reader fixture</title></head><body><main>
        <article><a href="/post">First article</a></article>
        <button id="more" onclick="this.textContent='Clicked';
        window.readerClicks=(window.readerClicks||0)+1">More</button>
        <a href="/next">Next</a></main></body></html>""",
        )

    def client(**kwargs):
        return url_safety.PublicAsyncClient(transport=httpx.MockTransport(response), **kwargs)

    monkeypatch.setattr(cli_browser, "PublicAsyncClient", client)
    return requests


def session():
    settings = Settings(_env_file=None, ingestion_lightpanda_executable_path=LIGHTPANDA)
    return cli_browser.CLIBrowserSession(settings, URL, cli_executable=CLI)


async def test_real_cli_keeps_page_state_after_commands_and_native_errors(fixture_http):
    browser = session()
    try:
        opened = await browser.command(["open", URL])
        assert opened["status"] == "ok", opened
        snapshot = await browser.command(["snapshot", "-u", "-s", "main"])
        assert snapshot["format"] == "snapshot" and "First article" in snapshot["output"], snapshot
        assert URL.replace("/blog", "/next") in cli_browser.visible_links(snapshot)
        clicked = await browser.command(["click", "#more"])
        assert clicked["status"] == "ok", clicked
        before = await browser.command(["eval", "window.readerClicks"])
        assert json.loads(before["output"]) == 1
        failed = await browser.command(
            [
                "eval",
                "(() => { document.querySelector('button').textContent='Changed before throw'; "
                "throw new Error('fixture actual error') })()",
            ]
        )
        assert failed["status"] == "error" and "fixture actual error" in failed["error"]["message"]
        assert failed["page_revision"] and failed["page_revision"] != before["page_revision"]
        after = await browser.command(["get", "text", "button"])
        assert after["output"] == "Changed before throw", after
        missing = await browser.command(["get", "attr"])
        assert missing["status"] == "error" and missing["error"]["source"] == "cli", missing
        assert json.loads((await browser.command(["eval", "window.readerClicks"]))["output"]) == 1
        assert fixture_http.count(URL) == 1
    finally:
        await browser.aclose()
        await browser.aclose()
    assert browser.cli.process.returncode is not None
    assert browser.endpoint.process.returncode is not None


async def test_real_top_navigations_refresh_budget_without_reopening_same_page(fixture_http):
    browser = session()
    try:
        assert (await browser.command(["open", URL]))["status"] == "ok"
        for argv, expected in [
            (["click", "a[href='/next']"], "/next"),
            (["back"], "/blog"),
            (["forward"], "/next"),
            (["eval", "location.href='/blog'"], "/blog"),
        ]:
            previous = browser.bridge.transfer
            previous.requests = 79
            result = await browser.command(argv)
            waited = await browser.command(["wait", "--url", "**" + expected])
            assert waited["status"] == "ok", (argv, result, waited)
            assert browser.bridge.transfer is not previous, (argv, result)
            assert browser.bridge.transfer.requests == 1, (argv, result)
            assert browser.url.endswith(expected), (argv, result)
        current = browser.bridge.transfer
        current.requests = 79
        await browser.command(["click", "#more"])
        assert browser.bridge.transfer is current and current.requests == 79
    finally:
        await browser.aclose()


async def test_real_large_document_transport_and_public_robots_redirect_boundaries(fixture_http):
    browser = session()
    try:
        target = URL.replace("/blog", "/large")
        result = await browser.command(["open", target], observed_urls=[target])
        assert result["status"] == "ok" and result["page_revision"], result
        assert (await browser.command(["get", "text", "main"]))["output"] == "Large fixture"
        for path, expected in [
            ("/blocked", "robots_disallowed"),
            ("/redirect", "browser_redirect_unsupported"),
            ("/oversized", "response_too_large"),
        ]:
            target = URL.replace("/blog", path)
            result = await browser.command(["open", target], observed_urls=[target])
            assert result["status"] == "error", result
            assert expected in [e["code"] for e in result["network_errors"]], result
        assert URL.replace("/blog", "/blocked") not in fixture_http
    finally:
        await browser.aclose()


def descendants(pid):
    result, pending = {pid}, [pid]
    while pending:
        parent = pending.pop()
        try:
            children = Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
        except FileNotFoundError:
            continue
        for child in map(int, children):
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


async def assert_exited(pids):
    for _ in range(50):
        if not any(alive(pid) for pid in pids):
            return
        await asyncio.sleep(0.1)
    pytest.fail(f"Owned processes still running: {[pid for pid in pids if alive(pid)]}")


@pytest.mark.parametrize("finish", ["cancel", "timeout", "close_failure", "close_busy"])
async def test_real_session_cleanup_for_cancellation_timeout_and_failed_close(
    fixture_http, finish, monkeypatch
):
    browser = session()
    try:
        assert (await browser.command(["open", URL]))["status"] == "ok"
        owned = descendants(browser.cli.process.pid) | descendants(browser.endpoint.process.pid)
        if finish == "close_failure":
            original = browser.cli.command

            async def failing(argv, **kwargs):
                if argv[-1] == "close":
                    raise RuntimeError("Controlled CLI close failure")
                return await original(argv, **kwargs)

            monkeypatch.setattr(browser.cli, "command", failing)
            await browser.aclose()
        elif finish == "close_busy":
            task = asyncio.create_task(browser.command(["wait", "#never-exists"]))
            await asyncio.sleep(0.1)
            assert browser.cli._lock.locked()
            await asyncio.wait_for(browser.aclose(), timeout=8)
            with pytest.raises(SourceScanError):
                await task
        elif finish == "cancel":
            task = asyncio.create_task(browser.command(["wait", "#never-exists"]))
            await asyncio.sleep(0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.1):
                    await browser.command(["wait", "#never-exists"])
        await assert_exited(owned)
    finally:
        await browser.aclose()


async def test_cancel_during_cli_process_spawn_reaps_new_namespace_and_lp(
    fixture_http, monkeypatch
):
    browser = session()
    spawned, release = asyncio.Event(), asyncio.Event()
    original = asyncio.create_subprocess_exec
    owned = set()

    async def spawning(*args, **kwargs):
        process = await original(*args, **kwargs)
        if args[-1] == "/reader/cli_runner.py":
            owned.update(descendants(process.pid))
            spawned.set()
            await release.wait()
        return process

    monkeypatch.setattr(cli_process.asyncio, "create_subprocess_exec", spawning)
    task = asyncio.create_task(browser.command(["open", URL]))
    await asyncio.wait_for(spawned.wait(), 10)
    owned.update(descendants(browser.endpoint.process.pid))
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await assert_exited(owned)
    assert browser.cli.directory is None


async def test_host_sigkill_reaps_namespace_and_lp_without_touching_another_session(fixture_http):
    other = session()
    child = None
    try:
        assert (await other.command(["open", URL]))["status"] == "ok"
        await other.command(["click", "#more"])
        code = """
import asyncio,json,socket,sys
import httpx
from app.core.settings import Settings
from app.ingestion import url_safety
from app.web_rule_agent import cli_browser
async def main():
    async def dns(host,port):
        return [(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",port))]
    url_safety._DNS_RESOLVER.getaddrinfo=dns
    def response(request):
        return httpx.Response(200,text="User-agent: *\\nAllow: /\\n"
                              if request.url.path=="/robots.txt"
                              else "<html><body><h1>Owned child</h1></body></html>",
                              headers={"content-type":"text/html"})
    cli_browser.PublicAsyncClient=lambda **kw:url_safety.PublicAsyncClient(
        transport=httpx.MockTransport(response),**kw)
    session=cli_browser.CLIBrowserSession(Settings(_env_file=None,
        ingestion_lightpanda_executable_path=sys.argv[2]),
        "https://reader-fixture.invalid/blog",cli_executable=sys.argv[1])
    result=await session.command(["open","https://reader-fixture.invalid/blog"])
    print(json.dumps({"result":result,"cli":session.cli.process.pid,
                      "lp":session.endpoint.process.pid}),flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            code,
            CLI,
            LIGHTPANDA,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
                "PYTHON_DOTENV_DISABLED": "1",
            },
        )
        line = await asyncio.wait_for(child.stdout.readline(), 15)
        assert line, await child.stderr.read()
        result = json.loads(line)
        assert result["result"]["status"] == "ok", result
        owned = descendants(result["cli"]) | descendants(result["lp"]) | {child.pid}
        child.kill()
        await child.wait()
        await assert_exited(owned)  # Assert before any outer cleanup fallback.
        remaining = await other.command(["eval", "window.readerClicks"])
        assert json.loads(remaining["output"]) == 1
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
        await other.aclose()
