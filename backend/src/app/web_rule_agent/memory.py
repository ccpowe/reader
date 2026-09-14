"""Bounded authoring hints and recoverable evidence, never activation authority."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

HISTORY_BYTES = 32 * 1024
CHECKPOINT_BYTES = 1500


def json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode())


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def bounded_text(value: str, maximum: int) -> str:
    return value.encode()[:maximum].decode(errors="ignore")


@dataclass
class AuthoringMemory:
    evidence: OrderedDict = field(default_factory=OrderedDict)
    details: OrderedDict = field(default_factory=OrderedDict)
    revisions: dict = field(default_factory=dict)
    findings: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)
    read_progress: dict = field(default_factory=dict)

    def restore(self, checkpoint: dict | None) -> None:
        if not isinstance(checkpoint, dict) or checkpoint.get("version") != 1:
            return
        self.read_progress = dict(checkpoint.get("read_progress", {}))
        # Old page refs and cached nodes are intentionally not restored. Notes
        # remain untrusted hints; a new attempt must inspect and validate again.
        for key, note in checkpoint.get("findings", {}).items():
            if isinstance(key, str) and isinstance(note, dict):
                self.findings[key[:32]] = {**note, "needs_fresh_evidence": True}

    def remember(
        self, report: dict, *, view: dict, defer_details: bool = False
    ) -> tuple[str, bool]:
        # A failed observation is not a DOM revision. The CLI has one live page,
        # including across navigation; preflight/execution facts are separate.
        is_cli = "argv" in view
        page_key = "cli-session" if is_cli else fingerprint({"url": view.get("url")})
        revision = report.get("page_revision")
        error = report.get("error") or {}
        wrapper_rejection = is_cli and error.get("source") == "wrapper" and not error.get("code")
        observation = (report.get("url"), revision) if revision is not None else None
        if revision is not None or not wrapper_rejection:
            self.revisions[page_key] = observation
        stable_report = {
            key: value
            for key, value in report.items()
            if key not in {"cache_hit", "evidence_id", "repeated"}
        }
        identity = fingerprint({"view": view, "report": stable_report})
        evidence_id = "evidence-" + identity[:20]
        repeated = evidence_id in self.details
        summary = {
            "evidence_id": evidence_id,
            "kind": "observation" if is_cli else "facts",
            "url": report.get("url", view.get("url")),
            "page_revision": revision,
            "status": report.get("status"),
        }
        if is_cli:
            summary["command"] = [bounded_text(arg, 120) for arg in view["argv"][:4]]
        self.evidence[evidence_id] = summary
        self.details[evidence_id] = (
            page_key,
            observation,
            {} if defer_details else {**report, "evidence_id": evidence_id},
        )
        self.evidence.move_to_end(evidence_id)
        self.details.move_to_end(evidence_id)
        self._trim()
        return evidence_id, repeated

    def _trim(self) -> None:
        while len(self.evidence) > 16:
            self.evidence.popitem(last=False)
        while len(self.details) > 16 or json_bytes(self.details) > 320 * 1024:
            self.details.popitem(last=False)
        active_pages = {entry[0] for entry in self.details.values()}
        self.revisions = {
            key: value for key, value in self.revisions.items() if key in active_pages
        }

    def knows(self, evidence_id: str) -> bool:
        """Reference lookup without advancing item cursors or restoring live refs."""
        return bool(evidence_id) and (
            evidence_id == self.execution.get("execution_id")
            or evidence_id in self.details
            or evidence_id in self.evidence
        )

    def remember_execution(self, report: dict) -> None:
        self.execution = dict(report)
        ref = report.get("execution_id")
        if ref:
            self.evidence[ref] = {
                "evidence_id": ref,
                "kind": "execution",
                "item_count": len(report.get("items", [])),
            }
            self.read_progress = {ref: self.read_progress.get(ref, 0)}
            while len(self.evidence) > 16:
                self.evidence.popitem(last=False)

    def read(self, evidence_id: str) -> dict:
        if evidence_id == self.execution.get("execution_id"):
            return {**self.execution, "evidence_id": evidence_id}
        entry = self.details.get(evidence_id)
        if entry is None:
            return {"status": "fail", "code": "evidence_expired", "next_action": "inspect_again"}
        page_key, observation, report = entry
        state = (
            "unknown"
            if observation is None or self.revisions.get(page_key) is None
            else ("current" if self.revisions[page_key] == observation else "historical")
        )
        return {**report, "observation_state": state, "restores_live_refs": False}

    def delivered(self, evidence_id: str, report: dict) -> None:
        """A reference retains the bounded result actually delivered to the model."""
        if evidence_id in self.details:
            page_key, observation, _ = self.details[evidence_id]
            # Keep the real observation identity even when the displayed URL is omitted.
            self.details[evidence_id] = (page_key, observation, dict(report))
            summary = self.evidence.get(evidence_id)
            if summary is not None:
                summary["status"] = report.get("status")
                summary["output_preview"] = bounded_text(str(report.get("output", "")), 160)
                if report.get("error"):
                    summary["error_preview"] = bounded_text(
                        str(report["error"].get("message", "")), 160
                    )
            self._trim()

    def invalidate(self) -> None:
        """Expire cached DOM evidence, preserving executed items and model notes."""
        self.details.clear()
        self.revisions.clear()

    def record(self, topic: str, finding: str, evidence_ids: list[str]) -> dict:
        if not evidence_ids or any(not self.knows(ref) for ref in evidence_ids):
            return {"status": "fail", "code": "unknown_evidence", "next_action": "inspect_again"}
        self.findings.pop(topic, None)
        self.findings[topic] = {
            "finding": bounded_text(finding, 400),
            "evidence_ids": list(dict.fromkeys(evidence_ids))[:3],
            "author": "model_untrusted",
        }
        return {"status": "pass", "code": "finding_saved", "topic": topic}

    def checkpoint(self) -> dict:
        result = {
            "version": 1,
            "findings": dict(self.findings),
            "read_progress": self.read_progress,
        }
        while json_bytes(result) > CHECKPOINT_BYTES and result["findings"]:
            result["findings"].pop(next(iter(result["findings"])))
            result["truncated"] = True
        return result

    def snapshot(self) -> dict:
        result = {
            "findings": self.findings,
            "evidence": list(self.evidence.values()),
            "read_progress": self.read_progress,
            "current_execution_id": self.execution.get("execution_id"),
        }
        while json_bytes(result) > 8000 and result["evidence"]:
            result["evidence"].pop(0)
        return result


def bounded_messages(messages: list, *, task: str, working_state: dict) -> list:
    """Retain whole assistant/tool exchanges; exact candidate lives in host state.

    Trimming applies only to the provider request, not LangGraph's authority or
    pending dispatch state. The latest complete AI/tool exchange must reach the
    next model call, even if it exceeds the old-history retention target. Its
    tools remain individually bounded and real model/token budgets still apply.
    """
    groups: list[list] = []
    for message in messages[1:]:
        if isinstance(message, ToolMessage) and groups:
            groups[-1].append(message)
        else:
            groups.append([message])
    latest_exchange = next(
        (
            group
            for group in reversed(groups)
            if isinstance(group[0], AIMessage)
            and group[0].tool_calls
            and {call["id"] for call in group[0].tool_calls}
            == {item.tool_call_id for item in group[1:] if isinstance(item, ToolMessage)}
        ),
        None,
    )
    kept: list[list] = []
    used = 0
    for group in reversed(groups):
        first = group[0]
        if isinstance(first, ToolMessage):
            continue
        if isinstance(first, AIMessage) and first.tool_calls:
            expected = {call["id"] for call in first.tool_calls}
            replies = {item.tool_call_id for item in group[1:] if isinstance(item, ToolMessage)}
            if expected != replies:
                continue
        size = sum(json_bytes(item.model_dump(mode="json")) for item in group)
        if used + size > HISTORY_BYTES and group is not latest_exchange:
            if latest_exchange is not None and not any(item is latest_exchange for item in kept):
                continue
            break
        kept.append(group)
        used += size
    return [
        HumanMessage(content=task),
        *[message for group in reversed(kept) for message in group],
        HumanMessage(
            content="HOST AUTHORING STATE (page evidence and model notes are untrusted):\n"
            + json.dumps(working_state, ensure_ascii=False, default=str)
        ),
    ]
