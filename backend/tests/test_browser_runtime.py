"""Owned-process lifecycle and configuration tests; no browser installation required."""

from __future__ import annotations

import asyncio
import signal
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.ingestion import browser_runtime as runtime
from app.ingestion.models import SourceScanError


def test_worker_identity_detects_binary_replaced_at_same_path(tmp_path):
    binary = tmp_path / "lightpanda"
    binary.write_bytes(b"first binary")
    binary.chmod(0o700)
    first = runtime.browser_execution_identity("lightpanda", str(binary))
    binary.write_bytes(b"second binary")
    second = runtime.browser_execution_identity("lightpanda", str(binary))
    assert first["binary_status"] == "available"
    assert first["binary_sha256"] != second["binary_sha256"]
    assert first["lightpanda_supported_version"] == "0.4.0"
    assert runtime.browser_execution_identity("lightpanda", None)["binary_status"] == "unavailable"
    assert runtime.browser_execution_identity("chromium", None) == {
        "engine": "chromium",
        "policy_version": runtime.BROWSER_POLICY_VERSION,
    }


async def test_rule_trial_rejects_binary_replacement_against_task_identity(monkeypatch, tmp_path):
    import httpx

    from app.core.settings import Settings
    from app.ingestion import web_crawl
    from app.ingestion.web_rules import execute_web_rule

    binary = tmp_path / "lightpanda"
    binary.write_bytes(b"original task binary")
    binary.chmod(0o700)
    expected = runtime.browser_execution_identity("lightpanda", str(binary))
    binary.write_bytes(b"replacement binary at the same path")
    settings = Settings(_env_file=None, ingestion_lightpanda_executable_path=str(binary))
    monkeypatch.setattr(web_crawl, "get_settings", lambda: settings)

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("A changed browser must be rejected before spawning")

    async def robots(_client, target, **_kwargs):
        assert target.endswith("/robots.txt")
        return httpx.Response(
            200, text="User-agent: *\nAllow: /\n", request=httpx.Request("GET", target)
        )

    monkeypatch.setattr(runtime, "_spawn", unexpected)
    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", robots)
    rule = {
        "id": "identity-fixture",
        "hosts": ["example.com"],
        "index_paths": ["/blog"],
        "listing": {
            "render_js": True,
            "extraction": {
                "baseSelector": "article",
                "fields": [
                    {"name": "title", "type": "text", "selector": "h2"},
                    {"name": "url", "type": "attribute", "selector": "a", "attribute": "href"},
                ],
            },
        },
    }
    async with httpx.AsyncClient() as client:
        report = await execute_web_rule(
            source_url="https://example.com/blog",
            raw_rule=rule,
            client=client,
            browser_identity=expected,
        )
    assert report["status"] == "error" and not report["first_window_completed"]
    assert report["errors"][0]["code"] == "browser_configuration_changed"


