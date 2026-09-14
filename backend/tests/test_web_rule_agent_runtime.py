from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from app.core.settings import Settings
from app.ingestion import web_rules as page_tools
from app.services.web_rule_jobs import JobGuardError
from app.web_rule_agent.context import RuleAgentContext, compact
from app.web_rule_agent.errors import AgentFailure, model_failure
from app.web_rule_agent.runtime import _request_bytes, _response_usage, run_rule_agent
from app.web_rule_agent.tools import RULE_TOOLS, FinalWebRule
from app.workers import web_rules as worker

RULE = {
    "id": "fixture",
    "hosts": ["example.com"],
    "index_paths": ["/blog"],
    "listing": {
        "extraction": {
            "name": "posts",
            "baseSelector": ".post",
            "fields": [
                {"name": "url", "selector": "a", "type": "attribute", "attribute": "href"},
                {"name": "title", "selector": "a", "type": "text"},
            ],
        }
    },
}


class ScriptModel(BaseChatModel):
    responses: list[Any]
    requests: list[list] = Field(default_factory=list)
    bindings: list[list[str]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "rule-author-controlled-test"

    def bind_tools(self, tools, **kwargs):
        self.bindings.append(
            [item.name if hasattr(item, "name") else item["function"]["name"] for item in tools]
        )
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("The backend Agent must use async model calls")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.requests.append(list(messages))
        if not self.responses:
            raise AssertionError("Unexpected model call after submission")
        response = self.responses.pop(0)
        if callable(response):
            response = response(messages)
        if isinstance(response, BaseException):
            raise response
        return ChatResult(generations=[ChatGeneration(message=response)])


def call(
    name: str, args: dict | None = None, *, extra_calls: list | None = None, usage: bool = True
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": (
                    {"argv": ["snapshot", "-u"]}
                    if name == "inspect_web_page" and args is None
                    else args or {}
                ),
                "id": str(uuid4()),
                "type": "tool_call",
            },
            *(extra_calls or []),
        ],
        usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}
        if usage
        else None,
    )


def final(rule: dict | None = None, *, validation_id="valid-receipt", extra_calls=None):
    return call(
        "FinalWebRule",
        {
            "rule": rule if rule is not None else RULE,
            "execution_id": validation_id,
            "assessment": "Actual titles and links match the observed listing.",
            "limitations": ["History unverified"],
        },
        extra_calls=extra_calls,
    )


def execution_report(value=None):
    value = dict(value or {})
    failed = value.get("status") == "fail"
    value["status"] = "error" if failed else "completed"
    value["execution_id"] = "valid-receipt"
    value["first_window_completed"] = not failed
    value.setdefault("items", [])
    value["errors"] = (
        [{"code": value.get("code"), "message": value.get("message", "")}] if failed else []
    )
    return value


class MemoryStore:
    """Retains reservation evidence across graph attempts, like the durable Store contract."""

    def __init__(self, *, max_tokens: int = 3_000_000, max_models: int = 80, busy: bool = False):
        self.max_tokens = max_tokens
        self.max_models = max_models
        self.total_tokens = 0
        self.model_reservations: list[int] = []
        self.usage_records: list[dict | None] = []
        self.tool_calls: list[str] = []
        self.candidates: list[dict] = []
        self.reports: list[dict] = []
        self.diagnostics: list[dict] = []
        self.finish_records: list[dict] = []
        self.activated: dict | None = None
        self.busy = busy
        self.lease_valid = True
        self.current_receipt = None
        self.paused = None
        self.goal_feedback: list[dict] = []
        self.authority_error: str | None = None
        self.checkpoints = []
        self.protection_report = {}

    async def progress(self, claim):
        await self.check(claim)
        return {
            "model_calls_used": len(self.model_reservations),
            "model_calls_remaining": max(0, self.max_models - len(self.model_reservations)),
            "tokens_used": self.total_tokens,
            "tokens_remaining": max(0, self.max_tokens - self.total_tokens),
        }

    async def checkpoint(self, claim, checkpoint):
        await self.check(claim)
        self.checkpoints.append(checkpoint)

    async def check(self, claim):
        if self.authority_error:
            raise JobGuardError(self.authority_error)
        if not self.lease_valid:
            raise JobGuardError("lease_lost")

    async def heartbeat(self, claim):
        return self.lease_valid

    async def reserve_model(self, claim, estimated_tokens):
        await self.check(claim)
        if len(self.model_reservations) >= self.max_models:
            raise JobGuardError("model_budget_exhausted")
        if self.total_tokens >= self.max_tokens:
            raise JobGuardError("token_budget_exhausted")
        self.model_reservations.append(estimated_tokens)
        return str(len(self.model_reservations))

    async def record_usage(self, claim, reservation_id, usage):
        self.usage_records.append(usage)
        self.total_tokens += (
            usage["total_tokens"]
            if usage is not None
            else self.model_reservations[int(reservation_id) - 1]
        )

    async def reserve_tool(self, claim, name, *, validation=False):
        await self.check(claim)
        self.tool_calls.append(name)

    async def diagnostic(self, claim, **record):
        self.diagnostics.append(record)

    async def save_validation(self, claim, candidate, report):
        await self.check(claim)
        self.protection_report = report
        self.candidates.append(candidate)
        self.reports.append(report)
        self.current_receipt = (
            report.get("execution_id") if report.get("first_window_completed") else None
        )
        return {
            **report,
            **({"execution_id": self.current_receipt} if self.current_receipt else {}),
        }

    async def save_recovery_check(self, claim, report):
        await self.check(claim)
        self.diagnostics.append({"phase": "preflight", "report": report})
        return report

    async def activate(
        self, claim, validation_id, *, raw_rule=None, assessment=None, limitations=None
    ):
        await self.check(claim)
        if not self.current_receipt or validation_id != self.current_receipt:
            raise JobGuardError("validation_expired")
        if raw_rule is not None and (
            page_tools.parse_web_rule(raw_rule, source_url=claim.source_url)
            != page_tools.parse_web_rule(self.candidates[-1], source_url=claim.source_url)
        ):
            raise JobGuardError("final_rule_mismatch")
        if self.busy:
            return {"status": "activation_pending", "code": "source_scan_busy"}
        self.activated = self.candidates[-1]
        return {"status": "succeeded", "version": "accepted-version"}

    async def resume_goal(self, claim, feedback):
        await self.check(claim)
        self.goal_feedback.append(feedback)
        return len(self.goal_feedback)

    async def finish(self, claim, **record):
        self.finish_records.append(record)

    async def pause(self, snapshot, **record):
        self.paused = record

    async def finish_and_pause(self, claim, *, engine_snapshot, **record):
        await self.check(claim)
        await self.finish(claim, **record)
        await self.pause(engine_snapshot, claim=claim, code=record["code"])

    async def release(self, claim):
        return None


