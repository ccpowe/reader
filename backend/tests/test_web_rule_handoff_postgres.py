"""Host handoff behavior on the guarded disposable PostgreSQL database."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_crawl4ai_persistence_postgres import PASS, RULE
from test_web_rule_jobs_postgres import ENGINE, _call, _job_database, _start

from app.core.settings import Settings
from app.ingestion import web_rules
from app.services import web_rule_jobs as jobs
from app.storage.models import FeedSource, SourceSyncState, WebRuleAgentRuntime, WebRuleJob
from app.web_rule_agent import handoff
from app.web_rule_agent.context import RuleAgentContext
from app.web_rule_agent.errors import AgentFailure
from app.web_rule_agent.store import JobStore
from app.workers import web_rules as worker

ASSESSMENT = "The current listing titles and article URLs match the observed DOM."
LIMITATIONS = ["Older pagination is not covered."]


@pytest.mark.postgres
async def test_scan_wait_expired_receipt_reexecutes_approved_candidate_before_activation(
    monkeypatch,
):
    executions = []

    async def execute(*, raw_rule, **kwargs):
        executions.append(raw_rule)
        return {
            **PASS,
            "execution_id": f"handoff-{len(executions)}",
            "status": "partial" if len(executions) == 2 else "completed",
            "errors": [{"code": "web_http_503", "window": 1}] if len(executions) == 2 else [],
        }

    monkeypatch.setattr(web_rules, "execute_web_rule", execute)
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id)
        context = RuleAgentContext(
            claim=claim, store=JobStore(factory), settings=Settings(_env_file=None)
        )
        first = await context.execute(RULE)
        assert first["execution_id"] == "handoff-1"
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            state.lease_token, state.lease_expires_at = (
                uuid4(),
                datetime.now(UTC) + timedelta(minutes=1),
            )
            await session.commit()
        accepted = await context.submit(
            first["execution_id"], raw_rule=RULE, assessment=ASSESSMENT, limitations=LIMITATIONS
        )
        assert accepted["status"] == "activation_pending"
        waits = []

        async def advance(seconds):
            waits.append(seconds)
            async with factory() as session:
                job = await session.get(WebRuleJob, claim.job_id)
                state = await session.get(SourceSyncState, source_id)
                if len(waits) == 1:
                    receipt = dict(job.validation_receipt)
                    receipt["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
                    job.validation_receipt = receipt
                    assert state.lease_token is not None
                elif len(waits) == 2:
                    assert job.validation_receipt["id"] == "handoff-2"
                    assert job.validation_report["assessment"] == ASSESSMENT
                    assert job.validation_report["limitations"] == LIMITATIONS
                    state.lease_token = state.lease_expires_at = None
                else:
                    pytest.fail("Activation did not complete after the scan lease was released")
                await session.commit()

        monkeypatch.setattr(handoff, "sleep", advance)
        await context.activation.complete()
        assert len(waits) == 2
        assert len(executions) == 2 and executions[0] == executions[1]
        assert context.activation.result["status"] == "succeeded"
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            job = await session.get(WebRuleJob, claim.job_id)
            validation = source.config["web_rule_versions"][-1]["validation"]
            assert source.config["web_rule"] == job.candidate_rule
            assert source.config["web_rule_revision"] == 1
            assert job.validation_report["execution_id"] == "handoff-2"
            assert validation["status"] == "partial"
            assert validation["assessment"] == ASSESSMENT
            assert validation["limitations"] == LIMITATIONS
            assert job.status == "succeeded"
            assert job.activated_version == source.config["web_rule_active_version"]
            assert job.model_calls_reserved == 0 and job.total_tokens == 0


@pytest.mark.postgres
@pytest.mark.parametrize("revalidated", [True, False], ids=["new-receipt", "first-window-error"])
async def test_reclaimed_activate_stage_uses_worker_without_model_or_old_receipt(
    monkeypatch, revalidated
):
    executions = []

    async def execute(*, raw_rule, **kwargs):
        executions.append(raw_rule)
        if len(executions) == 1:
            return {**PASS, "execution_id": "before-interruption"}
        if revalidated:
            return {**PASS, "execution_id": "after-reclaim"}
        return {
            **PASS,
            "execution_id": "failed-first-window",
            "status": "error",
            "first_window_completed": False,
            "errors": [{"code": "web_http_503", "window": 0}],
        }

    monkeypatch.setattr(web_rules, "execute_web_rule", execute)
    async with _job_database() as (factory, source_id):
        first_claim = await _start(factory, source_id)
        store = JobStore(factory)
        original = RuleAgentContext(
            claim=first_claim, store=store, settings=Settings(_env_file=None)
        )
        first_result = await original.execute(RULE)
        reservation = await store.reserve_model(first_claim, 50)
        await store.record_usage(
            first_claim,
            reservation,
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        )
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            state.lease_token, state.lease_expires_at = (
                uuid4(),
                datetime.now(UTC) + timedelta(minutes=1),
            )
            await session.commit()
        pending = await original.submit(
            first_result["execution_id"],
            raw_rule=RULE,
            assessment=ASSESSMENT,
            limitations=LIMITATIONS,
        )
        assert pending["status"] == "activation_pending"
        async with factory() as session:
            job = await session.get(WebRuleJob, first_claim.job_id)
            runtime = await session.get(WebRuleAgentRuntime, "default")
            job.lease_expires_at = runtime.lease_expires_at = datetime.now(UTC) - timedelta(
                seconds=1
            )
            state = await session.get(SourceSyncState, source_id)
            state.lease_token = state.lease_expires_at = None
            await session.commit()
        reclaimed = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            limits=jobs.JobLimits(),
        )
        assert reclaimed is not None and reclaimed.job_id == first_claim.job_id
        assert reclaimed.lease_token != first_claim.lease_token
        assert reclaimed.stage == "activate" and reclaimed.candidate_rule
        context = RuleAgentContext(
            claim=reclaimed, store=JobStore(factory), settings=Settings(_env_file=None)
        )

        def forbidden_model(*args, **kwargs):
            pytest.fail("A reclaimed approved candidate must not call the model")

        if revalidated:
            await worker._execute_claim(context, None, forbidden_model)
        else:
            with pytest.raises(AgentFailure, match="Saved candidate no longer validates"):
                await worker._execute_claim(context, None, forbidden_model)
        assert len(executions) == 2
        assert web_rules.parse_web_rule(
            executions[0], source_url=first_claim.source_url
        ) == web_rules.parse_web_rule(executions[1], source_url=first_claim.source_url)
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            job = await session.get(WebRuleJob, reclaimed.job_id)
            assert job.model_calls_reserved == 1 and job.total_tokens == 12
            assert job.attempt_count == 2
            if revalidated:
                validation = source.config["web_rule_versions"][-1]["validation"]
                assert source.config["web_rule"] == job.candidate_rule
                assert job.validation_report["execution_id"] == "after-reclaim"
                assert validation["assessment"] == ASSESSMENT
                assert validation["limitations"] == LIMITATIONS
                assert job.status == "succeeded"
            else:
                assert not source.config.get("web_rule")
                assert source.config.get("web_rule_revision") is None
                assert job.status == "running" and job.activated_version is None
                assert job.validation_report["execution_id"] == "failed-first-window"
                assert job.validation_receipt["first_window_completed"] is False
