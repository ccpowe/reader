"""CLI contracts and transport limits without installed browsers or external HTTP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.ingestion.models import SourceScanError
from app.web_rule_agent.cli_bridge import CDPBridge, PageTransfer
from app.web_rule_agent.cli_browser import (
    CLIBrowserSession,
    check_args,
    finalize_cli_report,
    native_report,
    visible_links,
)
from app.web_rule_agent.cli_process import OwnedCLIProcess, cli_execution_identity, sandbox_command
from app.web_rule_agent.cli_runner import CAPTURE_BYTES, execute


@pytest.mark.parametrize(
    "argv",
    [
        [],
        "snapshot",
        [1],
        ["close"],
        ["open", "--cdp", "9"],
        ["eval", "\0"],
        ["get", "title", "--namespace", "other"],
    ],
)
def test_wrapper_rejects_identity_switches_and_invalid_argv(argv):
    with pytest.raises(ValueError):
        check_args(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["snapshot", "-u", "-s", "main", "-d", "4"],
        ["get", "attr"],
        ["eval", "(() => { throw 1 })()"],
        ["open"],
        ["wait", "--fn", "window.ready"],
    ],
)
def test_cli_gets_to_report_its_own_missing_arguments_and_js_errors(argv):
    check_args(argv)


def raw(data=None, *, error=None, code=0, success=True, warning=None):
    return {
        "exit_code": code,
        "stderr": "",
        "stdout": json.dumps(
            {
                "success": success,
                "data": data,
                "error": error,
                "warning": warning,
            }
        ),
    }


def test_native_payload_mapping_and_errors_are_not_semantically_reclassified():
    snapshot = "- link Navigation [ref=e1, url=/menu]"
    result = native_report(
        ["snapshot"],
        raw({"snapshot": snapshot, "refs": {"e1": {}}, "lifecycle": {"engine": "chrome"}}),
        url="https://a/",
        revision="r",
    )
    assert result["output"] == snapshot and result["format"] == "snapshot"
    assert "chrome" not in json.dumps(result)
    actual = [{"title": "Navigation", "url": "/menu"}]
    result = native_report(["eval", "x"], raw({"result": actual}), url="https://a/", revision="r")
    assert json.loads(result["output"]) == actual and result["status"] == "ok"
    result = native_report(
        ["eval", "x"],
        raw(error="SyntaxError: actual error", success=False),
        url="https://a/",
        revision=None,
    )
    assert result["error"] == {
        "source": "cli",
        "exit_code": 0,
        "message": "SyntaxError: actual error",
    }
    assert result["status"] == "error" and result["page_revision"] is None


def test_non_json_preserves_received_streams_exit_and_capture_truncation():
    result = native_report(
        ["get", "title"],
        {
            "stdout": "actual non-JSON response",
            "stderr": "native stderr",
            "exit_code": 7,
            "capture_truncated": ["stdout"],
        },
        url=None,
        revision=None,
    )
    assert result["output"] == "actual non-JSON response"
    assert result["stderr"] == "native stderr" and result["error"]["exit_code"] == 7
    assert result["truncated"] and result["truncated_fields"] == ["stdout_capture"]


@pytest.mark.parametrize("text", ["中文🙂" * 12000, '\\"\n\t' * 12000, '[{"url":"/x"},' * 12000])
def test_single_finalizer_includes_all_metadata_and_json_escaping(text):
    report = native_report(
        ["get", "html"], raw({"html": text}), url="https://example.com/", revision="r"
    )
    report["stderr"] = "错" * 2000
    report["network_errors"] = [{"url": "https://example.com/", "message": "failure"}] * 5
    result = finalize_cli_report(report, metadata={"evidence_id": "e1", "repeated": True})
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 20 * 1024
    assert result["evidence_id"] == "e1" and result["repeated"]
    assert len(result["stderr"]) <= 1000 and len(result["network_errors"]) <= 3
    assert result["truncated"] and text.startswith(result["output"])
    assert result["output"]  # Never replace the whole body with a generic truncated dict.


def test_native_truncation_warning_is_preserved_separately():
    result = native_report(
        ["get", "text"],
        raw({"text": "native prefix [truncated]"}, warning="Output truncated by CLI"),
        url=None,
        revision=None,
    )
    result = finalize_cli_report(result)
    assert not result["truncated"]  # Reader did not clip anything.
    assert result["cli_warning"] == "Output truncated by CLI"
    assert result["output"].endswith("[truncated]")


def test_only_final_visible_complete_urls_are_navigation_authority():
    result = {
        "url": "https://example.com/blog",
        "format": "snapshot",
        "output": "- link One [ref=e1, url=/one]\n- link Other [ref=e2, url=https://other.com/]\n"
        + "x" * 30000
        + "\n- link Hidden [ref=e3, url=/hidden]",
        "error": None,
    }
    final = finalize_cli_report(result)
    assert visible_links(final) == ["https://example.com/one"]
    assert visible_links(
        {
            "url": result["url"],
            "format": "html",
            "output": '<a href="/one">One</a><a href="/cut">unfinished',
        }
    ) == ["https://example.com/one"]
    assert visible_links({"url": result["url"], "format": "json", "output": '["/hidden"]'}) == []


async def test_invalid_open_never_starts_browser_even_with_extra_cli_args():
    session = CLIBrowserSession(None, "https://example.com/blog", cli_executable="/unused")
    for argv in (["open", "https://example.com/unseen", "extra"], ["open", "https://other.com/"]):
        result = await session.command(argv)
        assert result["error"]["source"] == "wrapper" and session.stack is None


def event(*, resource="Document", frame="main", url="https://example.com/blog", method="GET"):
    return {
        "sessionId": "session",
        "params": {
            "requestId": "request",
            "frameId": frame,
            "resourceType": resource,
            "request": {"url": url, "method": method},
        },
    }


def bridge():
    value = CDPBridge(
        SimpleNamespace(url="ws://127.0.0.1:9"),
        None,
        "https://example.com/blog",
        maximum_bytes=5 * 1024 * 1024,
    )
    value.session_id, value.main_frame = "session", "main"
    return value


def test_page_limits_follow_top_document_not_cli_command_or_subframe():
    value = bridge()
    first = value._request_transfer(event())
    first.requests, first.bytes = 80, 200
    for request in [
        event(resource="XHR"),
        event(frame="subframe"),
        event(url="https://other.com/"),
        event(method="POST"),
    ]:
        assert value._request_transfer(request) is first
    next_page = value._request_transfer(event(url="https://example.com/page2"))
    assert next_page is not first and next_page.requests == 0
    first.bytes += 100  # Late responses retain the first page's accounting.
    assert next_page.bytes == 0


async def test_same_page_load_more_cannot_bypass_request_limit():
    value = bridge()
    value.transfer = PageTransfer(requests=80)
    calls = []

    async def request(method, *args, **kwargs):
        calls.append(method)

    value.request = request
    message = event(resource="XHR")
    await value.fulfill(message, value._request_transfer(message))
    assert calls == ["Fetch.failRequest"]
    assert value.network_errors[0]["code"] == "web_request_limit"


def test_cli_identity_detects_replacement_without_settings_or_execution(tmp_path):
    binary = tmp_path / "agent-browser"
    binary.write_bytes(b"first")
    binary.chmod(0o700)
    first = cli_execution_identity(str(binary))
    binary.write_bytes(b"second")
    second = cli_execution_identity(str(binary))
    assert first["binary_sha256"] != second["binary_sha256"]
    assert first["cli_supported_version"] == "0.37.1"
    assert cli_execution_identity(None)["binary_status"] == "unavailable"


def test_runner_drains_both_streams_but_retains_only_bounded_prefixes():
    result = execute(
        [sys.executable, "-c", "import os; os.write(1,b'o'*900000);os.write(2,b'e'*900000)"],
        timeout=3,
    )
    assert result["exit_code"] == 0
    assert result["stdout"] == "o" * CAPTURE_BYTES
    assert result["stderr"] == "e" * CAPTURE_BYTES
    assert result["capture_truncated"] == ["stdout", "stderr"]


def test_runner_timeout_returns_collected_output_and_kills_only_its_command():
    result = execute(
        [sys.executable, "-c", "import time;print('before wait',flush=True);time.sleep(5)"],
        timeout=0.1,
    )
    assert result["timed_out"] and result["exit_code"] < 0
    assert result["stdout"] == "before wait\n"


async def test_unavailable_and_changed_cli_identity_fail_before_process_start(tmp_path):
    with pytest.raises(SourceScanError, match="Configure agent-browser"):
        await OwnedCLIProcess("/missing-reader-fixture-cli").start()
    binary = tmp_path / "agent-browser"
    binary.write_bytes(b"initial")
    binary.chmod(0o700)
    expected = cli_execution_identity(str(binary))
    binary.write_bytes(b"replacement")
    owner = OwnedCLIProcess(str(binary), expected_identity=expected)
    with pytest.raises(SourceScanError, match="identity changed"):
        await owner.start()
    assert owner.process is None and owner.directory is None


def test_cli_namespace_mounts_only_explicit_runner_binary_and_task_directory(monkeypatch):
    monkeypatch.setattr(
        "app.web_rule_agent.cli_process.shutil.which", lambda _name: "/usr/bin/bwrap"
    )
    args = sandbox_command("/opt/reader/agent-browser", "/tmp/reader-cli-owned-fixture")
    assert "--die-with-parent" in args and "--unshare-pid" in args
    writable = [args[index + 1] for index, arg in enumerate(args) if arg == "--bind"]
    assert writable == ["/tmp/reader-cli-owned-fixture"]
    mounted = [args[index + 1] for index, arg in enumerate(args) if arg == "--ro-bind"]
    local_source = str(Path(__file__).parents[1] / "src/app/web_rule_agent/cli_runner.py")
    repository = Path(__file__).parents[2]
    assert [path for path in mounted if Path(path).is_relative_to(repository)] == [local_source]
    assert args[-3:] == ["/usr/bin/python3", "-u", "/reader/cli_runner.py"]


async def test_failed_dom_observation_does_not_hash_errors_or_assert_old_url(monkeypatch):
    browser = CLIBrowserSession(None, "https://example.com/", cli_executable="/unused")
    browser.url = "https://example.com/old"
    browser.base = []

    async def start():
        return None

    async def command(_argv):
        return raw(error="Native JS exception", success=False, code=1)

    async def dom():
        raise RuntimeError("DOM unavailable")

    monkeypatch.setattr(browser, "start", start)
    monkeypatch.setattr(browser.cli, "command", command)
    browser.bridge = SimpleNamespace(
        reset_errors=lambda: None, dom=dom, network_errors=[], network_error_count=0
    )
    result = await browser.command(["eval", "throw 1"])
    assert result["page_revision"] is None and result["url"] is None
    assert result["error"]["message"] == "Native JS exception"


@pytest.mark.parametrize(
    "data,format,output",
    [
        ({}, "json", "{}"),
        ({"result": None}, "json", "null"),
        ({"result": ""}, "text", ""),
        ({"result": "实际字符串"}, "text", "实际字符串"),
        ({"result": False}, "json", "false"),
        ({"result": 0}, "json", "0"),
        ({"result": []}, "json", "[]"),
        ({"result": {"key": None}}, "json", '{"key": null}'),
    ],
)
def test_eval_result_preserves_missing_null_strings_and_json_values(data, format, output):
    result = native_report(["eval", "expression"], raw(data), url=None, revision=None)
    assert (result["format"], result["output"]) == (format, output)


@pytest.mark.parametrize("format", ["snapshot", "html"])
def test_visible_urls_isolate_malformed_ipv6_without_losing_valid_links(format):
    hrefs = ["/first", "https://[2606:4700:4700::1111]/", "http://[invalid/", "/last"]

    def report(url):
        output = (
            "\n".join(f"- link Link [ref=e{i}, url={href}]" for i, href in enumerate(hrefs))
            if format == "snapshot"
            else "".join(f'<a href="{href}">Link</a>' for href in hrefs)
        )
        return {"url": url, "format": format, "output": output}

    assert visible_links(report("https://example.com/blog")) == [
        "https://example.com/first",
        "https://example.com/last",
    ]
    assert visible_links(report("https://[2606:4700:4700::1111]/blog")) == [
        "https://[2606:4700:4700::1111]/first",
        "https://[2606:4700:4700::1111]/",
        "https://[2606:4700:4700::1111]/last",
    ]


@pytest.mark.parametrize("finish_start", ["success", "failure", "cancel"])
async def test_close_during_endpoint_acquisition_waits_and_reaps_late_resources(
    monkeypatch, finish_start
):
    from app.web_rule_agent import cli_browser

    reached, release = asyncio.Event(), asyncio.Event()
    closed = {"client": False, "endpoint": False, "bridge": False, "cli": False}

    @asynccontextmanager
    async def client(**kwargs):
        try:
            yield object()
        finally:
            closed["client"] = True

    class Endpoint:
        async def stop(self):
            closed["endpoint"] = True

    @asynccontextmanager
    async def endpoint(*args, **kwargs):
        reached.set()
        await release.wait()
        value = Endpoint()
        try:
            yield value
        finally:
            await value.stop()

    class Bridge:
        port = 9

        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            return None

        async def aclose(self):
            closed["bridge"] = True

    settings = SimpleNamespace(
        ingestion_browser_engine="lightpanda",
        ingestion_lightpanda_executable_path="/unused",
        ingestion_max_response_bytes=1024,
    )
    browser = CLIBrowserSession(settings, "https://example.com/", cli_executable="/unused")

    async def cli_start():
        if finish_start == "failure":
            raise SourceScanError("browser_unavailable", "Controlled startup failure")

    async def cli_close():
        closed["cli"] = True

    monkeypatch.setattr(cli_browser, "PublicAsyncClient", client)
    monkeypatch.setattr(cli_browser, "browser_endpoint", endpoint)
    monkeypatch.setattr(cli_browser, "CDPBridge", Bridge)
    monkeypatch.setattr(browser.cli, "start", cli_start)
    monkeypatch.setattr(browser.cli, "aclose", cli_close)
    opening = asyncio.create_task(browser.start())
    await asyncio.wait_for(reached.wait(), 1)
    closing = asyncio.create_task(browser.aclose())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not closing.done()  # Close cannot declare completion before acquisition settles.
    if finish_start == "cancel":
        opening.cancel()
    release.set()
    result = await asyncio.wait_for(asyncio.gather(opening, closing, return_exceptions=True), 1)
    if finish_start == "failure":
        assert isinstance(result[0], SourceScanError)
    elif finish_start == "cancel":
        assert isinstance(result[0], asyncio.CancelledError)
    else:
        assert result[0] is None
    assert closed["client"] and closed["cli"]
    if finish_start != "cancel":
        assert closed["endpoint"] and closed["bridge"]
    assert len(browser.stack._exit_callbacks) == 0
    await browser.aclose()


def transfer_bridge(monkeypatch, handler):
    value = bridge()
    value.maximum_bytes = 100
    robot_calls = []

    async def robots(url, maximum_bytes):
        robot_calls.append(url)
        return True

    async def cdp(*args, **kwargs):
        return {}

    monkeypatch.setattr(value.adapter, "_robots_allows", robots)
    monkeypatch.setattr(value, "request", cdp)
    monkeypatch.setattr("app.web_rule_agent.cli_bridge.safe_get", handler)
    return value, robot_calls


def response_bytes(url, count):
    return httpx.Response(200, content=b"x" * count, request=httpx.Request("GET", url))


async def test_exhausted_transfer_budget_prevents_all_outbound_requests(monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("An exhausted page must not start another HTTP request")

    value, robots = transfer_bridge(monkeypatch, unexpected)
    value.transfer.bytes = 401
    for _ in range(3):
        await value.fulfill(event(resource="XHR"), value.transfer)
    assert robots == [] and value.transfer.bytes == 401
    assert value.transfer.reserved == 0 and value.network_error_count == 3


async def test_remaining_transfer_budget_is_the_actual_response_read_limit(monkeypatch):
    limits = []

    async def fetch(client, url, **kwargs):
        limit = kwargs["max_response_bytes"]
        limits.append(limit)
        return response_bytes(url, 15 if len(limits) == 1 else 5)

    value, _ = transfer_bridge(monkeypatch, fetch)
    value.transfer.bytes = 380
    for _ in range(3):
        await value.fulfill(event(resource="XHR"), value.transfer)
    assert limits == [20, 5]
    assert value.transfer.bytes == 400 and value.transfer.reserved == 0


async def test_concurrent_small_response_returns_budget_before_restricting_next_limit(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    limits = []

    async def fetch(client, url, **kwargs):
        limits.append(kwargs["max_response_bytes"])
        if len(limits) == 1:
            entered.set()
            await release.wait()
            return response_bytes(url, 10)
        return response_bytes(url, 75)

    value, _ = transfer_bridge(monkeypatch, fetch)
    value.transfer.bytes = 250
    first = asyncio.create_task(value.fulfill(event(resource="XHR"), value.transfer))
    await entered.wait()
    second = asyncio.create_task(value.fulfill(event(resource="XHR"), value.transfer))
    await asyncio.sleep(0)
    assert limits == [100] and value.transfer.reserved == 100
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert limits == [100, 100]  # Temporary 50 bytes did not become a false read cap.
    assert value.transfer.bytes == 335 and value.transfer.reserved == 0


async def test_transfer_budget_retains_two_concurrent_requests_without_overallocation(monkeypatch):
    both, release = asyncio.Event(), asyncio.Event()
    limits = []

    async def fetch(client, url, **kwargs):
        limits.append(kwargs["max_response_bytes"])
        if len(limits) == 2:
            both.set()
        await release.wait()
        return response_bytes(url, 10)

    value, _ = transfer_bridge(monkeypatch, fetch)
    tasks = [
        asyncio.create_task(value.fulfill(event(resource="XHR"), value.transfer)) for _ in range(2)
    ]
    try:
        await asyncio.wait_for(both.wait(), 1)
        assert limits == [100, 100] and value.transfer.reserved == 200
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert value.transfer.bytes == 20 and value.transfer.reserved == 0


async def test_cancelled_transfer_conservatively_settles_and_releases_waiting_request(monkeypatch):
    entered = asyncio.Event()
    limits = []

    async def fetch(client, url, **kwargs):
        limits.append(kwargs["max_response_bytes"])
        if len(limits) == 1:
            entered.set()
            await asyncio.Event().wait()
        return response_bytes(url, 10)

    value, _ = transfer_bridge(monkeypatch, fetch)
    value.transfer.bytes = 250
    first = asyncio.create_task(value.fulfill(event(resource="XHR"), value.transfer))
    await entered.wait()
    second = asyncio.create_task(value.fulfill(event(resource="XHR"), value.transfer))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, 1)
    assert limits == [100, 50]
    assert value.transfer.bytes == 360 and value.transfer.reserved == 0


async def test_failed_transfer_settles_allowance_without_stranding_reservation(monkeypatch):
    limits = []

    async def fetch(client, url, **kwargs):
        limits.append(kwargs["max_response_bytes"])
        if len(limits) == 1:
            raise SourceScanError("response_too_large", "Controlled response read limit")
        return response_bytes(url, 10)

    value, _ = transfer_bridge(monkeypatch, fetch)
    await value.fulfill(event(resource="XHR"), value.transfer)
    await value.fulfill(event(resource="XHR"), value.transfer)
    assert limits == [100, 100]
    assert value.transfer.bytes == 110 and value.transfer.reserved == 0


async def test_navigation_budget_is_independent_of_old_inflight_reservation(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    limits = []

    async def fetch(client, url, **kwargs):
        limits.append(kwargs["max_response_bytes"])
        if url.endswith("/old"):
            entered.set()
            await release.wait()
            return response_bytes(url, 20)
        return response_bytes(url, 10)

    value, _ = transfer_bridge(monkeypatch, fetch)
    old = value.transfer
    old.bytes = 350
    first = asyncio.create_task(
        value.fulfill(event(resource="XHR", url="https://example.com/old"), old)
    )
    await entered.wait()
    message = event(url="https://example.com/new")
    current = value._request_transfer(message)
    await value.fulfill(message, current)
    assert current.bytes == 10 and current.reserved == 0
    assert old.reserved == 50 and limits == [50, 100]
    release.set()
    await first
    assert current.bytes == 10 and old.bytes == 370 and old.reserved == 0


async def test_exhausting_redirect_response_never_fetches_final_origin_robots(monkeypatch):
    limits = []

    async def fetch(client, url, **kwargs):
        limits.append(kwargs["max_response_bytes"])
        return response_bytes("https://new-origin.example/final", 20)

    value, robots = transfer_bridge(monkeypatch, fetch)
    value.transfer.bytes = 380
    await value.fulfill(event(resource="XHR"), value.transfer)
    assert limits == [20] and value.transfer.bytes == 400
    assert value.transfer.reserved == 0
    assert robots == ["https://example.com/blog"]
    assert value.network_errors[0]["code"] == "browser_redirect_unsupported"


async def test_cli_runner_content_is_part_of_claim_and_start_identity(monkeypatch, tmp_path):
    from app.web_rule_agent import cli_process

    monkeypatch.setattr(cli_process, "__file__", str(tmp_path / "cli_process.py"))
    binary = tmp_path / "agent-browser"
    binary.write_bytes(b"native-binary-fixture")
    binary.chmod(0o700)
    runner = tmp_path / "cli_runner.py"
    runner.write_bytes(b"original runner")
    claimed = cli_execution_identity(str(binary))
    assert claimed["runner_status"] == "available"
    assert claimed["runner_sha256"] == hashlib.sha256(runner.read_bytes()).hexdigest()
    runner.write_bytes(b"modified runner")
    current = cli_execution_identity(str(binary))
    assert current["binary_sha256"] == claimed["binary_sha256"]
    assert current["runner_sha256"] != claimed["runner_sha256"]
    owner = OwnedCLIProcess(str(binary), expected_identity=claimed)
    with pytest.raises(SourceScanError, match="identity changed"):
        await owner.start()
    assert owner.process is None and owner.directory is None


@pytest.mark.parametrize("unavailable", ["missing", "unreadable"])
async def test_unavailable_cli_runner_has_no_fake_digest_and_fails_before_spawn(
    monkeypatch, tmp_path, unavailable
):
    from app.web_rule_agent import cli_process

    monkeypatch.setattr(cli_process, "__file__", str(tmp_path / "cli_process.py"))
    binary = tmp_path / "agent-browser"
    binary.write_bytes(b"native-binary-fixture")
    binary.chmod(0o700)
    runner = tmp_path / "cli_runner.py"
    if unavailable == "unreadable":
        runner.write_text("unreadable fixture")
        original = cli_process._binary_digest

        def digest(path, identity):
            if path == str(runner):
                raise PermissionError("Controlled unreadable runner")
            return original(path, identity)

        monkeypatch.setattr(cli_process, "_binary_digest", digest)
    identity = cli_execution_identity(str(binary))
    assert identity["binary_status"] == "available"
    assert identity["runner_status"] == "unavailable" and "runner_sha256" not in identity
    owner = OwnedCLIProcess(str(binary), expected_identity=identity)
    with pytest.raises(SourceScanError, match="runner file is unavailable"):
        await owner.start()
    assert owner.process is None and owner.directory is None