def make_context(*, store=None, base_rule=None, stage="preflight", candidate=None, settings=None):
    return RuleAgentContext(
        claim=SimpleNamespace(
            job_id=uuid4(),
            source_id=uuid4(),
            source_url="https://example.com/blog",
            lease_token=uuid4(),
            deadline_at=None,
            base_rule=base_rule,
            candidate_rule=candidate,
            stage=stage,
            budget_snapshot={},
            engine_snapshot={},
        ),
        store=store or MemoryStore(),
        settings=settings or Settings(_env_file=None),
    )


@pytest.fixture(autouse=True)
def source_browser_fixture(monkeypatch):
    """Scripted graph tests simulate CLI transport; real wiring has an opt-in fixture."""
    from app.ingestion.models import SourceScanError
    from app.web_rule_agent import cli_browser

    class TestBrowser:
        def __init__(self, settings, url, **kwargs):
            self.url = url

        async def command(self, argv, *, observed_urls=()):
            if argv[0] == "open" and argv[1] not in {self.url, *observed_urls}:
                return {
                    "status": "error",
                    "format": "text",
                    "output": "",
                    "page_revision": None,
                    "url": self.url,
                    "error": {
                        "source": "wrapper",
                        "message": "open requires observed same-origin URL",
                        "exit_code": None,
                    },
                }
            report = await page_tools.inspect_web_page(source_url=self.url, argv=argv)
            if "format" in report:
                return report
            if report.get("status") == "fail":
                raise SourceScanError(report["code"], report["code"])
            return {
                "status": "ok",
                "format": "html",
                "output": report.get("html", "listing"),
                "url": self.url,
                "page_revision": "fixture",
                "error": None,
            }

        async def aclose(self):
            pass

    monkeypatch.setattr(cli_browser, "CLIBrowserSession", TestBrowser)


@pytest.mark.asyncio
async def test_real_graph_reads_feedback_repairs_then_stops_immediately_after_submit(monkeypatch):
    async def inspect(**kwargs):
        return {"status": "pass", "html": '<article class="post">real page</article>', "links": []}

    async def validate(**kwargs):
        if kwargs["raw_rule"].get("id") == "wrong":
            return execution_report(
                {"status": "fail", "code": "web_structure_changed", "valid_pairs": 0}
            )
        return execution_report(
            {"status": "pass", "items": [{"url": "/post/1", "title": "Actual post"}]}
        )

    monkeypatch.setattr(page_tools, "inspect_web_page", inspect)
    monkeypatch.setattr(page_tools, "execute_web_rule", validate)

    def repair(messages):
        feedback = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        assert feedback["code"] == "web_structure_changed"
        return call("execute_web_rule", {"raw_rule": RULE})

    model = ScriptModel(
        responses=[
            call("web_rule_schema"),
            call("inspect_web_page"),
            call("execute_web_rule", {"raw_rule": {**RULE, "id": "wrong"}}),
            repair,
            final(),
        ]
    )
    # Some compatible providers reuse call IDs in successive model responses.
    # Dispatch ordering is scoped to a message, never just its call ID strings.
    for response in model.responses:
        if isinstance(response, AIMessage):
            response.tool_calls[0]["id"] = "provider-call-0"
    context = make_context()
    outcome = await run_rule_agent(context, model, preflight={"status": "pass"})
    assert outcome["status"] == "succeeded"
    assert context.store.activated == RULE
    assert len(model.requests) == 5
    assert len(context.store.model_reservations) == len(context.store.usage_records) == 5
    assert [r["status"] for r in context.store.reports] == ["error", "completed"]
    for tool in RULE_TOOLS:
        properties = tool.tool_call_schema.model_json_schema().get("properties", {})
        assert not ({"runtime", "source_id", "source_url", "lease_token"} & properties.keys())
    assert {tool.name for tool in RULE_TOOLS} == {
        "web_rule_schema",
        "inspect_web_page",
        "execute_web_rule",
        "read_rule_evidence",
        "record_rule_findings",
    }


