"""Factual execution memory and source-bound DOM evidence."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from test_web_rule_agent_runtime import (
    RULE,
    ScriptModel,
    call,
    execution_report,
    final,
    make_context,
)

from app.ingestion import web_rules
from app.services.web_rule_jobs import _diagnostic
from app.web_rule_agent.errors import AgentFailure
from app.web_rule_agent.memory import AuthoringMemory, bounded_messages, json_bytes
from app.web_rule_agent.runtime import run_rule_agent

SOURCE = "https://example.com/blog"


def test_evidence_can_be_recovered_after_complete_history_batches_are_trimmed():
    memory = AuthoringMemory()
    report = {
        "status": "pass",
        "page_revision": "revision-1",
        "nodes": [{"index": 81, "attributes": {"data-title": "Exact title"}}],
    }
    ref, repeated = memory.remember(report, view={"url": SOURCE, "selector": "article"})
    assert not repeated
    messages = [HumanMessage(content="task")]
    for index in range(12):
        messages.extend(
            [call("inspect_web_page"), ToolMessage(content="界" * 5000, tool_call_id="unused")]
        )
        messages[-1].tool_call_id = messages[-2].tool_calls[0]["id"]
    request = bounded_messages(messages, task="task", working_state={"candidate": RULE})
    assert len(request) < len(messages)
    for index, message in enumerate(request):
        if isinstance(message, ToolMessage):
            assert isinstance(request[index - 1], AIMessage)
            assert message.tool_call_id == request[index - 1].tool_calls[0]["id"]
    assert memory.read(ref)["nodes"][0]["attributes"]["data-title"] == "Exact title"
    memory.remember(
        {**report, "page_revision": "revision-2"}, view={"url": SOURCE, "selector": "article"}
    )
    assert memory.read(ref)["observation_state"] == "historical"


def test_unique_checkpoint_survives_small_diagnostic_retention():
    from datetime import UTC, datetime

    job = SimpleNamespace(diagnostics=[], limits_snapshot={"diagnostic_bytes": 4096})
    checkpoint = {
        "version": 1,
        "findings": {"listing": {"finding": "article cards", "evidence_ids": ["old-id"]}},
    }
    _diagnostic(
        job, phase="author", code="authoring_checkpoint", evidence=checkpoint, now=datetime.now(UTC)
    )
    for _ in range(30):
        _diagnostic(
            job,
            phase="tool",
            code="large",
            evidence={"content": "界" * 2000},
            now=datetime.now(UTC),
        )
    retained = [item for item in job.diagnostics if item["code"] == "authoring_checkpoint"]
    assert len(retained) == 1 and retained[0]["evidence"] == checkpoint
    assert json_bytes(job.diagnostics) <= 4096


@pytest.mark.parametrize(
    "code", ["browser_unavailable", "browser_configuration_changed", "TimeoutError"]
)
async def test_inspection_infrastructure_errors_are_task_failures(code):
    from app.ingestion.models import SourceScanError

    context = make_context()

    async def command(*args, **kwargs):
        raise SourceScanError(code, "actual infrastructure failure")

    context.browser = SimpleNamespace(command=command)
    with pytest.raises(AgentFailure) as exc:
        await context.inspect(argv=["open", SOURCE])
    assert exc.value.code == code


async def test_item_progress_survives_trim_and_restart_without_navigation_permissions():
    context = make_context()
    report = execution_report(
        {"items": [{"title": f"文章 {i}", "url": SOURCE + f"/{i}"} for i in range(19)]}
    )
    first = context.validation_feedback(report)
    assert len(first["items"]) == 5 and first["next_item_start"] == 5
    second = await context.read_evidence(report["execution_id"])
    assert second["item_start"] == 5 and second["next_item_start"] == 10
    assert context.memory.snapshot()["read_progress"][report["execution_id"]] == 10
    checkpoint = context.memory.checkpoint()
    claim = SimpleNamespace(**vars(context.claim))
    claim.last_feedback, claim.authoring_checkpoint = report, checkpoint
    from app.web_rule_agent.context import RuleAgentContext

    resumed = RuleAgentContext(claim=claim, store=context.store, settings=context.settings)
    third = await resumed.read_evidence(report["execution_id"])
    assert third["item_start"] == 10 and third["next_item_start"] == 15
    assert not resumed.observed_pages  # Historical execution URLs grant no page refs.
    last = await resumed.read_evidence(report["execution_id"])
    assert len(last["items"]) == 4 and last["next_item_start"] is None
    done = await resumed.read_evidence(report["execution_id"])
    assert done["items"] == [] and done["next_item_start"] is None


async def test_execution_can_be_followed_by_inspection_notes_or_honest_failure(monkeypatch):
    async def execute(**kwargs):
        return execution_report({"items": [{"title": "Contact", "url": SOURCE + "/contact"}]})

    monkeypatch.setattr(web_rules, "execute_web_rule", execute)
    context = make_context()
    model = ScriptModel(
        responses=[
            call("execute_web_rule", {"raw_rule": RULE}),
            call(
                "record_rule_findings",
                {
                    "topic": "unresolved",
                    "finding": "Contact is not an article.",
                    "evidence_ids": ["valid-receipt"],
                },
            ),
            call(
                "RuleAuthoringFailure",
                {
                    "reason": "missing_evidence",
                    "message": "Execution returned a contact link; "
                    "no usable article evidence is available.",
                    "evidence_ids": ["valid-receipt"],
                },
            ),
        ]
    )
    with pytest.raises(AgentFailure, match="contact link"):
        await run_rule_agent(context, model, preflight={})
    assert context.store.activated is None
    assert all("inspect_web_page" in binding for binding in model.bindings)


async def test_partial_execution_allows_model_final_with_history_limit(monkeypatch):
    async def execute(**kwargs):
        return {
            **execution_report(),
            "status": "partial",
            "errors": [{"code": "web_http_503", "window": 1}],
        }

    monkeypatch.setattr(web_rules, "execute_web_rule", execute)
    context = make_context()
    model = ScriptModel(responses=[call("execute_web_rule", {"raw_rule": RULE}), final()])
    assert (await run_rule_agent(context, model, preflight={}))["status"] == "succeeded"


def test_atomic_item_too_large_reports_output_error_without_advancing():
    from app.core.settings import Settings

    context = make_context(
        settings=Settings(_env_file=None, web_rule_agent_max_tool_result_bytes=1024)
    )
    report = execution_report({"items": [{"title": "界" * 1000, "url": SOURCE + "/article"}]})
    result = context.validation_feedback(report)
    assert result["items"] == [] and result["next_item_start"] == 0
    assert result["code"] == "execution_item_output_too_large"
    assert context.memory.read_progress["valid-receipt"] == 0
    assert json_bytes(result) <= 1024


async def test_latest_large_unicode_tool_batch_is_delivered_before_read_cursor_advances(
    monkeypatch,
):
    import httpx

    from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
    from app.web_rule_agent.memory import HISTORY_BYTES

    async with httpx.AsyncClient() as client:
        adapter = WebBlogSourceAdapter(
            WebBlogSourceConfig(url=SOURCE, rule=web_rules.parse_web_rule(RULE, source_url=SOURCE)),
            client,
        )
        items = [
            web_rules._execution_item(
                adapter._listing_item(SOURCE + f"/{i}", str(i) + "界" * 1259, {})
            )
            for i in range(20)
        ]
    assert all(len(item["title"]) <= 1261 for item in items)

    async def execute(**kwargs):
        return execution_report({"items": items})

    monkeypatch.setattr(web_rules, "execute_web_rule", execute)
    first_read = call("read_rule_evidence", {"evidence_id": "valid-receipt"})
    second_read = call("read_rule_evidence", {"evidence_id": "valid-receipt"}).tool_calls[0]
    first_read.tool_calls.append(second_read)
    context = make_context()

    def verify_delivery(messages):
        replies = [
            m
            for m in messages
            if isinstance(m, ToolMessage)
            and m.tool_call_id in {c["id"] for c in first_read.tool_calls}
        ]
        assert len(replies) == 2
        reports = [json.loads(reply.content) for reply in replies]
        assert sum(len(reply.content.encode()) for reply in replies) > HISTORY_BYTES
        assert all(
            len(reply.content.encode()) <= context.settings.web_rule_agent_max_tool_result_bytes
            for reply in replies
        )
        assert [item["url"] for report in reports for item in report["items"]] == [
            item["url"] for item in items[5:15]
        ]
        assert context.memory.read_progress["valid-receipt"] == 15
        ai = next(
            m
            for m in messages
            if isinstance(m, AIMessage)
            and {c["id"] for c in m.tool_calls} == {c["id"] for c in first_read.tool_calls}
        )
        assert {reply.tool_call_id for reply in replies} == {c["id"] for c in ai.tool_calls}
        return final()

    model = ScriptModel(
        responses=[call("execute_web_rule", {"raw_rule": RULE}), first_read, verify_delivery]
    )
    assert (await run_rule_agent(context, model, preflight={}))["status"] == "succeeded"
    first_output = next(m for m in model.requests[1] if isinstance(m, ToolMessage))
    assert [item["url"] for item in json.loads(first_output.content)["items"]] == [
        item["url"] for item in items[:5]
    ]
