"""Source-bound authoring tools and the host-verified final output protocol."""

from __future__ import annotations

from typing import Annotated, Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema

from app.ingestion.web_crawl_types import WebRule

from .context import RuleAgentContext

# Reuse the authoritative rule's provider schema while retaining the original
# dictionary for validation feedback, including malformed nested field values.
RuleCandidate = Annotated[
    dict, WithJsonSchema(convert_to_openai_tool(WebRule)["function"]["parameters"])
]


class FinalWebRule(BaseModel):
    """Submit the exact executed rule after assessing its actual items against the DOM.

    This is a candidate handoff. Reader checks the persisted rule, receipt,
    source revision, lease and first-window execution before activation.
    Explain historical scope and unresolved limitations; execution is not quality approval.
    """

    model_config = ConfigDict(extra="forbid")

    rule: WebRule
    execution_id: str = Field(min_length=1, max_length=128)
    assessment: str = Field(min_length=1, max_length=2000)
    limitations: list[Annotated[str, Field(max_length=1000)]] = Field(max_length=8)


class RuleAuthoringFailure(BaseModel):
    """End without activation, citing actual observations or execution evidence.

    missing_evidence: necessary observations could not be obtained.
    unsupported_schema: observed behavior cannot be expressed by the native schema.
    no_usable_rule: observations or trials do not support a usable article-list rule.
    """

    model_config = ConfigDict(extra="forbid")
    reason: Literal["missing_evidence", "unsupported_schema", "no_usable_rule"]
    message: str = Field(min_length=20, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


@tool
async def web_rule_schema(runtime: ToolRuntime[RuleAgentContext]) -> dict:
    """Read the native rule schema before execution and assessed FinalWebRule submission."""
    return await runtime.context.schema()


@tool
async def inspect_web_page(argv: list[str], runtime: ToolRuntime[RuleAgentContext]) -> dict:
    """Run ONE agent-browser 0.37.1 command in this task's persistent Lightpanda session.

    argv is an array of strings (not shell text or a batch), at most 16 strings and
    12000 characters. Commands: open, snapshot, get, eval, click, wait, scroll,
    find, back, forward. Host controls session, config, CDP, executable and flags.
    Examples: ["open", "https://example.com/news"],
    ["snapshot", "-u", "-s", "main", "-d", "4"], ["get", "html", "article"],
    ["get", "text", "h1"], ["eval", "document.querySelector('main').textContent"],
    ["click", "@e12"], ["wait", "--url", "**/page/2"], ["scroll", "down", "600"],
    ["find", "role", "link", "click", "--name", "Next"], ["back"], ["forward"].
    get supports text/html/attr/value/title/url/count/box/styles using native CLI syntax.
    Open only the source or same-origin URLs visible in delivered snapshot -u/HTML.
    output is native text/snapshot/HTML or serialized JSON (null is "null").
    Reader bounds the final JSON to 20KiB; truncated/truncated_fields mark Reader
    clipping, not page completeness. If a complete URL cannot fit, url is null and
    truncated_fields includes url; that report grants no navigation permission.
    Native warnings remain. Narrow snapshot with
    -s/-d, use a specific get selector, or eval a small DOM query if truncated.
    Errors retain source/message/exit_code; correct argv or selectors and retry in
    the same session. Repeated results still include content. Live @refs belong to
    the current page; saved evidence cannot restore them after navigation/restart.
    """
    return await runtime.context.inspect(argv=argv)


@tool
async def read_rule_evidence(
    evidence_id: str,
    runtime: ToolRuntime[RuleAgentContext],
    item_start: int | None = None,
    item_limit: int = 5,
) -> dict:
    """Read cached DOM evidence or complete executed items (up to 15 per call).

    Omit item_start to continue from the saved unread item. next_item_start=null means
    all stored items were delivered. Explicit item_start allows deliberate rereading.
    DOM evidence IDs expire across attempts; stored execution facts remain historical.
    """
    return await runtime.context.read_evidence(
        evidence_id, item_start=item_start, item_limit=item_limit
    )


@tool
async def record_rule_findings(
    topic: Literal["listing", "fields", "continuation", "unresolved"],
    finding: str,
    evidence_ids: list[str],
    runtime: ToolRuntime[RuleAgentContext],
) -> dict:
    """Save a short reusable finding with real evidence IDs before old messages are compacted.

    This is an untrusted working note, never a validation or navigation permission.
    """
    return await runtime.context.record_findings(topic, finding, evidence_ids)


@tool
async def execute_web_rule(raw_rule: RuleCandidate, runtime: ToolRuntime[RuleAgentContext]) -> dict:
    """Execute the real Reader scanner and return items, continuation facts and errors.

    completed means the bounded run completed; it does not judge relevance or completeness.
    partial retains successful windows after a later failure. Inspect or revise as needed,
    then use execution_id with the identical rule in FinalWebRule. A failed first window
    cannot activate. Historical uncertainty alone does not prevent submission.
    """
    return await runtime.context.execute(raw_rule)


RULE_TOOLS = [
    web_rule_schema,
    inspect_web_page,
    read_rule_evidence,
    record_rule_findings,
    execute_web_rule,
]