@pytest.mark.asyncio
async def test_model_claiming_success_in_text_cannot_activate_and_tokens_bound_resumption():
    context = make_context(store=MemoryStore(max_tokens=300))
    model = ScriptModel(
        responses=[
            AIMessage(
                content='{"status":"pass","saved":true}',
                usage_metadata={"input_tokens": 90, "output_tokens": 10, "total_tokens": 100},
            )
            for _ in range(3)
        ]
    )
    with pytest.raises(JobGuardError, match="token_budget_exhausted"):
        await run_rule_agent(context, model, preflight={})
    assert context.store.activated is None
    assert context.store.total_tokens == 300
    assert len(context.store.goal_feedback) == 3
    assert context.store.model_reservations[0] > 4096


@pytest.mark.asyncio
async def test_native_tool_schema_exposes_arrays_and_preserves_invalid_candidate_feedback(
    monkeypatch,
):
    validation_tool = next(tool for tool in RULE_TOOLS if tool.name == "execute_web_rule")
    properties = convert_to_openai_tool(validation_tool)["function"]["parameters"]["properties"]
    native = properties["raw_rule"]["properties"]
    assert native["hosts"]["type"] == native["index_paths"]["type"] == "array"
    extraction = native["listing"]["properties"]["extraction"]["properties"]
    assert extraction["fields"]["type"] == extraction["baseFields"]["type"] == "array"
    assert "runtime" not in properties

    malformed = {**RULE, "hosts": {"item": "example.com"}}

    async def validate(**kwargs):
        try:
            page_tools.parse_web_rule(kwargs["raw_rule"], source_url="https://example.com/blog")
        except ValueError as exc:
            return execution_report({"status": "fail", "code": "WebRuleError", "message": str(exc)})
        return execution_report({"status": "pass", "items": [{"url": "/post/1", "title": "Post"}]})

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)

    def repair(messages):
        feedback = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        assert feedback["code"] == "WebRuleError"
        assert "valid list" in feedback["message"]
        return call("execute_web_rule", {"raw_rule": RULE})

    context = make_context()
    model = ScriptModel(
        responses=[
            call("execute_web_rule", {"raw_rule": malformed}),
            repair,
            final(),
        ]
    )
    await run_rule_agent(context, model, preflight={})
    assert [report["status"] for report in context.store.reports] == ["error", "completed"]
    assert context.store.activated == RULE


@pytest.mark.asyncio
async def test_parallel_tools_are_serial_and_unobserved_urls_never_reach_crawler(monkeypatch):
    active = peak = calls = 0

    async def inspect(**kwargs):
        nonlocal active, peak, calls
        active += 1
        peak = max(active, peak)
        calls += 1
        await asyncio.sleep(0.01)
        active -= 1
        return {"status": "pass", "links": [{"url": "https://other.example/private"}]}

    monkeypatch.setattr(page_tools, "inspect_web_page", inspect)
    model = ScriptModel(
        responses=[
            call(
                "inspect_web_page",
                extra_calls=[
                    {
                        "name": "inspect_web_page",
                        "args": {"argv": ["snapshot", "-u"]},
                        "id": "second",
                        "type": "tool_call",
                    }
                ],
            ),
            call("inspect_web_page", {"argv": ["open", "https://127.0.0.1/"]}),
            AIMessage(
                content="No rule.",
                usage_metadata={
                    "input_tokens": 300_000,
                    "output_tokens": 1,
                    "total_tokens": 300_001,
                },
            ),
        ]
    )
    context = make_context(store=MemoryStore(max_tokens=300_000))
    with pytest.raises(JobGuardError, match="token_budget_exhausted"):
        await run_rule_agent(context, model, preflight={})
    assert peak == 1 and calls == 2
    assert context.observed_pages == {}
    messages = model.requests[-1]
    assert any("observed same-origin" in m.content for m in messages if isinstance(m, ToolMessage))


