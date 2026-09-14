"""Production CLI tool, evidence authority, and task lifecycle integration."""

import asyncio
import json

import pytest
from langchain_core.messages import ToolMessage
from test_web_rule_agent_runtime import (
    RULE,
    ScriptModel,
    call,
    execution_report,
    make_context,
)

from app.core.settings import Settings
from app.ingestion import web_rules
from app.services.web_rule_jobs import JobGuardError
from app.web_rule_agent.errors import AgentFailure
from app.web_rule_agent.memory import AuthoringMemory, json_bytes
from app.web_rule_agent.runtime import run_rule_agent
from app.web_rule_agent.tools import inspect_web_page
from app.workers import web_rules as worker

SOURCE = "https://example.com/blog"


def report(output="actual", *, url=SOURCE, revision="r1", format="text", error=None):
    return {
        "status": "error" if error else "ok",
        "url": url,
        "page_revision": revision,
        "format": format,
        "output": output,
        "error": error,
    }


class Session:
    def __init__(self, reports):
        self.reports = iter(reports)
        self.calls = []
        self.closed = False

    async def command(self, argv, *, observed_urls=()):
        assert not self.closed
        self.calls.append((argv, tuple(observed_urls)))
        return next(self.reports)

    async def aclose(self):
        self.closed = True


def test_production_inspect_tool_exposes_only_one_argv_array():
    schema = inspect_web_page.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"argv"}
    assert schema["required"] == ["argv"]
    assert schema["properties"]["argv"]["items"] == {"type": "string"}


@pytest.mark.parametrize("text", ['界"\\' * 20000, "article " * 30000])
async def test_graph_delivery_repeat_and_read_keep_actual_bounded_cli_content(text):
    context = make_context(
        settings=Settings(_env_file=None, web_rule_agent_max_tool_result_bytes=1024)
    )
    session = Session([report(text), report(text)])
    context.browser = session
    observed = {}

    def repeat(messages):
        value = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        assert value["truncated"] and value["output"] and json_bytes(value) <= 1024
        observed.update(value)
        return call("inspect_web_page", {"argv": ["get", "text", "main"]})

    def read(messages):
        value = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        assert value["repeated"] and value["evidence_id"] == observed["evidence_id"]
        assert value["output"]  # Not replaced with a same_evidence stub.
        return call("read_rule_evidence", {"evidence_id": value["evidence_id"]})

    def fail(messages):
        value = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        assert value["output"] and value["observation_state"] == "current"
        assert value["restores_live_refs"] is False and json_bytes(value) <= 1024
        return call(
            "RuleAuthoringFailure",
            {
                "reason": "missing_evidence",
                "message": "This controlled fixture ends after reading evidence.",
                "evidence_ids": [value["evidence_id"]],
            },
        )

    model = ScriptModel(
        responses=[call("inspect_web_page", {"argv": ["get", "text", "main"]}), repeat, read, fail]
    )
    with pytest.raises(AgentFailure, match="controlled fixture"):
        await run_rule_agent(context, model, preflight={})
    assert len(session.calls) == 2 and not session.closed
    await context.close()
    assert session.closed


async def test_only_final_complete_visible_links_grant_navigation_and_read_grants_nothing():
    context = make_context(
        settings=Settings(_env_file=None, web_rule_agent_max_tool_result_bytes=1024)
    )
    content = '<a href="/visible">Visible</a>' + "x" * 3000 + '<a href="/hidden">Hidden</a>'
    context.browser = Session(
        [
            report(content, format="html"),
            report('["https://example.com/eval-hidden"]', format="json"),
        ]
    )
    first = await context.inspect(argv=["get", "html", "main"])
    assert list(context.observed_pages) == ["https://example.com/visible"]
    assert "hidden" not in first["output"]
    context.observed_pages.clear()
    await context.read_evidence(first["evidence_id"])
    assert not context.observed_pages
    await context.inspect(argv=["eval", "Array.from(document.links, a => a.href)"])
    assert not context.observed_pages


