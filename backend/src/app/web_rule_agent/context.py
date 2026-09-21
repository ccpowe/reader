"""Per-attempt authority and serialized calls to the existing Python tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.core.settings import Settings
from app.ingestion import web_rules
from app.ingestion.models import SourceScanError
from app.services.web_rule_jobs import JobGuardError

from .cli_browser import MAX_RESULT_BYTES, finalize_cli_report, visible_links
from .errors import AgentFailure, page_failure
from .memory import AuthoringMemory, json_bytes


def compact(value: dict, maximum_bytes: int) -> dict:
    """Preserve the envelope and valid JSON while bounding untrusted evidence."""
    result = json.loads(json.dumps(value, ensure_ascii=False, default=str))

    def size():
        return len(json.dumps(result, ensure_ascii=False).encode("utf-8"))

    if size() <= maximum_bytes:
        return result
    result["truncated"] = True
    for key in ("html", "links", "items", "first", "replay", "evidence", "schema"):
        item = result.get(key)
        if isinstance(item, str):
            while len(item) > 256 and size() > maximum_bytes:
                item = item[: len(item) // 2]
                result[key] = item
        elif isinstance(item, list):
            while len(item) > 1 and size() > maximum_bytes:
                item = item[: max(1, len(item) // 2)]
                result[key] = item
        elif isinstance(item, dict) and size() > maximum_bytes:
            result[key] = {"truncated": True}
        if size() <= maximum_bytes:
            return result
    # Unknown future provider fields must not evade the same output bound.
    result = {
        key: result[key]
        for key in (
            "status",
            "code",
            "phase",
            "retryable",
            "next_action",
            "validation_id",
            "evidence_id",
            "page_revision",
            "next_node_start",
            "total_nodes",
        )
        if key in result
    }
    result["truncated"] = True
    result["message"] = "Evidence exceeded the tool limit; inspect a narrower selector or slice."
    return result


def envelope(report: dict, phase: str) -> dict:
    return {
        "status": report.get("status", "fail"),
        "code": report.get("code", "ok" if report.get("status") == "pass" else "invalid_rule"),
        "retryable": report.get("retryable", False),
        "phase": phase,
        "next_action": "continue" if report.get("status") == "pass" else "inspect_feedback",
        **report,
    }


@dataclass
class RuleAgentContext:
    claim: Any
    store: Any
    settings: Settings
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dispatch_condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    dispatch_batch: tuple[str, ...] = ()
    dispatch_message_id: int | None = None
    dispatch_next: int = 0
    dispatch_error: BaseException | None = None
    observed_pages: dict[str, str] = field(default_factory=dict)
    handoff: dict | None = None
    latest_candidate: dict | None = None
    lost_lease: bool = False
    heartbeat_callback: Any = None
    memory: AuthoringMemory = field(default_factory=AuthoringMemory)
    browser: Any = None
    latest_validation: dict | None = None
    receipt: str | None = None
    budget: dict = field(default_factory=dict)
    stage: str = "explore"
    recent_input_tokens: int = 0
    task_message: str = ""

    def __post_init__(self):
        self.memory.restore(getattr(self.claim, "authoring_checkpoint", None))
        self.latest_candidate = getattr(self.claim, "candidate_rule", None) or getattr(
            self.claim, "base_rule", None
        )
        previous = getattr(self.claim, "last_feedback", None) or {}
        if previous.get("execution_id"):
            self.memory.remember_execution(previous)
            self.latest_validation = web_rules.execution_summary(previous)
        self.assessment = previous.get("assessment", "")
        self.limitations = previous.get("limitations", [])

    async def check(self) -> None:
        if self.lost_lease:
            raise JobGuardError("lease_lost")
        await self.store.check(self.claim)

    def bound(self, report: dict, phase: str) -> dict:
        if "format" in report and "output" in report:
            # CLI deliveries already carry their final byte bound and metadata.
            return report
        report = envelope(report, phase)
        if "nodes" in report:
            from .browser import bound_inspection_report

            return bound_inspection_report(
                report, self.settings.web_rule_agent_max_tool_result_bytes
            )
        return compact(
            report,
            self.settings.web_rule_agent_max_tool_result_bytes,
        )

    async def close(self) -> None:
        if self.browser is not None:
            if self.settings.browser_controller_url is None:
                await self.browser.aclose()
            else:
                from app.browser_tasks.protocol import CloseReason

                await self.browser.aclose(
                    reason=CloseReason.LEASE_LOST if self.lost_lease else CloseReason.NORMAL
                )

    def get_browser(self):
        if self.browser is None:
            if self.settings.browser_controller_url is None:
                from .cli_browser import CLIBrowserSession

                self.browser = CLIBrowserSession(
                    self.settings,
                    self.claim.source_url,
                    cli_executable=self.settings.web_rule_agent_cli_executable_path,
                    cli_identity=getattr(self.claim, "engine_snapshot", {}).get(
                        "cli_execution_identity"
                    ),
                    browser_identity=getattr(self.claim, "engine_snapshot", {}).get(
                        "browser_execution_identity"
                    ),
                )
            else:
                from app.browser_tasks.client import RemoteCLIBrowserSession

                self.browser = RemoteCLIBrowserSession(
                    self.settings,
                    self.claim.source_url,
                    owner_job_id=str(self.claim.job_id),
                    expected_runtime_identity=getattr(self.claim, "engine_snapshot", {}).get(
                        "browser_execution_identity"
                    ),
                )
        return self.browser

    async def refresh_progress(self, request_bytes: int) -> dict:
        self.budget = await self.store.progress(self.claim)
        estimate = max(self.recent_input_tokens, (request_bytes + 2) // 3)
        estimate += self.settings.web_rule_agent_max_output_tokens
        return {
            **self.budget,
            "model_calls_remaining_includes_current_response": True,
            "model_calls_after_current_response": max(0, self.budget["model_calls_remaining"] - 1),
            "tokens_remaining_basis": "recorded_ledger_usage_before_current_response",
            "estimated_tokens_per_remaining_call": estimate,
            "reserve_for_assessment_and_final": True,
            "estimate_is_not_a_spending_limit": True,
        }

    def allowed_tools(self) -> set[str]:
        return {
            "web_rule_schema",
            "inspect_web_page",
            "read_rule_evidence",
            "record_rule_findings",
            "execute_web_rule",
        }

    def tool_rejection(self, name: str, args: dict) -> dict | None:
        if name not in self.allowed_tools():
            return self.bound({"status": "fail", "code": "unknown_tool"}, "dispatch")
        return None

    def _execution_slice(self, report: dict, start: int, limit: int) -> dict:
        items = report.get("items", [])
        result = {
            **web_rules.execution_summary(report),
            "items": items[start : start + limit],
            "evidence_id": report.get("execution_id"),
            "item_start": start,
            "stored_item_count": len(items),
        }
        maximum = self.settings.web_rule_agent_max_tool_result_bytes
        while True:
            end = start + len(result["items"])
            result["next_item_start"] = end if end < len(items) else None
            if json_bytes(result) <= maximum:
                break
            if result["items"]:
                result["items"].pop()
                continue
            result = {
                "status": report["status"],
                "execution_id": report.get("execution_id"),
                "evidence_id": report.get("execution_id"),
                "item_start": start,
                "stored_item_count": len(items),
                "items": [],
                "next_item_start": start if start < len(items) else None,
                "code": "execution_item_output_too_large",
                "message": "One complete item or its facts exceed the output limit.",
            }
            break
        if start < len(items) and not result["items"]:
            result["code"] = "execution_item_output_too_large"
            result["message"] = (
                "No complete item fits; narrow the evidence or assess already observed DOM."
            )
        if result["items"] or start == len(items):
            self.memory.read_progress[report["execution_id"]] = result["next_item_start"]
        return result

    async def read_evidence(
        self, evidence_id: str, *, item_start: int | None = None, item_limit: int = 5
    ) -> dict:
        async with self.lock:
            await self.check()
            await self.store.reserve_tool(self.claim, "read_rule_evidence")
            report = self.memory.read(evidence_id)
            if report.get("execution_id"):
                if item_start is None:
                    item_start = self.memory.read_progress.get(evidence_id, 0)
                    if item_start is None:
                        item_start = len(report.get("items", []))
                if (
                    type(item_start) is not int
                    or type(item_limit) is not int
                    or not 0 <= item_start <= len(report.get("items", []))
                    or not 1 <= item_limit <= 15
                ):
                    return self.bound(
                        {"status": "fail", "code": "invalid_evidence_slice"}, "evidence"
                    )
                result = self._execution_slice(report, item_start, item_limit)
                await self.store.checkpoint(self.claim, self.memory.checkpoint())
                return result
            if item_start not in (None, 0):
                return self.bound({"status": "fail", "code": "invalid_evidence_slice"}, "evidence")
            if "format" in report and "output" in report:
                return self._bound_cli(report)
            return self.bound(report, "evidence")

    def preflight_feedback(self, report: dict) -> dict:
        """Keep old-rule diagnostics separate from the candidate's executed version."""
        facts = {key: value for key, value in report.items() if key != "execution_id"}
        facts["preflight_execution_id"] = report.get("execution_id")
        feedback = self.bound(facts, "preflight")
        evidence_id, _ = self.memory.remember(
            feedback, view={"url": "preflight:" + self.claim.source_url}
        )
        # The worker needs actual completion facts, not the bounded model view.
        return {**facts, "evidence_id": evidence_id}

    def validation_feedback(self, report: dict) -> dict:
        # Execution is historical evidence. It grants no new browser page refs.
        report = web_rules.bounded_execution_report(report)
        self.memory.remember_execution(report)
        self.latest_validation = web_rules.execution_summary(report)
        if report.get("execution_id"):
            return self._execution_slice(report, 0, 5)
        return self.bound(report, "execute")

    async def record_findings(self, topic: str, finding: str, evidence_ids: list[str]) -> dict:
        async with self.lock:
            await self.check()
            await self.store.reserve_tool(self.claim, "record_rule_findings")
            result = self.memory.record(topic, finding, evidence_ids)
            await self.store.checkpoint(self.claim, self.memory.checkpoint())
            return self.bound(result, "remember")

    @property
    def cli_result_limit(self) -> int:
        return min(MAX_RESULT_BYTES, self.settings.web_rule_agent_max_tool_result_bytes)

    def _bound_cli(self, report: dict, *, metadata: dict | None = None) -> dict:
        try:
            return finalize_cli_report(
                report, metadata=metadata, maximum_bytes=self.cli_result_limit
            )
        except ValueError:
            # A complete URL can exceed a valid small tool limit on its own.
            # Retry from the original payload; never expose a URL prefix or
            # derive navigation authority from a report without its actual URL.
            omitted = {
                **report,
                "url": None,
                "truncated": True,
                "truncated_fields": list(
                    dict.fromkeys([*report.get("truncated_fields", []), "url"])
                ),
            }
            return finalize_cli_report(
                omitted, metadata=metadata, maximum_bytes=self.cli_result_limit
            )

    def _remember_links(self, report: dict) -> None:
        # Parse only the bytes actually delivered, never the unbounded DOM.
        source = urlsplit(self.claim.source_url)
        for target in visible_links(report):
            parsed = urlsplit(target)
            if (
                (parsed.scheme, parsed.netloc.lower()) == (source.scheme, source.netloc.lower())
                and parsed.username is None
                and parsed.password is None
                and len(self.observed_pages) < 200
            ):
                self.observed_pages[target] = target

    async def schema(self) -> dict:
        async with self.lock:
            if self.handoff:
                return self.handoff
            await self.check()
            await self.store.reserve_tool(self.claim, "web_rule_schema")
            return self.bound({"status": "pass", "schema": web_rules.web_rule_schema()}, "schema")

    async def inspect(self, *, argv: list[str]) -> dict:
        async with self.lock:
            if self.handoff:
                return self.handoff
            await self.check()
            await self.store.reserve_tool(self.claim, "inspect_web_page")
            try:
                async with asyncio.timeout(self.settings.web_rule_agent_inspect_timeout_seconds):
                    report = await self.get_browser().command(
                        argv, observed_urls=self.observed_pages.values()
                    )
            except SourceScanError as exc:
                # Missing binaries/changed identity and transport failures are
                # task failures. Native command/JS errors below remain tool data.
                failure = page_failure({"status": "fail", "code": exc.code}, phase="inspect")
                assert failure is not None
                raise failure from exc
            except TimeoutError as exc:
                raise AgentFailure(
                    "web_inspection_timeout",
                    "Page inspection timed out.",
                    status="retry_wait",
                    retry_after_seconds=30,
                ) from exc
            evidence_id, repeated = self.memory.remember(
                report, view={"url": self.claim.source_url, "argv": argv}, defer_details=True
            )
            result = self._bound_cli(
                report,
                metadata={"evidence_id": evidence_id, "repeated": repeated, "phase": "inspect"},
            )
            self._remember_links(result)
            self.memory.delivered(evidence_id, result)
            await self.store.checkpoint(self.claim, self.memory.checkpoint())
            encoded = json.dumps(result, ensure_ascii=False).encode()
            trace = {
                key: result.get(key)
                for key in (
                    "status",
                    "url",
                    "format",
                    "truncated",
                    "page_revision",
                    "evidence_id",
                    "repeated",
                )
            }
            trace.update(
                returned_bytes=len(encoded), evidence_sha256=hashlib.sha256(encoded).hexdigest()
            )
            await self.store.diagnostic(
                self.claim,
                phase="inspect",
                code=str((report.get("error") or {}).get("code") or report["status"]),
                evidence=trace,
            )
            return result

    async def execute(self, raw_rule: dict) -> dict:
        async with self.lock:
            if self.handoff:
                return self.handoff
            await self.check()
            await self.store.reserve_tool(self.claim, "execute_web_rule", validation=True)
            report = await web_rules.execute_web_rule(
                source_url=self.claim.source_url,
                raw_rule=raw_rule,
                timeout_seconds=self.settings.web_rule_agent_validate_timeout_seconds,
                browser_identity=getattr(self.claim, "engine_snapshot", {}).get(
                    "browser_execution_identity"
                ),
            )
            saved = await self.store.save_validation(self.claim, raw_rule, report)
            self.latest_candidate = raw_rule
            result = self.validation_feedback(saved)
            self.receipt = (
                saved.get("execution_id") if saved.get("first_window_completed") else None
            )
            await self.store.checkpoint(self.claim, self.memory.checkpoint())
            return result

    async def validate(self, raw_rule: dict) -> dict:
        """Internal compatibility alias; model tools advertise execute_web_rule only."""
        return await self.execute(raw_rule)

    async def submit(
        self,
        validation_id: str,
        *,
        raw_rule: dict | None = None,
        assessment: str | None = None,
        limitations: list[str] | None = None,
    ) -> dict:
        """Host-only activation; model output cannot bypass the persisted receipt check."""
        async with self.lock:
            if self.handoff:
                return self.handoff
            await self.check()
            await self.store.reserve_tool(self.claim, "submit_web_rule")
            try:
                result = await self.store.activate(
                    self.claim,
                    validation_id,
                    raw_rule=raw_rule,
                    assessment=self.assessment if assessment is None else assessment,
                    limitations=self.limitations if limitations is None else limitations,
                )
            except JobGuardError as exc:
                if exc.code not in {
                    "validation_expired",
                    "final_rule_mismatch",
                    "execution_not_usable",
                }:
                    raise
                self.receipt = None
                self.stage = "validate"
                return self.bound(
                    {
                        "status": "fail",
                        "code": exc.code,
                        "next_action": (
                            "execute_web_rule_again"
                            if exc.code == "validation_expired"
                            else "return_exact_executed_rule_or_execute_the_changed_rule"
                        ),
                    },
                    "activate",
                )
            if result.get("status") in {"succeeded", "activation_pending"}:
                if assessment is not None:
                    self.assessment = assessment
                if limitations is not None:
                    self.limitations = limitations
                # This is runtime authority, never a model-settable state key.
                self.handoff = {**result, "validation_id": validation_id}
            return self.bound(result, "activate")