@pytest.mark.asyncio
async def test_activation_pending_hands_off_without_another_model_call(monkeypatch):
    async def validate(**kwargs):
        return execution_report({"status": "pass"})

    async def forbidden_inspect(**kwargs):
        raise AssertionError("No more crawl after submit")

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    monkeypatch.setattr(page_tools, "inspect_web_page", forbidden_inspect)
    model = ScriptModel(
        responses=[
            call("execute_web_rule", {"raw_rule": RULE}),
            final(),
        ]
    )
    context = make_context(store=MemoryStore(busy=True))
    result = await run_rule_agent(context, model, preflight={})
    assert result["status"] == "activation_pending"
    assert len(model.requests) == 2
    assert context.store.tool_calls == ["execute_web_rule", "submit_web_rule"]
    assert context.store.goal_feedback == []


@pytest.mark.asyncio
async def test_token_budget_cannot_reset_across_attempts():
    store = MemoryStore(max_tokens=1)
    context = make_context(store=store)
    first = ScriptModel(responses=[AIMessage(content="Stopped")])
    with pytest.raises(JobGuardError, match="token_budget_exhausted"):
        await run_rule_agent(context, first, preflight={})
    second = ScriptModel(responses=[AIMessage(content="Not called")])
    with pytest.raises(JobGuardError, match="token_budget_exhausted"):
        await run_rule_agent(make_context(store=store), second, preflight={})
    assert second.requests == []


@pytest.mark.asyncio
async def test_model_402_is_unknown_usage_and_blocks_engine_without_raw_body():
    class ProviderError(Exception):
        status_code = 402

    context = make_context()
    with pytest.raises(AgentFailure) as error:
        await run_rule_agent(
            context,
            ScriptModel(responses=[ProviderError("private provider response")]),
            preflight={},
        )
    assert error.value.pause_engine and error.value.status == "blocked"
    assert "private provider" not in error.value.message
    assert context.store.usage_records == [None]


@pytest.mark.asyncio
async def test_preflight_recovered_rule_and_activation_resume_never_construct_model(monkeypatch):
    async def validate(**kwargs):
        return execution_report({"status": "pass"})

    def forbidden_model(*args, **kwargs):
        raise AssertionError("No model for recovered rule or saved activation")

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    recovered = make_context(base_rule=RULE)
    await worker._execute_claim(recovered, None, forbidden_model)
    assert recovered.store.finish_records[0]["code"] == "source_recovered"
    assert recovered.store.activated is None
    resumed = make_context(stage="activate", candidate=RULE)
    await worker._execute_claim(resumed, None, forbidden_model)
    assert resumed.store.activated == RULE
    assert resumed.store.tool_calls == ["execute_web_rule", "submit_web_rule"]


@pytest.mark.asyncio
async def test_preflight_http_failure_avoids_model_and_lease_loss_cancels_local_work(monkeypatch):
    async def unavailable(**kwargs):
        return {"status": "fail", "code": "web_http_429", "retryable": True}

    monkeypatch.setattr(page_tools, "inspect_web_page", unavailable)
    context = make_context()
    with pytest.raises(AgentFailure) as failure:
        await worker._execute_claim(context, None, lambda *a, **kw: pytest.fail("Model called"))
    assert failure.value.status == "retry_wait"
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    context.settings.web_rule_agent_heartbeat_seconds = 0.01
    context.store.lease_valid = False
    with pytest.raises(JobGuardError, match="lease_lost"):
        await worker._run_with_heartbeat(context, work())
    assert started.is_set() and cancelled.is_set()
    with pytest.raises(JobGuardError, match="lease_lost"):
        await context.check()


def test_tool_feedback_remains_valid_bounded_json_and_retry_after_is_obeyed():
    result = compact({"status": "pass", "validation_id": "opaque", "html": "🙂" * 60000}, 20480)
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 20480
    assert result["validation_id"] == "opaque" and result["truncated"]
    error = SimpleNamespace(
        status_code=429, response=SimpleNamespace(headers={"retry-after": "120"})
    )
    assert model_failure(error).retry_after_seconds == 120


async def test_inspection_trace_describes_actual_delivery_without_copying_content(monkeypatch):
    import hashlib

    async def inspect(**kwargs):
        return {
            "status": "ok",
            "format": "text",
            "output": "Actual title",
            "url": "https://example.com/blog",
            "page_revision": "revision",
            "error": None,
        }

    monkeypatch.setattr(page_tools, "inspect_web_page", inspect)
    context = make_context()
    result = await context.inspect(argv=["get", "text", "article"])
    returned = json.dumps(result, ensure_ascii=False).encode("utf-8")
    trace = context.store.diagnostics[-1]["evidence"]
    assert trace["returned_bytes"] == len(returned)
    assert trace["format"] == "text"
    assert trace["evidence_sha256"] == hashlib.sha256(returned).hexdigest()
    assert "output" not in trace and "html" not in trace


