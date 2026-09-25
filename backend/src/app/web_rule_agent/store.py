"""One short transaction per durable operation; never across a model or page request."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.services import web_rule_jobs as jobs


class JobStore:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def _transaction(self, operation: Callable[..., Awaitable[Any]], **kwargs) -> Any:
        async with self.session_factory() as session:
            result = await operation(session, **kwargs)
            await session.commit()
            return result

    async def backfill(self, *, limit: int) -> int:
        return await self._transaction(jobs.backfill_web_rule_jobs, limit=limit)

    async def claim(
        self,
        *,
        engine_snapshot: dict,
        limits: jobs.JobLimits,
        prompt_version: str,
        rule_schema_version: str,
        validator_version: str,
        browser_execution_identity: dict | None = None,
        cli_execution_identity: dict | None = None,
    ) -> jobs.WebRuleJobClaim | None:
        return await self._transaction(
            jobs.claim_web_rule_job,
            engine_snapshot=engine_snapshot,
            limits=limits,
            prompt_version=prompt_version,
            rule_schema_version=rule_schema_version,
            validator_version=validator_version,
            browser_execution_identity=browser_execution_identity,
            cli_execution_identity=cli_execution_identity,
        )

    async def status(self, *, engine_snapshot: dict) -> dict:
        return await self._transaction(jobs.web_rule_agent_status, engine_snapshot=engine_snapshot)

    async def check(self, claim):
        return await self._transaction(jobs.check_web_rule_job, claim=claim)

    async def progress(self, claim):
        return await self._transaction(jobs.web_rule_agent_progress, claim=claim)

    async def checkpoint(self, claim, checkpoint):
        await self.diagnostic(
            claim, phase="author", code="authoring_checkpoint", evidence=checkpoint
        )

    async def heartbeat(self, claim) -> bool:
        return await self._transaction(jobs.heartbeat_web_rule_job, claim=claim)

    async def reserve_model(self, claim, estimated_tokens: int) -> str:
        return await self._transaction(
            jobs.reserve_model_call, claim=claim, estimated_tokens=estimated_tokens
        )

    async def record_usage(self, claim, reservation_id: str, usage: dict | None) -> None:
        await self._transaction(
            jobs.record_model_usage, claim=claim, reservation_id=reservation_id, usage=usage
        )

    async def reserve_tool(self, claim, name: str, *, validation: bool = False) -> None:
        await self._transaction(
            jobs.reserve_tool_call, claim=claim, tool_name=name, validation=validation
        )

    async def resume_goal(self, claim, feedback: dict | str) -> int:
        return await self._transaction(jobs.reserve_goal_resumption, claim=claim, feedback=feedback)

    async def save_validation(self, claim, candidate: dict, report: dict) -> dict:
        return await self._transaction(
            jobs.save_web_rule_validation, claim=claim, raw_rule=candidate, report=report
        )

    async def save_recovery_check(self, claim, report: dict) -> dict:
        return await self._transaction(
            jobs.save_web_rule_recovery_check, claim=claim, report=report
        )

    async def activate(
        self,
        claim,
        validation_id: str,
        *,
        raw_rule: dict | None = None,
        assessment: str | None = None,
        limitations: list[str] | None = None,
    ) -> dict:
        return await self._transaction(
            jobs.activate_web_rule_job,
            claim=claim,
            validation_id=validation_id,
            raw_rule=raw_rule,
            assessment=assessment,
            limitations=limitations,
        )

    async def finish(
        self, claim, *, status: str, code: str, message: str = "", retry_after_seconds: float = 0
    ) -> None:
        await self._transaction(
            jobs.finish_web_rule_job,
            claim=claim,
            status=status,
            code=code,
            message=message,
            retry_after_seconds=retry_after_seconds,
        )

    async def pause(
        self, engine_snapshot: dict, *, code: str, message: str = "", claim=None
    ) -> None:
        await self._transaction(
            jobs.pause_web_rule_agent,
            engine_snapshot=engine_snapshot,
            code=code,
            message=message,
            claim=claim,
        )

    async def finish_and_pause(
        self,
        claim,
        *,
        engine_snapshot: dict,
        status: str,
        code: str,
        message: str = "",
        retry_after_seconds: float = 0,
    ) -> None:
        # Lock the runtime before source/job locks, matching claim/heartbeat.
        # Neither a terminal task without a pause nor a pause from a fenced
        # worker may become visible to the next claimant.
        async with self.session_factory() as session:
            paused = await jobs.pause_web_rule_agent(
                session,
                engine_snapshot=engine_snapshot,
                code=code,
                message=message,
                claim=claim,
            )
            if not paused:
                raise jobs.JobGuardError("lease_lost")
            await jobs.finish_web_rule_job(
                session,
                claim=claim,
                status=status,
                code=code,
                message=message,
                retry_after_seconds=retry_after_seconds,
            )
            await session.commit()

    async def release(self, claim) -> None:
        await self._transaction(jobs.release_web_rule_slot, claim=claim)

    async def diagnostic(self, claim, *, phase: str, code: str, evidence: dict) -> None:
        await self._transaction(
            jobs.record_web_rule_diagnostic,
            claim=claim,
            phase=phase,
            code=code,
            evidence=evidence,
        )