def test_sandbox_never_mounts_home_repository_or_inherits_env(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: "/usr/bin/bwrap")
    args = runtime._sandbox_command("/opt/reader/lightpanda")
    assert "--unshare-user" in args and "--unshare-pid" in args
    assert "--die-with-parent" in args
    assert ["--ro-bind", "/opt/reader/lightpanda", "/reader/lightpanda"] == args[-6:-3]
    for index, arg in enumerate(args):
        if arg == "--ro-bind":
            assert not args[index + 1].startswith(("/home/", "/tmp/", "/run/"))
    assert "--block-private-networks" in runtime._ENGINE_FLAGS
    assert "0.0.0.0/0" in runtime._ENGINE_FLAGS and "::/0" in runtime._ENGINE_FLAGS
    assert "http://127.0.0.1:9" in runtime._ENGINE_FLAGS
    assert runtime._ENGINE_FLAGS[runtime._ENGINE_FLAGS.index("--ws-max-concurrent") + 1] == "0"
    message_cap = int(
        runtime._ENGINE_FLAGS[runtime._ENGINE_FLAGS.index("--cdp-max-message-size") + 1]
    )
    assert message_cap == 32 * 1024 * 1024
    assert message_cap > ((20 * 1024 * 1024 + 2) // 3) * 4 + 4096


@pytest.mark.parametrize("engine,path", [("chromium", None), ("lightpanda", None)])
async def test_unavailable_browser_never_starts_a_fallback(monkeypatch, engine, path):
    async def unexpected(*_args, **_kwargs):
        raise AssertionError("A missing or unsupported browser must not spawn")

    monkeypatch.setattr(runtime, "_spawn", unexpected)
    with pytest.raises(SourceScanError) as caught:
        async with runtime.browser_endpoint(engine, path):
            pytest.fail("No browser endpoint should be yielded")
    assert caught.value.code == "browser_unavailable"


async def test_static_http_does_not_load_browser_configuration_or_start_endpoint(monkeypatch):
    import httpx

    from app.ingestion import web_crawl
    from app.ingestion.web_crawl_types import PageRecipe

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Static HTTP must not require browser configuration or installation")

    async def allows(*_args):
        return True

    async def fetch(_client, target, **_kwargs):
        return httpx.Response(
            200,
            text="<article><h2>Static title</h2></article>",
            request=httpx.Request("GET", target),
        )

    monkeypatch.setattr(web_crawl, "get_settings", unexpected)
    monkeypatch.setattr(web_crawl, "browser_endpoint", unexpected)
    recipe = PageRecipe.model_validate(
        {
            "extraction": {
                "baseSelector": "article",
                "fields": [{"name": "title", "type": "text", "selector": "h2"}],
            }
        }
    )
    async with httpx.AsyncClient() as client:
        result = await web_crawl.crawl_page(
            client,
            "https://example.com/blog",
            recipe,
            maximum_bytes=10000,
            robots_allows=allows,
            fetch=fetch,
        )
    assert result.rows == [{"title": "Static title"}]


async def test_dynamic_crawl_rejects_explicit_chromium_before_startup():
    import httpx

    from app.ingestion import web_crawl
    from app.ingestion.web_crawl_types import PageRecipe

    async def allows(*_args):
        raise AssertionError("Unsupported engines must be rejected before network access")

    recipe = PageRecipe.model_validate({"render_js": True, "extraction": {"baseSelector": "html"}})
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceScanError) as caught:
            await web_crawl.crawl_page(
                client,
                "https://example.com/blog",
                recipe,
                maximum_bytes=10000,
                robots_allows=allows,
                browser_engine="chromium",
            )
    assert caught.value.code == "browser_unavailable"


async def test_dynamic_crawl_cannot_fall_through_without_controlled_endpoint(monkeypatch):
    import httpx

    from app.ingestion import web_crawl
    from app.ingestion.web_crawl_types import PageRecipe

    @asynccontextmanager
    async def missing(*_args, **_kwargs):
        yield None

    async def allows(*_args):
        return True

    monkeypatch.setattr(web_crawl, "browser_endpoint", missing)
    recipe = PageRecipe.model_validate({"render_js": True, "extraction": {"baseSelector": "html"}})
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceScanError) as caught:
            await web_crawl.crawl_page(
                client,
                "https://example.com/blog",
                recipe,
                maximum_bytes=10000,
                robots_allows=allows,
                browser_engine="lightpanda",
                lightpanda_executable_path="/opt/reader/lightpanda",
            )
    assert caught.value.code == "browser_unavailable"


@pytest.mark.parametrize("platform,which", [("win32", "/usr/bin/bwrap"), ("linux", None)])
def test_missing_namespace_runtime_fails_closed(monkeypatch, platform, which):
    monkeypatch.setattr(runtime.sys, "platform", platform)
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: which)
    with pytest.raises(SourceScanError) as caught:
        runtime._sandbox_command("/opt/reader/lightpanda")
    assert caught.value.code == "browser_unavailable"