async def test_malformed_page_links_do_not_abort_preflight(monkeypatch):
    async def inspect(**kwargs):
        return {
            "status": "ok",
            "format": "html",
            "url": kwargs["source_url"],
            "page_revision": "r",
            "error": None,
            "output": (
                '<a href="http://[bad">bad</a>'
                '<a href="https://example.com@other.example/post">other</a>'
                '<a href="/post/valid#section">valid</a>'
            ),
        }

    monkeypatch.setattr(page_tools, "inspect_web_page", inspect)
    context = make_context()
    result = await worker._preflight(context)
    assert result["status"] == "ok"
    assert list(context.observed_pages.values()) == ["https://example.com/post/valid"]
    assert "page_ref" not in result


@pytest.mark.parametrize("source", ["cli", "wrapper"])
async def test_correctable_inspection_error_reaches_model_for_correction(monkeypatch, source):
    async def inspect(**kwargs):
        if kwargs["argv"][0] == "click":
            return {
                "status": "error",
                "format": "text",
                "output": "",
                "page_revision": "r",
                "error": {"source": source, "message": "actual missing selector", "exit_code": 1},
            }
        return {"status": "pass", "html": "<article>Actual listing</article>"}

    async def validate(**kwargs):
        return execution_report()

    def corrected(messages):
        report = json.loads(
            next(m.content for m in reversed(messages) if isinstance(m, ToolMessage))
        )
        assert report["error"] == {
            "source": source,
            "message": "actual missing selector",
            "exit_code": 1,
        }
        return call("inspect_web_page")

    monkeypatch.setattr(page_tools, "inspect_web_page", inspect)
    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    context = make_context()
    model = ScriptModel(
        responses=[
            call("inspect_web_page", {"argv": ["click", ".missing"]}),
            corrected,
            call("execute_web_rule", {"raw_rule": RULE}),
            final(),
        ]
    )
    assert (await run_rule_agent(context, model, preflight={}))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_model_timeout_records_unknown_usage_before_retry():
    class WaitingModel(ScriptModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            await asyncio.Event().wait()

    context = make_context()
    context.settings.web_rule_agent_model_timeout_seconds = 0.01
    with pytest.raises(AgentFailure) as failure:
        await run_rule_agent(context, WaitingModel(responses=[]), preflight={})
    assert failure.value.status == "retry_wait"
    assert context.store.usage_records == [None]
    assert len(context.store.model_reservations) == 1


@pytest.mark.asyncio
async def test_worker_persists_provider_block_and_releases_slot(monkeypatch):
    class ProviderError(Exception):
        status_code = 402

    class CycleStore(MemoryStore):
        released = False

        async def _call(self, name, **kwargs):
            if name == "backfill_web_rule_jobs":
                return 0
            if name == "claim_web_rule_job":
                return context.claim
            if name == "web_rule_agent_status":
                return {"mode": "paused" if self.paused else "active"}
            raise AssertionError(name)

        async def release(self, claim):
            self.released = True

    async def inspect(**kwargs):
        return {"status": "pass", "html": "listing", "links": []}

    monkeypatch.setattr(page_tools, "inspect_web_page", inspect)
    store = CycleStore()
    settings = Settings(_env_file=None, web_rule_agent_engine_id="deepseek-v4-flash")
    context = make_context(store=store, settings=settings)
    result = await worker.run_web_rule_author_once(
        None,
        settings=settings,
        store=store,
        model_factory=lambda *a, **kw: ScriptModel(responses=[ProviderError("provider body")]),
    )
    assert result == 1 and result.metrics["mode"] == "paused"
    assert store.finish_records[0]["status"] == "blocked"
    assert store.paused["code"] == "model_http_402"
    assert store.paused["claim"] == context.claim
    assert store.released and store.usage_records == [None]


@pytest.mark.asyncio
@pytest.mark.parametrize("premature_text", ["Done", ""])
async def test_goal_resumes_plain_or_empty_final_then_accepts_real_validation(
    monkeypatch, premature_text
):
    async def validate(**kwargs):
        return execution_report({"status": "pass"})

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    context = make_context()
    model = ScriptModel(
        responses=[
            AIMessage(content=premature_text),
            call("execute_web_rule", {"raw_rule": RULE}),
            final(),
        ]
    )
    result = await run_rule_agent(context, model, preflight={})
    assert result["status"] == "succeeded"
    assert len(context.store.goal_feedback) == 1
    assert context.store.goal_feedback[0]["code"] == "structured_completion_required"
    assert len(context.store.model_reservations) == len(model.requests) == 3
    assert any("HOST SUBMISSION FEEDBACK" in str(m.content) for m in model.requests[1])


@pytest.mark.asyncio
async def test_tool_strategy_corrects_schema_error_without_goal_resumption(monkeypatch):
    async def validate(**kwargs):
        return execution_report({"status": "pass"})

    def corrected(messages):
        errors = [str(message.content) for message in messages if isinstance(message, ToolMessage)]
        assert any("hosts" in message and "valid list" in message for message in errors)
        return final()

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    context = make_context()
    model = ScriptModel(
        responses=[
            call("execute_web_rule", {"raw_rule": RULE}),
            final({**RULE, "hosts": {"item": "example.com"}}),
            corrected,
        ]
    )
    result = await run_rule_agent(context, model, preflight={})
    assert result["status"] == "succeeded"
    assert context.store.goal_feedback == []
    assert len(context.store.usage_records) == 3
    assert all(usage["total_tokens"] == 140 for usage in context.store.usage_records)


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["validation_expired", "final_rule_mismatch"])
async def test_host_rejects_forged_receipt_or_changed_final_and_allows_correction(
    monkeypatch, rejection
):
    async def validate(**kwargs):
        return execution_report({"status": "pass"})

    def corrected(messages):
        assert any(rejection in str(message.content) for message in messages)
        return final()

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    bad_final = (
        final(validation_id="model-invented-receipt")
        if rejection == "validation_expired"
        else final({**RULE, "id": "changed-after-validation"})
    )
    context = make_context()
    model = ScriptModel(
        responses=[
            call("execute_web_rule", {"raw_rule": RULE}),
            bad_final,
            corrected,
        ]
    )
    result = await run_rule_agent(context, model, preflight={})
    assert result["status"] == "succeeded"
    assert context.store.activated == RULE
    assert context.store.goal_feedback[0]["code"] == rejection
    assert len(context.store.goal_feedback) == 1


