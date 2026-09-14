"""Actual LangChain graph with Reader-owned authority, budgets and completion."""

from __future__ import annotations

import asyncio
import json
import sys

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    hook_config,
)
from langchain.agents.structured_output import OutputToolBinding, ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from .context import RuleAgentContext
from .errors import AgentFailure, model_failure
from .memory import bounded_messages
from .prompt import SYSTEM_PROMPT, goal_feedback, initial_message
from .tools import RULE_TOOLS, FinalWebRule, RuleAuthoringFailure


class DurableGuardMiddleware(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):
        context: RuleAgentContext = request.runtime.context
        message = next(
            message
            for message in reversed(request.state["messages"])
            if isinstance(message, AIMessage)
        )
        # LangChain handles FinalWebRule internally, outside ToolNode. Including
        # its ID here would make a later real tool wait forever for index zero.
        batch = tuple(
            call["id"]
            for call in message.tool_calls
            if call["name"] not in {FinalWebRule.__name__, RuleAuthoringFailure.__name__}
        )
        if len(set(batch)) != len(batch):
            raise AgentFailure("invalid_tool_batch", "Model emitted duplicate tool call IDs.")
        index = batch.index(request.tool_call["id"])
        # Async ToolNode middleware can arrive out of list order. Honor the
        # model's call order, including arguments/limits, and wake every waiter
        # on success, handoff, cancellation or failure.
        async with context.dispatch_condition:
            if context.dispatch_message_id != id(message) or context.dispatch_batch != batch:
                context.dispatch_message_id = id(message)
                context.dispatch_batch, context.dispatch_next = batch, 0
            await context.dispatch_condition.wait_for(
                lambda: (
                    index == context.dispatch_next
                    or context.handoff
                    or context.dispatch_error is not None
                )
            )
            if context.dispatch_error is not None:
                raise context.dispatch_error
            if context.handoff is not None:
                return ToolMessage(
                    content=json.dumps(context.handoff),
                    tool_call_id=request.tool_call["id"],
                )
            try:
                await context.check()
                if rejection := context.tool_rejection(
                    request.tool_call["name"], request.tool_call.get("args", {})
                ):
                    await context.store.reserve_tool(context.claim, request.tool_call["name"])
                    return ToolMessage(
                        content=json.dumps(rejection), tool_call_id=request.tool_call["id"]
                    )
                return await handler(request)
            except BaseException as exc:
                context.dispatch_error = exc
                raise
            finally:
                context.dispatch_next += 1
                context.dispatch_condition.notify_all()

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        context: RuleAgentContext = runtime.context
        # An accepted submission needs no final model turn, including while a
        # scanner is busy. Only the injected context can set this handoff.
        if context.handoff is not None:
            return {"jump_to": "end"}
        await context.check()
        return None

    async def awrap_model_call(self, request, handler):
        context: RuleAgentContext = request.runtime.context
        await context.check()
        working = {
            **context.memory.snapshot(),
            "candidate": context.latest_candidate,
            "execution": context.latest_validation,
            "execution_id": context.receipt,
        }
        request = request.override(
            messages=bounded_messages(
                request.messages, task=context.task_message, working_state=working
            )
        )
        progress = await context.refresh_progress(_request_bytes(request))
        request = request.override(
            tools=[tool for tool in request.tools if tool.name in context.allowed_tools()],
            response_format=ToolStrategy(
                FinalWebRule | RuleAuthoringFailure,
                tool_message_content="Structured output received; host checks acceptance.",
            ),
            messages=bounded_messages(
                request.state["messages"],
                task=context.task_message,
                working_state={**working, "progress": progress},
            ),
        )
        size = _request_bytes(request)
        # This estimate is only the unknown-usage charge if the request loses
        # its response. The durable store gates new calls on recorded usage,
        # never on this UTF-8 byte estimate of the next request.
        reservation = await context.store.reserve_model(
            context.claim, size + context.settings.web_rule_agent_max_output_tokens + 1024
        )
        usage = None
        try:
            async with asyncio.timeout(context.settings.web_rule_agent_model_timeout_seconds):
                response = await handler(request)
            usage = _response_usage(response)
            if usage is not None:
                context.recent_input_tokens = usage.get("input_tokens", 0)
            # Check before LangGraph dispatches any real tools. Structured-output
            # tools already have synthetic replies at this point; a shared ID
            # could otherwise hide a real call and strand our ordered dispatcher.
            for message in response.result:
                if isinstance(message, AIMessage):
                    call_ids = [call["id"] for call in message.tool_calls]
                    if len(set(call_ids)) != len(call_ids):
                        raise AgentFailure(
                            "invalid_tool_batch", "Model emitted duplicate tool call IDs."
                        )
            return response
        except asyncio.CancelledError:
            raise
        except AgentFailure:
            raise
        except Exception as exc:
            raise model_failure(exc) from exc
        finally:
            # A timeout/cancellation is unknown consumption, never a free call.
            # The durable reservation itself also survives process termination.
            await context.store.record_usage(context.claim, reservation, usage)