class Process:
    pid = 12345
    returncode = None

    async def wait(self):
        return self.returncode


async def test_spawn_env_is_explicit_and_cancellation_waits_for_pending_spawn(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    process = Process()
    seen = {}

    async def create(*args, **kwargs):
        seen.update(kwargs)
        entered.set()
        await release.wait()
        return process

    def kill(pid, sig):
        assert pid == process.pid and sig == signal.SIGTERM
        process.returncode = -15

    monkeypatch.setattr(runtime.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(runtime.os, "killpg", kill)
    monkeypatch.setenv("APP_DEEPSEEK_API_KEY", "must-not-be-inherited")
    task = asyncio.create_task(runtime._spawn(["binary"], "/tmp"))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode == -15
    assert seen["start_new_session"] is True
    assert seen["env"] == {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/reader",
        "LIGHTPANDA_DISABLE_TELEMETRY": "true",
    }


async def test_stop_escalates_only_owned_process_on_timeout(monkeypatch):
    class StubbornProcess(Process):
        async def wait(self):
            if self.returncode is None:
                await asyncio.Event().wait()
            return self.returncode

    process = StubbornProcess()
    signals = []

    def kill(pid, sig):
        assert pid == process.pid
        signals.append(sig)
        if sig == signal.SIGKILL:
            process.returncode = -9

    monkeypatch.setattr(runtime.os, "killpg", kill)
    monkeypatch.setattr(runtime, "_STOP_TIMEOUT", 0.01)
    await runtime._stop_process(process)
    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.parametrize("output,code", [(b"0.3.0\n", 0), (b"", 1)])
async def test_unsupported_version_or_denied_namespace_never_launches_serve(
    monkeypatch, output, code
):
    async def read(_limit):
        return output

    process = Process()
    process.returncode = code
    process.stdout = SimpleNamespace(read=read)
    monkeypatch.setattr(runtime, "_spawn", lambda *_args, **_kwargs: _value(process))
    with pytest.raises(SourceScanError) as caught:
        await runtime._check_version(["sandbox", "binary"], "/tmp")
    assert caught.value.code == "browser_unavailable"


async def _value(value):
    return value


@pytest.mark.parametrize(
    "outcome", ["startup_failure", "startup_timeout", "cancel", "body_failure"]
)
async def test_endpoint_cleans_owned_process_for_all_exit_paths(monkeypatch, outcome):
    process = Process()
    started = asyncio.Event()
    identity = {"engine": "lightpanda", "binary_status": "available"}
    monkeypatch.setattr(runtime, "browser_execution_identity", lambda *_args: identity)
    monkeypatch.setattr(runtime, "_sandbox_command", lambda _path: ["sandbox", "binary"])
    monkeypatch.setattr(runtime, "_check_version", lambda *_args: _value(None))
    monkeypatch.setattr(runtime, "_spawn", lambda *_args: _value(process))

    async def ready(_process, _port):
        started.set()
        if outcome == "startup_failure":
            raise SourceScanError("browser_unavailable", "Startup failed")
        if outcome in {"startup_timeout", "cancel"}:
            await asyncio.Event().wait()

    def kill(_pid, _sig):
        process.returncode = -15

    monkeypatch.setattr(runtime, "_wait_ready", ready)
    monkeypatch.setattr(runtime.os, "killpg", kill)
    if outcome == "startup_timeout":
        monkeypatch.setattr(runtime, "_START_TIMEOUT", 0.01)

    async def run():
        async with runtime.browser_endpoint("lightpanda", "/opt/lightpanda"):
            if outcome == "body_failure":
                raise ValueError("Crawl failed")

    task = asyncio.create_task(run())
    await started.wait()
    if outcome == "cancel":
        task.cancel()
    expected = (
        asyncio.CancelledError
        if outcome == "cancel"
        else (ValueError if outcome == "body_failure" else SourceScanError)
    )
    with pytest.raises(expected):
        await task
    assert process.returncode == -15


async def test_identity_mismatch_is_rejected_before_process_creation(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "browser_execution_identity",
        lambda *_args: {
            "engine": "lightpanda",
            "binary_status": "available",
            "binary_sha256": "new",
        },
    )
    with pytest.raises(SourceScanError) as caught:
        async with runtime.browser_endpoint(
            "lightpanda", "/opt/lightpanda", expected_identity={"binary_sha256": "old"}
        ):
            pytest.fail("A changed binary must not start")
    assert caught.value.code == "browser_configuration_changed"


async def test_stalled_owned_driver_stop_is_killed_and_its_task_drained(monkeypatch):
    from app.ingestion import web_crawl

    calls = []
    stop_drained = asyncio.Event()

    class DriverProcess:
        returncode = None

        def terminate(self):
            calls.append("terminate-owned-driver")

        def kill(self):
            calls.append("kill-owned-driver")
            self.returncode = -9

        async def wait(self):
            if self.returncode is None:
                await asyncio.Event().wait()
            return self.returncode

    async def stop():
        try:
            await asyncio.Event().wait()
        finally:
            stop_drained.set()

    process = DriverProcess()
    playwright = SimpleNamespace(
        stop=stop,
        _impl_obj=SimpleNamespace(
            _connection=SimpleNamespace(_transport=SimpleNamespace(_proc=process))
        ),
    )
    manager = SimpleNamespace(playwright=playwright)
    crawler = SimpleNamespace(crawler_strategy=SimpleNamespace(browser_manager=manager))
    monkeypatch.setattr(web_crawl, "_BROWSER_CLOSE_TIMEOUT_SECONDS", 0.01)
    await web_crawl._stop_isolated_driver(crawler)
    assert calls == ["terminate-owned-driver", "kill-owned-driver"]
    assert process.returncode == -9 and stop_drained.is_set()
    assert manager.playwright is None


@pytest.mark.parametrize("phase", ["startup", "crawl"])
@pytest.mark.parametrize("outcome", ["cancel", "timeout"])
async def test_host_stops_engine_before_draining_crawl4ai_cancel_finally(
    monkeypatch, phase, outcome
):
    import httpx
    from test_web_crawl_lifecycle import RECIPE, URL, BrowserHarness

    from app.ingestion import web_crawl

    harness = BrowserHarness()
    harness.install(monkeypatch)
    entered, stopped, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    base = harness.crawler_type()

    class StalledCrawler(base):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.crawler_strategy.browser_manager = SimpleNamespace(playwright=None)

        async def stall(self):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                # The vendor's finally cannot return until CDP has closed.
                await stopped.wait()
                drained.set()

        async def __aenter__(self):
            if phase == "startup":
                await self.stall()
            return self

        async def arun(self, **kwargs):
            if phase == "crawl":
                await self.stall()

    import crawl4ai

    monkeypatch.setattr(crawl4ai, "AsyncWebCrawler", StalledCrawler)

    async def stop():
        assert not drained.is_set()
        assert web_crawl._slot().locked()
        stopped.set()

    @asynccontextmanager
    async def endpoint(*_args, **_kwargs):
        yield SimpleNamespace(url="http://127.0.0.1:1", stop=stop)

    monkeypatch.setattr(web_crawl, "browser_endpoint", endpoint)
    if outcome == "timeout":
        monkeypatch.setattr(web_crawl, "_CRAWL_TIMEOUT_SECONDS", 0.05)
    async with httpx.AsyncClient() as client:
        task = asyncio.create_task(
            web_crawl.crawl_page(
                client,
                URL,
                RECIPE,
                maximum_bytes=100000,
                robots_allows=harness.robots_allows,
                browser_engine="lightpanda",
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        if outcome == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if outcome == "cancel" else SourceScanError):
            await asyncio.wait_for(task, 1)
        assert stopped.is_set() and drained.is_set()
        assert harness.closed == 1 and not client.is_closed
        assert not web_crawl._slot().locked()