@pytest.mark.asyncio
async def test_expired_receipt_requires_fresh_validation_before_host_activation(monkeypatch):
    class ExpiringStore(MemoryStore):
        activation_attempts = 0

        async def activate(
            self, claim, validation_id, *, raw_rule=None, assessment=None, limitations=None
        ):
            self.activation_attempts += 1
            if self.activation_attempts == 1:
                self.current_receipt = None
                raise JobGuardError("validation_expired")
            return await super().activate(claim, validation_id, raw_rule=raw_rule)

    async def validate(**kwargs):
        return execution_report({"status": "pass"})

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    context = make_context(store=ExpiringStore())
    model = ScriptModel(
        responses=[
            call("execute_web_rule", {"raw_rule": RULE}),
            final(),
            call("execute_web_rule", {"raw_rule": RULE}),
            final(),
        ]
    )
    result = await run_rule_agent(context, model, preflight={})
    assert result["status"] == "succeeded"
    assert context.store.tool_calls.count("execute_web_rule") == 2
    assert len(context.store.goal_feedback) == 1


@pytest.mark.asyncio
async def test_goal_never_reuses_old_structured_output_after_plain_end():
    context = make_context(store=MemoryStore(max_tokens=420))
    model = ScriptModel(
        responses=[
            final(validation_id="forged"),
            AIMessage(
                content="Done",
                usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
            ),
            AIMessage(
                content="",
                usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
            ),
        ]
    )
    with pytest.raises(JobGuardError, match="token_budget_exhausted"):
        await run_rule_agent(context, model, preflight={})
    assert context.store.activated is None
    # Only the actual structured output reaches the host activation service.
    assert context.store.tool_calls == ["submit_web_rule"]
    assert [feedback["code"] for feedback in context.store.goal_feedback] == [
        "validation_expired",
        "structured_completion_required",
        "structured_completion_required",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("final_first", [True, False])
async def test_mixed_structured_and_real_tool_batch_never_waits_for_internal_tool(
    monkeypatch, final_first
):
    async def validate(**kwargs):
        return execution_report({"status": "pass"})

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    final_call = final().tool_calls[0]
    validation_call = call("execute_web_rule", {"raw_rule": RULE}).tool_calls[0]
    mixed = AIMessage(
        content="",
        tool_calls=[final_call, validation_call] if final_first else [validation_call, final_call],
        usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
    )
    context = make_context()
    model = ScriptModel(responses=[mixed])
    result = await asyncio.wait_for(run_rule_agent(context, model, preflight={}), timeout=2)
    assert result["status"] == "succeeded"
    assert context.store.tool_calls == ["execute_web_rule", "submit_web_rule"]
    assert len(model.requests) == 1 and context.store.goal_feedback == []


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["source_inactive", "input_changed", "lease_lost"])
async def test_authority_failure_does_not_reserve_a_goal_resumption(guard):
    context = make_context()

    def revoked(messages):
        context.store.authority_error = guard
        return final()

    model = ScriptModel(responses=[revoked])
    with pytest.raises(JobGuardError, match=guard):
        await run_rule_agent(context, model, preflight={})
    assert context.store.goal_feedback == [] and context.store.activated is None
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_tool_permission_exception_never_resumes_goal(monkeypatch):
    async def forbidden(**kwargs):
        raise PermissionError("tool permission revoked")

    monkeypatch.setattr(page_tools, "inspect_web_page", forbidden)
    context = make_context()
    model = ScriptModel(responses=[call("inspect_web_page")])
    with pytest.raises(PermissionError, match="permission revoked"):
        await run_rule_agent(context, model, preflight={})
    assert len(model.requests) == 1 and context.store.goal_feedback == []