def _request_bytes(request) -> int:
    messages = [message.model_dump(mode="json") for message in request.messages]
    if request.system_message is not None:
        messages.insert(0, request.system_message.model_dump(mode="json"))
    tools = [convert_to_openai_tool(item) for item in request.tools]
    if isinstance(request.response_format, ToolStrategy):
        # create_agent binds these in addition to request.tools, after middleware.
        # Count the same tool schema that will actually be sent to the provider.
        names = {item["function"]["name"] for item in tools if item.get("type") == "function"}
        for schema in request.response_format.schema_specs:
            structured_tool = OutputToolBinding.from_schema_spec(schema).tool
            if structured_tool.name not in names:
                tools.append(convert_to_openai_tool(structured_tool))
    return len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False).encode())


def _response_usage(response) -> dict | None:
    messages = [message for message in response.result if isinstance(message, AIMessage)]
    if not messages or any(message.usage_metadata is None for message in messages):
        return None
    usage: dict = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for message in messages:
        metadata = message.usage_metadata or {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            count = metadata.get(key)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                return None
            usage[key] += count
        for key in ("input_token_details", "output_token_details"):
            details = metadata.get(key)
            if isinstance(details, dict):
                target = usage.setdefault(key, {})
                for name, count in details.items():
                    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                        target[str(name)[:80]] = target.get(str(name)[:80], 0) + count
    # Nonempty model requests cannot consume zero tokens. Some compatible
    # providers return a zero-filled placeholder; charge it as unknown usage.
    return (
        usage if usage["total_tokens"] or usage["input_tokens"] or usage["output_tokens"] else None
    )


async def run_rule_agent(context: RuleAgentContext, model, *, preflight: dict) -> dict:
    """Run and, when needed, resume one graph under the same durable task budget."""
    agent = create_agent(
        model=model,
        tools=RULE_TOOLS,
        context_schema=RuleAgentContext,
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(
            FinalWebRule | RuleAuthoringFailure,
            tool_message_content=(
                "Structured completion received. Reader checks the FinalWebRule rule and "
                "receipt, or the RuleAuthoringFailure evidence, before acceptance."
            ),
        ),
        middleware=[DurableGuardMiddleware()],
    )
    try:
        if not context.memory.evidence:
            report = context.bound(preflight, "preflight")
            ref, _ = context.memory.remember(
                report, view={"url": context.claim.source_url, "mode": "preflight"}
            )
            preflight = {**report, "evidence_id": ref}
        context.task_message = initial_message(context, preflight)
        state = {"messages": [HumanMessage(content=context.task_message)]}
        while True:
            # No checkpointer is attached. Carry only message history;
            # never reuse an earlier structured_response as new completion.
            state = await agent.ainvoke(
                state,
                context=context,
                config={"recursion_limit": sys.maxsize},
            )
            final_rule = state.get("structured_response")
            if isinstance(final_rule, FinalWebRule):
                feedback = await context.submit(
                    final_rule.execution_id,
                    raw_rule=final_rule.rule.model_dump(mode="json"),
                    assessment=final_rule.assessment,
                    limitations=final_rule.limitations,
                )
                if context.handoff is not None:
                    return context.handoff
            elif isinstance(final_rule, RuleAuthoringFailure):
                if all(context.memory.knows(ref) for ref in final_rule.evidence_ids):
                    await context.check()
                    await context.store.diagnostic(
                        context.claim,
                        phase="finish",
                        code=final_rule.reason,
                        evidence=final_rule.model_dump(),
                    )
                    raise AgentFailure("web_rule_" + final_rule.reason, final_rule.message)
                else:
                    feedback = context.bound(
                        {
                            "status": "fail",
                            "code": "failure_evidence_required",
                            "next_action": "reference_real_evidence_or_inspect",
                        },
                        "finish",
                    )
            else:
                feedback = context.bound(
                    {
                        "status": "fail",
                        "code": "structured_completion_required",
                        "message": (
                            "Return FinalWebRule or an evidence-backed RuleAuthoringFailure."
                        ),
                        "next_action": "assess_evidence_then_submit_or_report_failure",
                    },
                    "finish",
                )
            # This short transaction checks authority and records the
            # continuation before another invocation.
            # Provider, permission and budget exceptions never reach here.
            resumptions = await context.store.resume_goal(context.claim, feedback)
            state = {
                "messages": [
                    *state["messages"],
                    HumanMessage(content=goal_feedback(feedback, resumptions)),
                ],
            }
    except Exception as exc:
        if type(exc).__name__ == "GraphRecursionError":
            raise AgentFailure("agent_graph_error", "Rule graph could not continue.") from exc
        raise
