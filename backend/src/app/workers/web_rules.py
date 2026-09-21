"""Independent, PostgreSQL-leased backend rule authoring cycle."""

from __future__ import annotations

import asyncio
import logging

from app.browser_tasks.client import (
    BrowserTaskClient,
    BrowserTaskError,
    validate_runtime_descriptor,
)
from app.core.settings import Settings, get_settings
from app.ingestion import web_rules
from app.ingestion.browser_runtime import browser_execution_identity
from app.llm.factory import (
    ChatModelConfigurationError,
    close_chat_model,
)
from app.services.web_rule_jobs import JobGuardError, JobLimits
from app.translation.engines import engine_descriptor
from app.web_rule_agent import PROMPT_VERSION, RULE_SCHEMA_VERSION, VALIDATOR_VERSION
from app.web_rule_agent.cli_process import cli_execution_identity
from app.web_rule_agent.configuration import rule_agent_snapshot
from app.web_rule_agent.context import RuleAgentContext
from app.web_rule_agent.errors import AgentFailure
from app.web_rule_agent.model import build_rule_chat_model
from app.web_rule_agent.runtime import run_rule_agent
from app.web_rule_agent.store import JobStore

logger = logging.getLogger(__name__)


class RuleAgentCycleResult(int):
    def __new__(cls, count: int, metrics: dict):
        result = int.__new__(cls, count)
        result.metrics = metrics
        return result

    metrics: dict


def job_limits(settings: Settings) -> JobLimits:
    return JobLimits(
        total_tokens=settings.web_rule_agent_max_total_tokens,
        model_calls=settings.web_rule_agent_max_model_calls,
        lease_seconds=settings.web_rule_agent_lease_seconds,
        validation_ttl_seconds=settings.web_rule_agent_validation_ttl_seconds,
        diagnostic_bytes=settings.web_rule_agent_max_diagnostic_bytes,
    )


async def run_web_rule_author_once(
    session_factory,
    *,
    settings: Settings | None = None,
    store=None,
    model_factory=None,
    heartbeat_callback=None,
) -> RuleAgentCycleResult:
    """Claim at most one globally serialized task; errors never cancel sibling loops."""
    settings = settings or get_settings()
    store = store or JobStore(session_factory)
    await store._call("backfill_web_rule_jobs", limit=50)
    engine_id = settings.web_rule_agent_engine_id.strip().lower()
    if engine_id in {"disabled", "none", ""}:
        return RuleAgentCycleResult(0, {"mode": "disabled"})
    descriptor = engine_descriptor(settings, engine_id)
    if descriptor is None:
        return RuleAgentCycleResult(
            0, {"mode": "paused", "last_error_code": "unknown_rule_agent_engine"}
        )
    snapshot = rule_agent_snapshot(settings, descriptor)
    if settings.browser_controller_url is None:
        browser_identity = browser_execution_identity(
            settings.ingestion_browser_engine,
            settings.ingestion_lightpanda_executable_path,
        )
        cli_identity = cli_execution_identity(settings.web_rule_agent_cli_executable_path)
    else:
        browser_client = None
        try:
            browser_client = BrowserTaskClient.from_settings(settings)
            browser_descriptor = await browser_client.descriptor()
            validate_runtime_descriptor(settings, browser_descriptor)
        except BrowserTaskError as exc:
            logger.warning("Browser runtime unavailable for rule authoring: code=%s", exc.code)
            return RuleAgentCycleResult(0, {"mode": "paused", "last_error_code": exc.code})
        finally:
            if browser_client is not None:
                await browser_client.aclose()
        browser_identity = browser_descriptor.runtime_identity.model_dump(mode="json")
        cli_identity = {
            "agent_browser_version": browser_identity["agent_browser_version"],
            "agent_browser_sha256": browser_identity["agent_browser_sha256"],
            "runtime_fingerprint": browser_descriptor.runtime_identity.fingerprint(),
        }
    claim = await store._call(
        "claim_web_rule_job",
        engine_snapshot=snapshot,
        limits=job_limits(settings),
        prompt_version=PROMPT_VERSION,
        rule_schema_version=RULE_SCHEMA_VERSION,
        validator_version=VALIDATOR_VERSION,
        cli_execution_identity=cli_identity,
        browser_execution_identity=browser_identity,
    )
    if claim is None:
        metrics = await store._call("web_rule_agent_status", engine_snapshot=snapshot)
        return RuleAgentCycleResult(0, {"mode": "active", **metrics})
    context = RuleAgentContext(
        claim=claim, store=store, settings=settings, heartbeat_callback=heartbeat_callback
    )
    try:
        await _run_with_heartbeat(
            context, _execute_claim(context, descriptor, model_factory or build_rule_chat_model)
        )
    except asyncio.CancelledError:
        # Shutdown retains the task budget and candidate. If the process is
        # killed before this write, lease recovery has the same retry semantics.
        await _safe_finish(
            context,
            AgentFailure(
                "worker_interrupted",
                "Rule worker stopped before task completion.",
                status="retry_wait",
                retry_after_seconds=1,
            ),
        )
        raise
    except JobGuardError as exc:
        if exc.code != "lease_lost":
            await _safe_finish(
                context,
                AgentFailure(
                    exc.code,
                    "Rule task stopped at its durable authority or budget guard.",
                    status="cancelled"
                    if exc.code in {"source_inactive", "input_changed"}
                    else "failed",
                ),
            )
    except AgentFailure as exc:
        await _safe_finish(context, exc, engine_snapshot=snapshot if exc.pause_engine else None)
    except Exception:
        # Do not persist provider HTTP bodies, prompts or credentials in diagnostics.
        logger.exception("Rule author task failed: job_id=%s", claim.job_id)
        await _safe_finish(
            context,
            AgentFailure(
                "rule_agent_internal_error", "Rule author encountered an internal execution error."
            ),
        )
    finally:
        await store.release(claim)
    metrics = await store._call("web_rule_agent_status", engine_snapshot=snapshot)
    return RuleAgentCycleResult(1, {"mode": "active", **metrics})


