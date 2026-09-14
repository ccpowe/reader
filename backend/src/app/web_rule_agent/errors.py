"""Public failure categories, without persisting raw provider responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


@dataclass
class AgentFailure(RuntimeError):
    code: str
    message: str
    status: str = "failed"
    retry_after_seconds: float = 0
    pause_engine: bool = False

    def __str__(self) -> str:
        return self.message


STRUCTURAL_CODES = {
    "web_structure_changed",
    "web_rule_missing_fields",
    "web_rule_unstable",
    "web_rule_required",
    "WebRuleError",
    "ValidationError",
    "ValueError",
    "cursor_invalid",
    "web_rule_action_failed",
}


def page_failure(report: dict, *, phase: str) -> AgentFailure | None:
    """Quality feedback can reach the model; inaccessible pages cannot be repaired by it."""
    if report.get("status") == "pass":
        return None
    code = str(report.get("code") or "web_page_unavailable")
    if code == "web_rule_action_failed":
        return None
    if phase == "validate" and code in STRUCTURAL_CODES:
        return None
    transient = (
        bool(report.get("retryable"))
        or code
        in {
            "TimeoutError",
            "ReadTimeout",
            "ConnectTimeout",
            "ConnectError",
            "ReadError",
            "RemoteProtocolError",
            "PoolTimeout",
            "web_timeout",
            "web_crawl_timeout",
        }
        or code in {"web_http_408", "web_http_429"}
        or code.startswith("web_http_5")
    )
    return AgentFailure(
        code=code,
        message=f"Page {phase} could not complete ({code}).",
        status="retry_wait" if transient else "failed",
        retry_after_seconds=30 if transient else 0,
    )


def model_failure(exc: Exception) -> AgentFailure:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in {400, 401, 402, 403, 404, 422} or isinstance(exc, NotImplementedError):
        return AgentFailure(
            code=f"model_http_{status}" if status else "model_tools_unsupported",
            message="Rule model configuration, credentials or tool support require attention.",
            status="blocked",
            pause_engine=True,
        )
    if (
        status in {408, 429}
        or (isinstance(status, int) and status >= 500)
        or (
            isinstance(exc, TimeoutError)
            or type(exc).__name__ in {"APITimeoutError", "APIConnectionError", "ConnectError"}
        )
    ):
        return AgentFailure(
            code=f"model_http_{status}" if status else "model_request_interrupted",
            message="Rule model request failed temporarily; its reserved budget is retained.",
            status="retry_wait",
            retry_after_seconds=_retry_after(exc),
        )
    return AgentFailure("model_execution_failed", "Rule model execution failed.")


def _retry_after(exc: Exception) -> float:
    headers = getattr(getattr(exc, "response", None), "headers", {})
    value = headers.get("retry-after") if headers else None
    if value:
        try:
            return max(1.0, float(value))
        except (TypeError, ValueError):
            try:
                when = parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                return max(1.0, (when - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return 30.0