def test_request_budget_includes_final_schema_and_usage_ignores_generated_tool_messages():
    request = SimpleNamespace(
        messages=[AIMessage(content="Context")],
        system_message=None,
        tools=RULE_TOOLS,
        response_format=None,
    )
    without_final_schema = _request_bytes(request)
    request.response_format = ToolStrategy(FinalWebRule)
    assert _request_bytes(request) > without_final_schema + 1000
    response = SimpleNamespace(
        result=[
            final(),
            ToolMessage(content="Structured candidate accepted for parsing", tool_call_id="x"),
        ]
    )
    assert _response_usage(response) == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
    }


@pytest.mark.asyncio
async def test_duplicate_id_across_final_and_real_tool_fails_before_dispatch(monkeypatch):
    async def forbidden_inspect(**kwargs):
        raise AssertionError("No tool may execute from an ambiguous batch")

    monkeypatch.setattr(page_tools, "inspect_web_page", forbidden_inspect)
    final_call = final().tool_calls[0]
    final_call["id"] = "x"
    mixed = AIMessage(
        content="",
        tool_calls=[
            final_call,
            {"id": "x", "name": "inspect_web_page", "args": {}},
            {"id": "y", "name": "web_rule_schema", "args": {}},
        ],
        usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
    )
    context = make_context()
    model = ScriptModel(responses=[mixed])
    with pytest.raises(AgentFailure) as failure:
        await asyncio.wait_for(run_rule_agent(context, model, preflight={}), timeout=2)
    assert failure.value.code == "invalid_tool_batch"
    assert context.store.tool_calls == [] and context.store.goal_feedback == []
    assert context.store.activated is None and len(model.requests) == 1
    assert context.store.usage_records == [
        {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}
    ]