async def test_null_and_errors_keep_real_revision_and_historical_navigation_identity():
    wrapper_error = {"source": "wrapper", "message": "invalid argv", "exit_code": None}
    cli_error = {"source": "cli", "message": "mutated then threw", "exit_code": 1}
    context = make_context()
    context.browser = Session(
        [
            report("null", format="json"),
            report("", revision=None, error=wrapper_error),
            report("", revision="r2", error=cli_error),
            report("other", url=SOURCE + "/next", revision="r2"),
            report("unknown", url=None, revision=None, error=cli_error),
        ]
    )
    first = await context.inspect(argv=["eval", "null"])
    assert first["output"] == "null"
    invalid = await context.inspect(argv=["unknown"])
    assert invalid["error"] == wrapper_error
    assert context.memory.read(first["evidence_id"])["observation_state"] == "current"
    mutated = await context.inspect(argv=["eval", "mutateAndThrow()"])
    assert mutated["error"] == cli_error
    assert context.memory.read(first["evidence_id"])["observation_state"] == "historical"
    await context.inspect(argv=["click", "a"])
    assert context.memory.read(mutated["evidence_id"])["observation_state"] == "historical"
    await context.inspect(argv=["eval", "brokenTransport()"])
    assert context.memory.read(mutated["evidence_id"])["observation_state"] == "unknown"
    assert not context.browser.closed


async def test_current_execution_reference_survives_observation_summary_eviction():
    context = make_context()
    context.validation_feedback(execution_report())
    cursor = dict(context.memory.read_progress)
    for index in range(18):
        context.memory.remember(
            report(str(index)), view={"url": SOURCE, "argv": ["get", "text", str(index)]}
        )
    assert "valid-receipt" not in context.memory.evidence
    assert context.memory.knows("valid-receipt")
    assert context.memory.snapshot()["current_execution_id"] == "valid-receipt"
    assert (
        context.memory.record("unresolved", "History unknown", ["valid-receipt"])["status"]
        == "pass"
    )
    assert context.memory.read_progress == cursor
    model = ScriptModel(
        responses=[
            call(
                "RuleAuthoringFailure",
                {
                    "reason": "missing_evidence",
                    "message": "Observed execution did not establish the desired listing.",
                    "evidence_ids": ["valid-receipt"],
                },
            )
        ]
    )
    with pytest.raises(AgentFailure, match="desired listing"):
        await run_rule_agent(context, model, preflight={})
    assert context.memory.read_progress == cursor


@pytest.mark.parametrize("status", ["error", "partial"])
async def test_trial_and_model_wait_preserve_session_and_evidence_until_task_end(
    monkeypatch, status
):
    context = make_context()
    session = Session([report("Opened"), report("clicked state"), report("clicked state")])
    context.browser = session
    saved = {}

    async def execute(**kwargs):
        assert context.browser is session and not session.closed
        return {
            **execution_report(),
            "status": status,
            "first_window_completed": status == "partial",
            "items": [{"title": "First article", "url": SOURCE + "/article"}]
            if status == "partial"
            else [],
            "errors": [
                {
                    "code": "web_http_503",
                    "message": "Fixture window failed",
                    "window": 1 if status == "partial" else 0,
                }
            ],
        }

    monkeypatch.setattr(web_rules, "execute_web_rule", execute)

    def trial(messages):
        value = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        saved["ref"] = value["evidence_id"]
        assert not session.closed
        return call("execute_web_rule", {"raw_rule": RULE})

    def read(messages):
        value = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        assert value["status"] == status
        assert value["first_window_completed"] is (status == "partial")
        assert value["errors"][0]["code"] == "web_http_503"
        assert bool(value["items"]) is (status == "partial")
        assert not session.closed and context.memory.knows(saved["ref"])
        return call("read_rule_evidence", {"evidence_id": saved["ref"]})

    def again(messages):
        assert not session.closed
        return call("inspect_web_page", {"argv": ["get", "text", "button"]})

    def fail(messages):
        value = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        assert value["output"] == "clicked state"
        return call(
            "RuleAuthoringFailure",
            {
                "reason": "missing_evidence",
                "message": "Fixture retains the page while ending without activation.",
                "evidence_ids": [saved["ref"]],
            },
        )

    model = ScriptModel(
        responses=[call("inspect_web_page", {"argv": ["click", "#more"]}), trial, read, again, fail]
    )
    monkeypatch.setattr(worker, "close_chat_model", _noop)
    with pytest.raises(AgentFailure, match="Fixture retains"):
        await worker._execute_claim(context, None, lambda *a, **kw: model)
    assert session.closed and len(session.calls) == 3


async def _noop(*args, **kwargs):
    pass


