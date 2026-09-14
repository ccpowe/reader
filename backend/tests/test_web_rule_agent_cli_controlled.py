"""Opt-in production tool with real CLI/Lightpanda and a real dynamic scanner trial."""

import json
from copy import deepcopy

import pytest
from test_cli_browser_controlled import CLI, LIGHTPANDA, fixture_http  # noqa: F401
from test_web_rule_agent_runtime import RULE, make_context

from app.core.settings import Settings
from app.ingestion import web_crawl, web_rules
from app.web_rule_agent import cli_browser
from app.web_rule_agent.cli_browser import CLIBrowserSession

pytestmark = pytest.mark.skipif(not CLI or not LIGHTPANDA, reason="Explicit CLI/Lightpanda opt-in")


async def test_production_cli_session_survives_actual_trial_native_error_and_evidence_read(
    fixture_http,  # noqa: F811
    monkeypatch,
):
    context = make_context(
        settings=Settings(
            _env_file=None,
            ingestion_lightpanda_executable_path=LIGHTPANDA,
            web_rule_agent_cli_executable_path=CLI,
        )
    )
    source = context.claim.source_url

    # Keep the real scanner and controlled browser runtime. Only HTTP/DNS and
    # explicit local executable settings come from the shared fixture.
    monkeypatch.setattr(web_rules, "PublicAsyncClient", cli_browser.PublicAsyncClient)
    monkeypatch.setattr(web_crawl, "get_settings", lambda: context.settings)
    rule = deepcopy(RULE)
    rule["listing"]["render_js"] = True
    rule["listing"]["extraction"]["baseSelector"] = "article"
    try:
        opened = await context.inspect(argv=["open", source])
        assert opened["status"] == "ok", opened
        browser = context.browser
        assert isinstance(browser, CLIBrowserSession)
        snapshot = await context.inspect(argv=["snapshot", "-u", "-s", "main"])
        assert "First article" in snapshot["output"]
        assert source.replace("/blog", "/next") in context.observed_pages
        clicked = await context.inspect(argv=["click", "#more"])
        assert clicked["status"] == "ok", clicked
        receipt = await context.execute(rule)
        assert receipt["first_window_completed"] is True, receipt
        assert receipt["items"][0]["title"] == "First article", receipt
        await context.read_evidence(receipt["execution_id"])
        await context.read_evidence(snapshot["evidence_id"])
        failed = await context.inspect(argv=["get", "attr"])
        assert failed["status"] == "error" and failed["error"]["source"] == "cli", failed
        current = await context.inspect(argv=["eval", "window.readerClicks"])
        assert json.loads(current["output"]) == 1, current
        assert context.browser is browser and fixture_http.count(source) == 2
        for value in (opened, snapshot, clicked, failed, current):
            assert len(json.dumps(value, ensure_ascii=False).encode()) <= 20 * 1024
    finally:
        await context.close()
    assert browser.cli.process.returncode is not None
    assert browser.endpoint.process.returncode is not None