@pytest.mark.asyncio
async def test_last_model_call_can_finish_without_other_goal_tool_context_or_deadline_caps(
    monkeypatch,
):
    async def validate(**kwargs):
        return execution_report({"status": "pass"})

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    context = make_context()
    # Historical deadline values must not impose a hidden wall-clock quota.
    context.claim.deadline_at = datetime.now(UTC) - timedelta(minutes=1)
    model = ScriptModel(
        responses=[
            *[
                AIMessage(
                    content="Continue",
                    usage_metadata={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
                )
                for _ in range(12)
            ],
            *[
                call(
                    "execute_web_rule",
                    {"raw_rule": RULE},
                    extra_calls=[
                        {
                            "name": "execute_web_rule",
                            "args": {"raw_rule": RULE},
                            "id": str(uuid4()),
                            "type": "tool_call",
                        }
                        for _ in range(3)
                    ],
                )
                for _ in range(67)
            ],
            final(),
        ]
    )
    result = await run_rule_agent(context, model, preflight={"html": "x" * 550_000})
    assert result["status"] == "succeeded"
    assert len(model.requests) == 80 and len(context.store.goal_feedback) == 12
    assert context.store.tool_calls.count("execute_web_rule") == 268
    assert context.store.total_tokens == 80 * 140


@pytest.mark.asyncio
async def test_model_call_cap_stops_81st_request_independent_of_reported_tokens():
    context = make_context()
    model = ScriptModel(responses=[call("web_rule_schema") for _ in range(80)])
    with pytest.raises(JobGuardError, match="model_budget_exhausted"):
        await run_rule_agent(context, model, preflight={})
    assert len(model.requests) == 80 and context.store.total_tokens < context.store.max_tokens
    # Recreating the Agent cannot give this job another model call.
    another = ScriptModel(responses=[])
    with pytest.raises(JobGuardError, match="model_budget_exhausted"):
        await run_rule_agent(make_context(store=context.store), another, preflight={})
    assert another.requests == []


@pytest.mark.asyncio
async def test_final_response_can_finish_after_crossing_recorded_token_threshold(monkeypatch):
    async def validate(**kwargs):
        return execution_report({"status": "pass"})

    monkeypatch.setattr(page_tools, "execute_web_rule", validate)
    context = make_context(store=MemoryStore(max_tokens=141))
    model = ScriptModel(responses=[call("execute_web_rule", {"raw_rule": RULE}), final()])
    assert (await run_rule_agent(context, model, preflight={}))["status"] == "succeeded"
    assert len(model.requests) == 2 and context.store.total_tokens == 280


@pytest.mark.asyncio
async def test_zero_provider_usage_is_unknown_and_cannot_continue_for_free():
    context = make_context(store=MemoryStore(max_tokens=1))
    model = ScriptModel(
        responses=[
            AIMessage(
                content="Continue",
                usage_metadata={
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
            )
        ]
    )
    with pytest.raises(JobGuardError, match="token_budget_exhausted"):
        await run_rule_agent(context, model, preflight={})
    assert len(model.requests) == 1
    assert context.store.usage_records == [None]
    assert context.store.total_tokens == context.store.model_reservations[0] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("premature_text", ["Done", ""])
async def test_plain_completion_recovers_to_evidence_backed_no_usable_rule(
    monkeypatch, premature_text
):
    async def execute(**kwargs):
        return execution_report(
            {"items": [{"title": "Contact", "url": "https://example.com/contact"}]}
        )

    monkeypatch.setattr(page_tools, "execute_web_rule", execute)
    context = make_context()
    model = ScriptModel(
        responses=[
            call("execute_web_rule", {"raw_rule": RULE}),
            AIMessage(content=premature_text),
            call(
                "RuleAuthoringFailure",
                {
                    "reason": "no_usable_rule",
                    "message": (
                        "The trial returned a contact page, not the requested article listing."
                    ),
                    "evidence_ids": ["valid-receipt"],
                },
            ),
        ]
    )
    with pytest.raises(AgentFailure) as raised:
        await run_rule_agent(context, model, preflight={})
    assert raised.value.code == "web_rule_no_usable_rule"
    assert context.store.activated is None
    assert len(context.store.goal_feedback) == 1
    assert context.store.goal_feedback[0]["code"] == "structured_completion_required"
    diagnostic = context.store.diagnostics[-1]
    assert diagnostic["phase"] == "finish" and diagnostic["code"] == "no_usable_rule"
    assert diagnostic["evidence"]["evidence_ids"] == ["valid-receipt"]
    assert len(context.store.model_reservations) == len(context.store.usage_records) == 3


@pytest.mark.asyncio
async def test_unknown_failure_evidence_can_be_corrected_without_activation(monkeypatch):
    async def execute(**kwargs):
        return execution_report({"items": []})

    monkeypatch.setattr(page_tools, "execute_web_rule", execute)
    context = make_context()
    failure_args = {
        "reason": "no_usable_rule",
        "message": "The available execution evidence does not support a useful article rule.",
        "evidence_ids": ["invented-id"],
    }
    model = ScriptModel(
        responses=[
            call("execute_web_rule", {"raw_rule": RULE}),
            call("RuleAuthoringFailure", failure_args),
            call("RuleAuthoringFailure", {**failure_args, "evidence_ids": ["valid-receipt"]}),
        ]
    )
    with pytest.raises(AgentFailure) as raised:
        await run_rule_agent(context, model, preflight={})
    assert raised.value.code == "web_rule_no_usable_rule"
    assert [entry["code"] for entry in context.store.goal_feedback] == ["failure_evidence_required"]
    assert context.store.activated is None
    assert sum(entry.get("phase") == "finish" for entry in context.store.diagnostics) == 1


def request_progress(messages):
    host = next(
        message for message in messages if str(message.content).startswith("HOST AUTHORING STATE")
    )
    return json.loads(host.content.split("\n", 1)[1])["progress"]


@pytest.mark.asyncio
async def test_budget_facts_allow_trial_then_last_response_submission(monkeypatch):
    async def execute(**kwargs):
        return execution_report({"items": [{"title": "Post", "url": "https://example.com/post"}]})

    monkeypatch.setattr(page_tools, "execute_web_rule", execute)
    context = make_context(store=MemoryStore(max_models=2, max_tokens=10000))
    model = ScriptModel(responses=[call("execute_web_rule", {"raw_rule": RULE}), final()])
    result = await run_rule_agent(context, model, preflight={})
    assert result["status"] == "succeeded"
    first, last = [request_progress(messages) for messages in model.requests]
    assert (first["model_calls_remaining"], first["model_calls_after_current_response"]) == (2, 1)
    assert (last["model_calls_remaining"], last["model_calls_after_current_response"]) == (1, 0)
    assert first["tokens_remaining"] == 10000 and last["tokens_remaining"] == 9860
    assert last["tokens_remaining_basis"] == "recorded_ledger_usage_before_current_response"
    assert last["model_calls_remaining_includes_current_response"] is True
    assert last["estimated_tokens_per_remaining_call"] > 0
    assert last["estimate_is_not_a_spending_limit"] is True
    assert all(
        {"inspect_web_page", "execute_web_rule", "FinalWebRule", "RuleAuthoringFailure"}
        <= set(names)
        for names in model.bindings
    )


@pytest.mark.asyncio
async def test_last_response_can_still_execute_without_automatic_submission(monkeypatch):
    async def execute(**kwargs):
        return execution_report({"items": [{"title": "Post", "url": "https://example.com/post"}]})

    monkeypatch.setattr(page_tools, "execute_web_rule", execute)
    context = make_context(store=MemoryStore(max_models=1))
    model = ScriptModel(responses=[call("execute_web_rule", {"raw_rule": RULE})])
    with pytest.raises(JobGuardError, match="model_budget_exhausted"):
        await run_rule_agent(context, model, preflight={})
    assert context.store.tool_calls == ["execute_web_rule"]
    assert context.store.current_receipt == "valid-receipt"
    assert context.store.activated is None
    assert request_progress(model.requests[0])["model_calls_after_current_response"] == 0