@pytest.mark.parametrize("stop", ["cancel", "timeout", "lease"])
async def test_worker_finally_closes_session_on_each_task_stop(monkeypatch, stop):
    context = make_context()
    session = Session([])
    context.browser = session
    started = asyncio.Event()

    async def body(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_execute_claim_inner", body)
    if stop == "lease":

        async def renew(*args):
            await started.wait()
            return False

        context.store.heartbeat = renew
        context.settings.web_rule_agent_heartbeat_seconds = 0.001
        with pytest.raises(JobGuardError, match="lease_lost"):
            await worker._run_with_heartbeat(context, worker._execute_claim(context, None, None))
    elif stop == "timeout":
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.02):
                await worker._execute_claim(context, None, None)
    else:
        task = asyncio.create_task(worker._execute_claim(context, None, None))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert session.closed


def test_checkpoint_does_not_restore_old_live_page_or_permission():
    memory = AuthoringMemory()
    ref, _ = memory.remember(report(), view={"argv": ["snapshot"], "url": SOURCE})
    memory.record("listing", "Observed card", [ref])
    context = make_context()
    context.memory.restore(memory.checkpoint())
    assert not context.observed_pages and not context.memory.details
    assert context.memory.findings["listing"]["needs_fresh_evidence"]


@pytest.mark.parametrize(
    "native_error", [None, {"source": "cli", "message": "actual native error", "exit_code": 1}]
)
async def test_graph_long_url_omission_keeps_actual_content_and_observation_identity(native_error):
    context = make_context(
        settings=Settings(_env_file=None, web_rule_agent_max_tool_result_bytes=1024)
    )
    source = "https://example.com/" + "x" * 900
    context.claim.source_url = source
    content = '<a href="/not-authorized">Actual card</a>'
    context.browser = Session(
        [
            report(content, url=source, revision="f" * 64, format="html", error=native_error),
            report(content, url=source + "/next", revision="f" * 64, format="html"),
        ]
    )
    saved = {}

    def last(messages):
        value = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        assert json_bytes(value) <= 1024
        assert value["url"] is None and "url" in value["truncated_fields"]
        assert value["truncated"] and value["output"] == content
        assert value["page_revision"] == "f" * 64 and value["evidence_id"]
        assert not context.observed_pages
        return value

    def read_current(messages):
        value = last(messages)
        assert value["error"] == native_error
        saved["ref"] = value["evidence_id"]
        return call("read_rule_evidence", {"evidence_id": saved["ref"]})

    def navigate(messages):
        assert last(messages)["observation_state"] == "current"
        return call("inspect_web_page", {"argv": ["click", "a"]})

    def read_old(messages):
        last(messages)
        return call("read_rule_evidence", {"evidence_id": saved["ref"]})

    def fail(messages):
        value = last(messages)
        assert value["observation_state"] == "historical"
        assert value["restores_live_refs"] is False
        return call(
            "RuleAuthoringFailure",
            {
                "reason": "missing_evidence",
                "message": "Controlled long URL evidence was delivered successfully.",
                "evidence_ids": [saved["ref"]],
            },
        )

    model = ScriptModel(
        responses=[
            call("inspect_web_page", {"argv": ["open", source]}),
            read_current,
            navigate,
            read_old,
            fail,
        ]
    )
    with pytest.raises(AgentFailure, match="delivered successfully"):
        await run_rule_agent(context, model, preflight={})
    assert len(context.browser.calls) == 2


async def test_read_metadata_can_omit_previously_delivered_url_without_losing_identity():
    context = make_context(
        settings=Settings(_env_file=None, web_rule_agent_max_tool_result_bytes=1024)
    )
    empty = {
        **report("", url="", revision="f" * 64),
        "evidence_id": "evidence-" + "a" * 20,
        "phase": "inspect",
        "repeated": False,
        "truncated": False,
        "truncated_fields": [],
    }
    target_length = 1024 - json_bytes(empty) - 30
    source = "https://example.com/" + "x" * (target_length - len("https://example.com/"))
    context.claim.source_url = source
    context.browser = Session([report("Actual read content", url=source, revision="f" * 64)])
    first = await context.inspect(argv=["open", source])
    assert first["url"] == source and not first["truncated"]
    read = await context.read_evidence(first["evidence_id"])
    assert read["url"] is None and "url" in read["truncated_fields"]
    assert read["output"] == "Actual read content" and read["observation_state"] == "current"
    assert read["page_revision"] == "f" * 64 and json_bytes(read) <= 1024
    assert not context.observed_pages and len(context.browser.calls) == 1