async def _safe_finish(
    context: RuleAgentContext,
    failure: AgentFailure,
    *,
    engine_snapshot: dict | None = None,
) -> bool:
    try:
        finish = context.store.finish if engine_snapshot is None else context.store.finish_and_pause
        await finish(
            context.claim,
            status=failure.status,
            code=failure.code,
            message=failure.message,
            retry_after_seconds=failure.retry_after_seconds,
            **({"engine_snapshot": engine_snapshot} if engine_snapshot is not None else {}),
        )
    except JobGuardError as exc:
        if exc.code != "lease_lost":
            raise
        return False
    return True


async def _run_with_heartbeat(context: RuleAgentContext, operation) -> None:
    async def renew() -> None:
        while True:
            if context.heartbeat_callback is not None:
                await context.heartbeat_callback(
                    {
                        "mode": "active",
                        "current_job_id": str(context.claim.job_id),
                        "stage": context.claim.stage,
                    }
                )
            await asyncio.sleep(context.settings.web_rule_agent_heartbeat_seconds)
            if not await context.store.heartbeat(context.claim):
                context.lost_lease = True
                raise JobGuardError("lease_lost")

    execution = asyncio.create_task(operation)
    heartbeat = asyncio.create_task(renew())
    try:
        done, _ = await asyncio.wait({execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if execution in done:
            await execution
        else:
            await heartbeat
    finally:
        for task in (execution, heartbeat):
            if not task.done():
                task.cancel()
        await asyncio.gather(execution, heartbeat, return_exceptions=True)


async def _execute_claim(context: RuleAgentContext, descriptor, model_factory) -> None:
    try:
        await _execute_claim_inner(context, descriptor, model_factory)
    finally:
        await context.close()


async def _execute_claim_inner(context: RuleAgentContext, descriptor, model_factory) -> None:
    if context.claim.stage == "activate" and context.claim.candidate_rule:
        await _revalidate_for_activation(context, context.claim.candidate_rule)
        await _finish_activation(context)
        return
    preflight = await _preflight(context)
    if context.claim.base_rule and preflight.get("first_window_completed") is True:
        await context.store.finish(
            context.claim,
            status="cancelled",
            code="source_recovered",
            message="The old rule completed a fresh production first window without model calls.",
        )
        return
    model = None
    try:
        try:
            model = model_factory(
                context.settings,
                descriptor,
                timeout_seconds=context.settings.web_rule_agent_model_timeout_seconds,
                max_output_tokens=context.settings.web_rule_agent_max_output_tokens,
            )
        except ChatModelConfigurationError as exc:
            raise AgentFailure(
                "rule_model_unavailable",
                "Rule model credentials or configuration are unavailable.",
                status="blocked",
                pause_engine=True,
            ) from exc
        await run_rule_agent(context, model, preflight=preflight)
    finally:
        if model is not None:
            try:
                async with asyncio.timeout(5):
                    await close_chat_model(model)
            except Exception:
                logger.warning(
                    "Rule model resource cleanup failed: job_id=%s", context.claim.job_id
                )
    await _finish_activation(context)


async def _preflight(context: RuleAgentContext) -> dict:
    if not context.claim.base_rule:
        return await context.inspect(argv=["open", context.claim.source_url])
    async with context.lock:
        await context.check()
        await context.store.reserve_tool(context.claim, "preflight_validate", validation=True)
        report = await web_rules.execute_web_rule(
            source_url=context.claim.source_url,
            raw_rule=context.claim.base_rule,
            timeout_seconds=context.settings.web_rule_agent_validate_timeout_seconds,
            browser_identity=context.claim.engine_snapshot.get("browser_execution_identity"),
        )
        await context.store.save_recovery_check(context.claim, report)
        return context.preflight_feedback(report)


async def _revalidate_for_activation(context: RuleAgentContext, candidate: dict) -> None:
    context.handoff = None
    await context.execute(candidate)
    # Activation uses persisted execution authority, never a bounded tool view.
    validation_id = context.receipt
    if not validation_id:
        raise AgentFailure("activation_revalidation_failed", "Saved candidate no longer validates.")
    await context.submit(validation_id)


async def _finish_activation(context: RuleAgentContext) -> None:
    while context.handoff and context.handoff.get("status") == "activation_pending":
        await asyncio.sleep(2)
        await context.check()
        try:
            context.handoff = {
                **context.handoff,
                **await context.store.activate(context.claim, context.handoff["validation_id"]),
            }
        except JobGuardError as exc:
            if exc.code != "validation_expired":
                raise
            candidate = context.latest_candidate or context.claim.candidate_rule
            if not candidate:
                raise AgentFailure("candidate_missing", "Activation candidate is missing.") from exc
            await _revalidate_for_activation(context, candidate)
