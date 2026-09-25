"""Host-owned execution receipt and activation handoff for one claimed rule job."""

from __future__ import annotations

import asyncio
from asyncio import sleep
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.ingestion import web_rules
from app.services.web_rule_jobs import JobGuardError

from .errors import AgentFailure


@dataclass
class ActivationHandoff:
    claim: Any
    store: Any
    settings: Any
    lock: asyncio.Lock
    check: Callable[[], Awaitable[None]]
    feedback: Callable[[dict], dict]
    checkpoint: Callable[[], Awaitable[None]]
    bound: Callable[[dict, str], dict]
    validation_required: Callable[[], None]
    candidate: dict | None = None
    receipt: str | None = None
    assessment: str = ""
    limitations: list[str] = field(default_factory=list)
    result: dict | None = None

    def __post_init__(self) -> None:
        self.candidate = getattr(self.claim, "candidate_rule", None) or getattr(
            self.claim, "base_rule", None
        )
        previous = getattr(self.claim, "last_feedback", None) or {}
        self.assessment = previous.get("assessment", "")
        self.limitations = previous.get("limitations", [])

    async def execute(self, raw_rule: dict) -> dict:
        """Save the complete scanner receipt before exposing a bounded model view."""
        async with self.lock:
            if self.result is not None:
                return self.result
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
            self.candidate = raw_rule
            view = self.feedback(saved)
            self.receipt = (
                saved.get("execution_id") if saved.get("first_window_completed") else None
            )
            await self.checkpoint()
            return view

    async def submit(
        self,
        validation_id: str,
        *,
        raw_rule: dict | None = None,
        assessment: str | None = None,
        limitations: list[str] | None = None,
    ) -> dict:
        """Only a persisted execution and store-side fencing may approve a handoff."""
        async with self.lock:
            if self.result is not None:
                return self.result
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
                self.validation_required()
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
                self.result = {**result, "validation_id": validation_id}
            return self.bound(result, "activate")

    async def _revalidate(self, candidate: dict) -> None:
        self.result = None
        await self.execute(candidate)
        # The bounded feedback is for the model only; activation uses the saved receipt.
        if not self.receipt:
            raise AgentFailure(
                "activation_revalidation_failed", "Saved candidate no longer validates."
            )
        await self.submit(self.receipt)

    async def resume(self) -> bool:
        """Resume a durable pending candidate without a model call or new budget."""
        candidate = getattr(self.claim, "candidate_rule", None)
        if self.claim.stage != "activate" or not candidate:
            return False
        await self._revalidate(candidate)
        await self.complete()
        return True

    async def complete(self) -> None:
        """Poll scanner authority in short transactions; refresh only an expired receipt."""
        while self.result is not None and self.result.get("status") == "activation_pending":
            await sleep(2)
            async with self.lock:
                await self.check()
                try:
                    result = await self.store.activate(self.claim, self.result["validation_id"])
                except JobGuardError as exc:
                    if exc.code != "validation_expired":
                        raise
                    candidate = self.candidate or getattr(self.claim, "candidate_rule", None)
                    if not candidate:
                        raise AgentFailure(
                            "candidate_missing", "Activation candidate is missing."
                        ) from exc
                    self.result = None
                else:
                    self.result = {**self.result, **result}
                    continue
            await self._revalidate(candidate)
