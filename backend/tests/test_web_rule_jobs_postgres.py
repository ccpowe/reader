"""Durable authoring invariants on the guarded, disposable PostgreSQL database."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from test_candidate_recovery_postgres import _database
from test_crawl4ai_persistence_postgres import PASS, RULE

from app.core.settings import Settings
from app.domain.enums import SourceKind, SourceStatus, SyncRunStatus
from app.services import web_rule_jobs as jobs
from app.storage.models import (
    FeedSource,
    SourceSubscription,
    SourceSyncRun,
    SourceSyncState,
    WebRuleAgentRuntime,
    WebRuleJob,
)
from app.web_rule_agent.errors import AgentFailure
from app.web_rule_agent.store import JobStore
from app.workers import web_rules as worker

ENGINE = {
    "engine_id": "fake",
    "provider": "fake",
    "model": "tool-model",
    "config_fingerprint": "one",
}


@pytest.mark.postgres
@pytest.mark.parametrize("artifact", ["browser_execution_identity", "cli_execution_identity"])
async def test_worker_artifact_identity_fences_resume_without_changing_provider_pause_snapshot(
    artifact,
):
    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        await _call(factory, jobs.ensure_web_rule_job, source_id=source_id, now=now)
        first = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            **{artifact: {"binary_sha256": "first"}},
            limits=jobs.JobLimits(),
            now=now,
        )
        assert first.engine_snapshot[artifact]["binary_sha256"] == "first"
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=first,
            status="retry_wait",
            code="interrupted",
            retry_after_seconds=1,
            now=now,
        )
        await _call(factory, jobs.release_web_rule_slot, claim=first)
        assert (
            await _call(
                factory,
                jobs.claim_web_rule_job,
                engine_snapshot=ENGINE,
                **{artifact: {"binary_sha256": "changed"}},
                limits=jobs.JobLimits(),
                now=now + timedelta(seconds=2),
            )
            is None
        )
        async with factory() as session:
            assert (await session.get(WebRuleJob, first.job_id)).last_error_code == "config_changed"
            assert (await session.get(WebRuleAgentRuntime, "default")).engine_snapshot == ENGINE


@pytest.mark.postgres
@pytest.mark.parametrize("interruption", ["source_inactive", "lease_lost"])
async def test_worker_heartbeat_interruption_preserves_recoverable_task(monkeypatch, interruption):
    settings = Settings(_env_file=None, web_rule_agent_engine_id="deepseek-v4-flash")
    settings.web_rule_agent_heartbeat_seconds = 0.01
    cancelled = asyncio.Event()
    claims = []
    async with _job_database() as (factory, source_id):

        async def operation(context, descriptor, model_factory):
            claims.append(context.claim)
            reservation = await context.store.reserve_model(context.claim, 50)
            await context.store.record_usage(
                context.claim,
                reservation,
                {
                    "input_tokens": 10,
                    "output_tokens": 1,
                    "total_tokens": 11,
                },
            )
            async with factory() as session:
                if interruption == "source_inactive":
                    subscription = await session.scalar(
                        select(SourceSubscription).where(SourceSubscription.source_id == source_id)
                    )
                    subscription.is_enabled = False
                else:
                    job = await session.get(WebRuleJob, context.claim.job_id)
                    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(worker, "_execute_claim", operation)
        async with asyncio.timeout(5):
            await worker.run_web_rule_author_once(factory, settings=settings)
        assert cancelled.is_set()
        claim = claims[0]
        async with factory() as session:
            job = await session.get(WebRuleJob, claim.job_id)
            assert job.model_calls_reserved == 1 and job.total_tokens == 11
            assert job.deadline_at == claim.deadline_at
            if interruption == "source_inactive":
                assert (job.status, job.last_error_code) == ("cancelled", "source_inactive")
                subscription = await session.scalar(
                    select(SourceSubscription).where(SourceSubscription.source_id == source_id)
                )
                subscription.is_enabled = True
                await session.commit()
            else:
                assert job.status == "running" and job.finished_at is None
        await _call(factory, jobs.ensure_web_rule_job, source_id=source_id)
        resumed = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=job.engine_snapshot,
            limits=worker.job_limits(settings),
            prompt_version=job.prompt_version,
            rule_schema_version=job.rule_schema_version,
            validator_version=job.validator_version,
        )
        assert resumed.job_id == claim.job_id and resumed.lease_token != claim.lease_token
        assert resumed.deadline_at == claim.deadline_at
        assert resumed.budget_snapshot["model_calls_reserved"] == 1
        assert resumed.budget_snapshot["total_tokens"] == 11
        assert resumed.budget_snapshot["attempt_count"] == 2


@pytest.mark.postgres
@pytest.mark.parametrize("status_code", [401, 402])
async def test_provider_block_is_visible_before_another_worker_can_claim(monkeypatch, status_code):
    settings = Settings(_env_file=None, web_rule_agent_engine_id="deepseek-v4-flash")
    async with _job_database() as (factory, first_source), _job_database() as (_, second_source):
        first = await _call(factory, jobs.ensure_web_rule_job, source_id=first_source)
        second = await _call(factory, jobs.ensure_web_rule_job, source_id=second_source)
        attempts = []
        snapshot = {}

        class InterleavingStore(JobStore):
            async def competing_claim(self):
                attempts.append(
                    await self._call(
                        "claim_web_rule_job",
                        engine_snapshot=snapshot["engine"],
                        limits=worker.job_limits(settings),
                        prompt_version=snapshot["prompt"],
                        rule_schema_version=snapshot["schema"],
                        validator_version=snapshot["validator"],
                    )
                )

            async def finish(self, claim, **kwargs):
                await super().finish(claim, **kwargs)
                # Reproduce another worker taking the slot between finish and pause.
                await self.competing_claim()

            async def finish_and_pause(self, claim, **kwargs):
                await super().finish_and_pause(claim, **kwargs)
                await self.competing_claim()

        async def operation(context, descriptor, model_factory):
            claim = context.claim
            assert claim.job_id == first.id
            snapshot.update(
                engine=claim.engine_snapshot,
                prompt=claim.prompt_version,
                schema=claim.rule_schema_version,
                validator=claim.validator_version,
            )
            raise AgentFailure(
                f"model_http_{status_code}",
                "Provider unavailable.",
                status="blocked",
                pause_engine=True,
            )

        monkeypatch.setattr(worker, "_execute_claim", operation)
        await worker.run_web_rule_author_once(
            factory,
            settings=settings,
            store=InterleavingStore(factory),
        )
        assert attempts == [None]
        async with factory() as session:
            assert (await session.get(WebRuleJob, first.id)).status == "blocked"
            assert (await session.get(WebRuleJob, second.id)).status == "queued"
            runtime = await session.get(WebRuleAgentRuntime, "default")
            assert runtime.paused_code == f"model_http_{status_code}"
            assert runtime.current_job_id is None


@pytest.mark.postgres
async def test_fenced_task_cannot_leave_a_partial_engine_pause():
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id)
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=claim,
            status="cancelled",
            code="source_inactive",
        )
        with pytest.raises(jobs.JobGuardError, match="lease_lost"):
            await JobStore(factory).finish_and_pause(
                claim,
                engine_snapshot=ENGINE,
                status="blocked",
                code="model_http_402",
            )
        async with factory() as session:
            assert (await session.get(WebRuleAgentRuntime, "default")).paused_code is None
            job = await session.get(WebRuleJob, claim.job_id)
            assert (job.status, job.last_error_code) == ("cancelled", "source_inactive")


def test_job_limits_keep_token_and_model_quotas_and_operational_controls():
    from dataclasses import asdict

    assert asdict(jobs.JobLimits()) == {
        "total_tokens": 3_000_000,
        "model_calls": 80,
        "lease_seconds": 120,
        "validation_ttl_seconds": 300,
        "diagnostic_bytes": 256 * 1024,
    }
    for field in asdict(jobs.JobLimits()):
        with pytest.raises(ValueError, match="positive"):
            jobs.JobLimits(**{field: 0})


@asynccontextmanager
async def _job_database():
    async with _database() as (factory, source_id):
        user_id = uuid4()
        async with factory() as session:
            await session.execute(
                text("INSERT INTO profiles (id) VALUES (:id)"),
                {"id": user_id},
            )
            source = await session.get(FeedSource, source_id)
            source.kind = SourceKind.WEB
            # These fixtures exercise rule authoring after RSS probing completed.
            source.config = {
                "web_feed_discovery": {
                    "version": 1,
                    "status": "not_found",
                    "source_url": source.canonical_url,
                }
            }
            session.add(SourceSubscription(source_id=source_id, user_id=user_id))
            session.add(SourceSyncState(source_id=source_id, last_error_code="web_rule_required"))
            runtime = await session.get(WebRuleAgentRuntime, "default")
            runtime.current_job_id = runtime.lease_token = runtime.lease_expires_at = None
            runtime.paused_code = runtime.paused_at = runtime.paused_message = None
            await session.commit()
        try:
            yield factory, source_id
        finally:
            async with factory() as session:
                await session.execute(text("DELETE FROM profiles WHERE id = :id"), {"id": user_id})
                runtime = await session.get(WebRuleAgentRuntime, "default")
                runtime.current_job_id = runtime.lease_token = runtime.lease_expires_at = None
                runtime.paused_code = runtime.paused_at = runtime.paused_message = None
                await session.commit()


async def _call(factory, fn, **kwargs):
    async with factory() as session:
        result = await fn(session, **kwargs)
        await session.commit()
        return result


async def _start(factory, source_id, *, now=None, limits=None):
    await _call(factory, jobs.ensure_web_rule_job, source_id=source_id, now=now)
    return await _call(
        factory,
        jobs.claim_web_rule_job,
        engine_snapshot=ENGINE,
        limits=limits or jobs.JobLimits(),
        now=now,
    )


@pytest.mark.postgres
async def test_dispatch_is_unique_across_subscribers_and_terminal_budget_is_not_refreshed():
    async with _job_database() as (factory, source_id):
        first, second = await asyncio.gather(
            *[_call(factory, jobs.ensure_web_rule_job, source_id=source_id) for _ in range(2)]
        )
        assert first.id == second.id
        claim = await _call(
            factory, jobs.claim_web_rule_job, engine_snapshot=ENGINE, limits=jobs.JobLimits()
        )
        assert claim.job_id == first.id
        assert (
            await _call(
                factory, jobs.claim_web_rule_job, engine_snapshot=ENGINE, limits=jobs.JobLimits()
            )
            is None
        )
        await _call(factory, jobs.reserve_model_call, claim=claim, estimated_tokens=100)
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=claim,
            status="failed",
            code="token_budget_exhausted",
        )
        await _call(factory, jobs.release_web_rule_slot, claim=claim)
        repeated = await _call(factory, jobs.ensure_web_rule_job, source_id=source_id)
        assert repeated.id == first.id and repeated.status == "failed"
        assert repeated.model_calls_reserved == 1
        assert await _call(factory, jobs.backfill_web_rule_jobs) == 0
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(WebRuleJob)) == 1
        retry = await _call(factory, jobs.retry_web_rule_job, job_id=first.id, request_id=uuid4())
        assert retry.id != first.id and retry.retry_of == first.id
        assert retry.model_calls_reserved == 0 and retry.deadline_at is None


@pytest.mark.postgres
async def test_crash_reclaim_preserves_unknown_usage_without_attempt_or_deadline_caps():
    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        first = await _start(factory, source_id, now=now)
        reservation = await _call(
            factory, jobs.reserve_model_call, claim=first, estimated_tokens=1234, now=now
        )
        passed = await _call(
            factory, jobs.save_web_rule_validation, claim=first, raw_rule=RULE, report=PASS, now=now
        )
        later = now + timedelta(seconds=121)
        second = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            limits=jobs.JobLimits(total_tokens=9_000_000),
            now=later,
        )
        assert second.job_id == first.job_id and second.lease_token != first.lease_token
        assert second.deadline_at == first.deadline_at
        async with factory() as session:
            job = await session.get(WebRuleJob, first.job_id)
            assert job.model_calls_reserved == 1 and job.unknown_usage_count == 1
            assert job.conservative_tokens_reserved == 1234
            assert job.usage_reservations[reservation]["state"] == "unknown"
        with pytest.raises(jobs.JobGuardError, match="lease_lost"):
            await _call(
                factory, jobs.reserve_model_call, claim=first, estimated_tokens=1, now=later
            )
        with pytest.raises(jobs.JobGuardError, match="validation_expired"):
            await _call(
                factory,
                jobs.activate_web_rule_job,
                claim=second,
                validation_id=passed["execution_id"],
                now=later,
            )
        # Earlier snapshots must neither restore retired limits nor refresh
        # the recorded token allowance when the worker's settings change.
        async with factory() as session:
            job = await session.get(WebRuleJob, first.job_id)
            job.limits_snapshot = {
                **job.limits_snapshot,
                "model_calls": 1,
                "tool_calls": 1,
                "validation_calls": 1,
                "goal_resumptions": 0,
                "max_attempts": 2,
                "total_seconds": 600,
            }
            job.deadline_at = now + timedelta(seconds=200)
            await session.commit()
        for attempt in range(3, 13):
            resumed = await _call(
                factory,
                jobs.claim_web_rule_job,
                engine_snapshot=ENGINE,
                limits=jobs.JobLimits(total_tokens=9_000_000),
                now=now + timedelta(seconds=121 * (attempt - 1)),
            )
            assert resumed.job_id == first.job_id
            assert resumed.deadline_at is None
            assert resumed.budget_snapshot["attempt_count"] == attempt
            assert resumed.budget_snapshot["limits"]["total_tokens"] == 3_000_000
            assert resumed.budget_snapshot["limits"]["model_calls"] == 1
        # The interrupted model call still consumes its slot after all reclaims.
        with pytest.raises(jobs.JobGuardError, match="model_budget_exhausted"):
            await _call(
                factory,
                jobs.reserve_model_call,
                claim=resumed,
                estimated_tokens=1,
                now=now + timedelta(seconds=121 * 11),
            )
        async with factory() as session:
            job = await session.get(WebRuleJob, first.job_id)
            assert job.status == "running" and job.unknown_usage_count == 1
            assert job.conservative_tokens_reserved == 1234


@pytest.mark.postgres
async def test_receipt_activation_and_job_success_are_atomic_and_scan_wait_preserves_candidate():
    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id, now=now)
        with pytest.raises(jobs.JobGuardError, match="validation_expired"):
            await _call(
                factory,
                jobs.activate_web_rule_job,
                claim=claim,
                validation_id=str(uuid4()),
                now=now,
            )
        receipt = await _call(
            factory, jobs.save_web_rule_validation, claim=claim, raw_rule=RULE, report=PASS, now=now
        )
        scan_token, run_id = uuid4(), uuid4()
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            state.lease_token, state.lease_expires_at = scan_token, now + timedelta(seconds=60)
            state.committed_checkpoint = {"head_ids": ["keep"], "validators": {"etag": "old"}}
            session.add(SourceSyncRun(id=run_id, source_id=source_id, status=SyncRunStatus.RUNNING))
            await session.commit()
        pending = await _call(
            factory,
            jobs.activate_web_rule_job,
            claim=claim,
            validation_id=receipt["execution_id"],
            now=now,
        )
        assert pending["status"] == "activation_pending"
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            state.lease_expires_at = now - timedelta(seconds=1)
            await session.commit()
        # Roll back the entire service call: neither successful status nor active
        # rule may leak out independently.
        async with factory() as session:
            result = await jobs.activate_web_rule_job(
                session, claim=claim, validation_id=receipt["execution_id"], now=now
            )
            assert result["status"] == "succeeded"
            await session.rollback()
        async with factory() as session:
            assert (await session.get(WebRuleJob, claim.job_id)).status == "running"
            assert not (await session.get(FeedSource, source_id)).config.get("web_rule")
        result = await _call(
            factory,
            jobs.activate_web_rule_job,
            claim=claim,
            validation_id=receipt["execution_id"],
            now=now,
        )
        async with factory() as session:
            source, state = (
                await session.get(FeedSource, source_id),
                await session.get(SourceSyncState, source_id),
            )
            job = await session.get(WebRuleJob, claim.job_id)
            assert job.status == "succeeded" and job.activated_version == result["version"]
            assert source.config["web_rule_revision"] == 1
            assert state.committed_checkpoint == {"head_ids": ["keep"]}
            assert state.lease_token is None and state.next_scan_at == now
            assert (await session.get(SourceSyncRun, run_id)).status == "partial"


@pytest.mark.postgres
async def test_changed_candidate_and_same_hash_new_source_revision_reject_old_acceptance():
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id)
        first = await _call(
            factory, jobs.save_web_rule_validation, claim=claim, raw_rule=RULE, report=PASS
        )
        failed = await _call(
            factory,
            jobs.save_web_rule_validation,
            claim=claim,
            raw_rule={"invalid": "candidate"},
            report={"status": "fail", "code": "schema_invalid"},
        )
        assert "validation_id" not in failed
        with pytest.raises(jobs.JobGuardError, match="validation_expired"):
            await _call(
                factory,
                jobs.activate_web_rule_job,
                claim=claim,
                validation_id=first["execution_id"],
            )
        second = await _call(
            factory, jobs.save_web_rule_validation, claim=claim, raw_rule=RULE, report=PASS
        )
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            source.config = {**source.config, "web_rule_revision": 2}
            await session.commit()
        with pytest.raises(jobs.JobGuardError, match="input_changed"):
            await _call(
                factory,
                jobs.activate_web_rule_job,
                claim=claim,
                validation_id=second["execution_id"],
            )


@pytest.mark.postgres
async def test_inactive_source_resume_preserves_spent_tokens_without_old_deadline():
    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id, now=now, limits=jobs.JobLimits(total_tokens=50))
        await _call(factory, jobs.reserve_model_call, claim=claim, estimated_tokens=50, now=now)
        async with factory() as session:
            (await session.get(FeedSource, source_id)).status = SourceStatus.PAUSED
            await session.commit()
        with pytest.raises(jobs.JobGuardError, match="source_inactive"):
            await _call(factory, jobs.check_web_rule_job, claim=claim, now=now)
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=claim,
            status="cancelled",
            code="source_inactive",
            now=now,
        )
        await _call(factory, jobs.release_web_rule_slot, claim=claim)
        async with factory() as session:
            (await session.get(FeedSource, source_id)).status = SourceStatus.ACTIVE
            await session.commit()
        resumed = await _call(
            factory, jobs.ensure_web_rule_job, source_id=source_id, now=now + timedelta(seconds=1)
        )
        assert resumed.id == claim.job_id and resumed.model_calls_reserved == 1
        assert resumed.deadline_at == claim.deadline_at
        later = now + timedelta(seconds=601)
        reclaimed = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            limits=jobs.JobLimits(),
            now=later,
        )
        assert reclaimed.job_id == claim.job_id and reclaimed.deadline_at is None
        with pytest.raises(jobs.JobGuardError, match="token_budget_exhausted"):
            await _call(
                factory, jobs.reserve_model_call, claim=reclaimed, estimated_tokens=1, now=later
            )
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=reclaimed,
            status="failed",
            code="token_budget_exhausted",
            now=later,
        )
        async with factory() as session:
            job = await session.get(WebRuleJob, claim.job_id)
            assert job.status == "failed" and job.last_error_code == "token_budget_exhausted"
            assert job.unknown_usage_count == 1 and job.conservative_tokens_reserved == 50
        assert (
            await _call(factory, jobs.ensure_web_rule_job, source_id=source_id)
        ).status == "failed"


@pytest.mark.postgres
async def test_engine_resume_does_not_retry_blocked_tasks_or_change_their_model_snapshot():
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id)
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=claim,
            status="blocked",
            code="model_payment_required",
        )
        await _call(
            factory,
            jobs.pause_web_rule_agent,
            engine_snapshot=ENGINE,
            code="model_payment_required",
        )
        await _call(factory, jobs.release_web_rule_slot, claim=claim)
        assert (await _call(factory, jobs.web_rule_agent_status, engine_snapshot=ENGINE))["paused"]
        await _call(factory, jobs.resume_web_rule_agent)
        assert (
            await _call(
                factory, jobs.claim_web_rule_job, engine_snapshot=ENGINE, limits=jobs.JobLimits()
            )
            is None
        )
        assert (
            await _call(factory, jobs.ensure_web_rule_job, source_id=source_id)
        ).status == "blocked"
        retry_id = uuid4()
        retry = await _call(
            factory, jobs.retry_web_rule_job, job_id=claim.job_id, request_id=retry_id
        )
        same = await _call(
            factory, jobs.retry_web_rule_job, job_id=claim.job_id, request_id=retry_id
        )
        assert same.id == retry.id
        next_claim = await _call(
            factory, jobs.claim_web_rule_job, engine_snapshot=ENGINE, limits=jobs.JobLimits()
        )
        assert next_claim.job_id == retry.id
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=next_claim,
            status="retry_wait",
            code="network_timeout",
            retry_after_seconds=1,
        )
        await _call(factory, jobs.release_web_rule_slot, claim=next_claim)
        assert (
            await _call(
                factory,
                jobs.claim_web_rule_job,
                engine_snapshot={**ENGINE, "model": "changed"},
                limits=jobs.JobLimits(),
                now=datetime.now(UTC) + timedelta(seconds=2),
            )
            is None
        )
        async with factory() as session:
            assert (await session.get(WebRuleJob, retry.id)).last_error_code == "config_changed"


@pytest.mark.postgres
async def test_repair_preflight_recovery_closes_episode_without_model_or_rule_replacement():
    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            source.config = {"web_rule": RULE, "web_rule_revision": 4}
            state = await session.get(SourceSyncState, source_id)
            state.web_rule_structural_failures, state.web_rule_failure_episode_id = 2, uuid4()
            state.last_error_code = "web_structure_changed"
            await session.commit()
        job = await _call(
            factory, jobs.ensure_web_rule_job, source_id=source_id, reason="repair", now=now
        )
        claim = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            limits=jobs.JobLimits(),
            now=now,
        )
        assert claim.job_id == job.id and claim.reason == "repair"
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=claim,
            status="cancelled",
            code="source_recovered",
            now=now,
        )
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            assert (
                state.web_rule_failure_episode_id is None
                and state.web_rule_structural_failures == 0
            )
            assert state.next_scan_at == now
            assert (await session.get(FeedSource, source_id)).config["web_rule_revision"] == 4
            assert (await session.get(WebRuleJob, claim.job_id)).model_calls_reserved == 0


@pytest.mark.postgres
@pytest.mark.parametrize(
    "unknown_usage", [None, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}]
)
async def test_actual_and_unknown_usage_stop_next_model_without_estimate_pre_rejection(
    unknown_usage,
):
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id, limits=jobs.JobLimits(total_tokens=100))
        reserved = await _call(factory, jobs.reserve_model_call, claim=claim, estimated_tokens=60)
        await _call(
            factory,
            jobs.record_model_usage,
            claim=claim,
            reservation_id=reserved,
            usage=unknown_usage,
        )
        # 40 tokens remain, but a 1,000-token estimate must not reject this call.
        next_reserved = await _call(
            factory, jobs.reserve_model_call, claim=claim, estimated_tokens=1000
        )
        running = await _call(factory, jobs.check_web_rule_job, claim=claim)
        assert jobs.remaining_model_tokens(running) == 40
        assert await _call(factory, jobs.heartbeat_web_rule_job, claim=claim)
        with pytest.raises(jobs.JobGuardError, match="model_call_in_flight"):
            await _call(factory, jobs.reserve_model_call, claim=claim, estimated_tokens=1)
        await _call(
            factory,
            jobs.record_model_usage,
            claim=claim,
            reservation_id=next_reserved,
            usage={"input_tokens": 5, "output_tokens": 5, "total_tokens": 10},
        )
        # A duplicate successful accounting callback cannot add usage twice.
        await _call(
            factory,
            jobs.record_model_usage,
            claim=claim,
            reservation_id=next_reserved,
            usage={"total_tokens": 999},
        )
        last_reserved = await _call(
            factory, jobs.reserve_model_call, claim=claim, estimated_tokens=30
        )
        await _call(
            factory, jobs.record_model_usage, claim=claim, reservation_id=last_reserved, usage=None
        )
        with pytest.raises(jobs.JobGuardError, match="token_budget_exhausted"):
            await _call(factory, jobs.reserve_model_call, claim=claim, estimated_tokens=1)
        # Host work is still permitted after the total allowance is spent.
        await _call(
            factory,
            jobs.reserve_tool_call,
            claim=claim,
            tool_name="execute_web_rule",
            validation=True,
        )
        async with factory() as session:
            job = await session.get(WebRuleJob, claim.job_id)
            assert job.total_tokens == 10 and job.conservative_tokens_reserved == 90
            assert job.unknown_usage_count == 2 and job.model_calls_reserved == 3
            assert job.validation_calls_reserved == 1 and job.tool_calls_reserved == 1


@pytest.mark.postgres
async def test_claim_skips_source_locked_by_compensation_batch_without_lock_order_deadlock(
    monkeypatch,
):
    from dataclasses import asdict

    from sqlalchemy import delete

    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        second_id = uuid4()
        try:
            async with factory() as session:
                user_id = await session.scalar(
                    select(SourceSubscription.user_id).where(
                        SourceSubscription.source_id == source_id
                    )
                )
                session.add(
                    FeedSource(
                        id=second_id,
                        kind="web",
                        canonical_key=f"lock-order:{second_id}",
                        canonical_url="https://example.com/feed",
                        config={
                            "web_feed_discovery": {
                                "version": 1,
                                "status": "not_found",
                                "source_url": "https://example.com/feed",
                            }
                        },
                    )
                )
                await session.flush()
                session.add(
                    SourceSyncState(source_id=second_id, last_error_code="web_rule_required")
                )
                session.add(SourceSubscription(source_id=second_id, user_id=user_id))
                await session.commit()
            low, high = sorted([source_id, second_id])
            for current in (low, high):
                await _call(factory, jobs.ensure_web_rule_job, source_id=current, now=now)
            async with factory() as session:
                high_job = await session.scalar(
                    select(WebRuleJob).where(WebRuleJob.source_id == high)
                )
                high_job.available_at = now - timedelta(seconds=2)
                high_job.started_at = now - timedelta(seconds=5)
                high_job.deadline_at = now + timedelta(seconds=595)
                high_job.engine_snapshot = {**ENGINE, "model": "old"}
                high_job.limits_snapshot = asdict(jobs.JobLimits())
                await session.commit()
            claim_has_high, compensation_has_low = asyncio.Event(), asyncio.Event()
            original_lock = jobs._source_locked

            async def interleaved_lock(session, source_id, *, skip_locked=False):
                result = await original_lock(session, source_id, skip_locked=skip_locked)
                if source_id == high and skip_locked and result[0] is not None:
                    claim_has_high.set()
                    await compensation_has_low.wait()
                return result

            monkeypatch.setattr(jobs, "_source_locked", interleaved_lock)

            async def compensate_in_uuid_order():
                await claim_has_high.wait()
                async with factory() as session:
                    await original_lock(session, low)
                    compensation_has_low.set()
                    await jobs.ensure_web_rule_job(session, source_id=high, now=now)
                    await session.commit()

            # Force opposite source orders in real transactions: claim holds
            # high and compensation holds low. SKIP LOCKED must break the wait
            # cycle while claim terminalizes its stale configuration job.
            async with asyncio.timeout(5):
                result, _ = await asyncio.gather(
                    _call(
                        factory,
                        jobs.claim_web_rule_job,
                        engine_snapshot=ENGINE,
                        limits=jobs.JobLimits(),
                        now=now,
                    ),
                    compensate_in_uuid_order(),
                )
            assert result is None
            async with factory() as session:
                statuses = dict(
                    (await session.execute(select(WebRuleJob.source_id, WebRuleJob.status))).all()
                )
                assert statuses[high] == "blocked" and statuses[low] == "queued"
        finally:
            async with factory() as session:
                await session.execute(delete(FeedSource).where(FeedSource.id == second_id))
                await session.commit()


@pytest.mark.postgres
async def test_long_provider_retry_after_preserves_delay_without_whole_job_deadline():
    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id, now=now)
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=claim,
            status="retry_wait",
            code="rate_limited",
            retry_after_seconds=900,
            now=now,
        )
        async with factory() as session:
            job = await session.get(WebRuleJob, claim.job_id)
            assert job.status == "retry_wait" and job.last_error_code == "rate_limited"
            assert job.available_at == now + timedelta(seconds=900)
            assert job.attempt_count == 1 and job.deadline_at is None
        assert (
            await _call(
                factory,
                jobs.claim_web_rule_job,
                engine_snapshot=ENGINE,
                limits=jobs.JobLimits(),
                now=now + timedelta(seconds=300),
            )
            is None
        )

        resumed = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            limits=jobs.JobLimits(),
            now=now + timedelta(seconds=901),
        )
        assert resumed.job_id == claim.job_id
        assert resumed.budget_snapshot["attempt_count"] == 2
        assert resumed.deadline_at is None


@pytest.mark.postgres
@pytest.mark.parametrize("reported_tokens", [100, 125])
async def test_reported_token_threshold_stops_next_model_but_allows_host_activation(
    reported_tokens,
):
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id, limits=jobs.JobLimits(total_tokens=100))
        reservation = await _call(
            factory, jobs.reserve_model_call, claim=claim, estimated_tokens=1000
        )
        await _call(
            factory,
            jobs.record_model_usage,
            claim=claim,
            reservation_id=reservation,
            usage={"total_tokens": reported_tokens},
        )
        with pytest.raises(jobs.JobGuardError, match="token_budget_exhausted"):
            await _call(factory, jobs.reserve_model_call, claim=claim, estimated_tokens=1)
        assert await _call(factory, jobs.heartbeat_web_rule_job, claim=claim)
        await _call(
            factory,
            jobs.reserve_tool_call,
            claim=claim,
            tool_name="execute_web_rule",
            validation=True,
        )
        receipt = await _call(
            factory, jobs.save_web_rule_validation, claim=claim, raw_rule=RULE, report=PASS
        )
        result = await _call(
            factory,
            jobs.activate_web_rule_job,
            claim=claim,
            validation_id=receipt["execution_id"],
            raw_rule=RULE,
        )
        assert result["status"] == "succeeded"
        async with factory() as session:
            job = await session.get(WebRuleJob, claim.job_id)
            assert job.status == "succeeded" and job.total_tokens == reported_tokens
            assert (await session.get(FeedSource, source_id)).config.get("web_rule")


@pytest.mark.postgres
async def test_goal_resumptions_survive_retry_without_separate_round_quota():
    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        first = await _start(
            factory,
            source_id,
            now=now,
            limits=jobs.JobLimits(diagnostic_bytes=4096),
        )
        assert first.budget_snapshot["goal_resumptions_reserved"] == 0
        assert (
            await _call(
                factory,
                jobs.reserve_goal_resumption,
                claim=first,
                feedback={"code": "final_rule_missing"},
                now=now,
            )
            == 1
        )
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=first,
            status="retry_wait",
            code="worker_interrupted",
            retry_after_seconds=1,
            now=now,
        )
        await _call(factory, jobs.release_web_rule_slot, claim=first)
        later = now + timedelta(seconds=2)
        resumed = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            limits=jobs.JobLimits(),
            now=later,
        )
        assert resumed.job_id == first.job_id and resumed.deadline_at == first.deadline_at
        assert "goal_resumptions" not in resumed.budget_snapshot["limits"]
        assert resumed.budget_snapshot["goal_resumptions_reserved"] == 1
        assert (
            await _call(
                factory,
                jobs.reserve_goal_resumption,
                claim=resumed,
                feedback={"code": "final_rule_mismatch", "detail": "feedback " * 10000},
                now=later,
            )
            == 2
        )
        for expected in range(3, 12):
            assert (
                await _call(
                    factory,
                    jobs.reserve_goal_resumption,
                    claim=resumed,
                    feedback="Continue until the host verifies completion.",
                    now=later,
                )
                == expected
            )
        detail = await _call(factory, jobs.get_web_rule_job, job_id=resumed.job_id)
        assert detail["goal_resumptions_reserved"] == 11
        assert len(json.dumps(detail["diagnostics"], ensure_ascii=False).encode()) <= 4096
        assert any(row["code"] == "goal_resumption_reserved" for row in detail["diagnostics"])
        # Goal telemetry never prevents normal tools or activation.
        await _call(factory, jobs.check_web_rule_job, claim=resumed, now=later)
        await _call(
            factory,
            jobs.reserve_tool_call,
            claim=resumed,
            tool_name="execute_web_rule",
            validation=True,
            now=later,
        )
        receipt = await _call(
            factory,
            jobs.save_web_rule_validation,
            claim=resumed,
            raw_rule=RULE,
            report=PASS,
            now=later,
        )
        result = await _call(
            factory,
            jobs.activate_web_rule_job,
            claim=resumed,
            validation_id=receipt["execution_id"],
            raw_rule=RULE,
            now=later,
        )
        assert result["status"] == "succeeded"


@pytest.mark.postgres
async def test_model_call_cap_is_durable_but_tools_and_activation_can_finish():
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id)
        # The last model call is allowed; tool and validation counts remain telemetry.
        async with factory() as session:
            for _ in range(80):
                reserved = await jobs.reserve_model_call(session, claim=claim, estimated_tokens=100)
                await jobs.record_model_usage(
                    session, claim=claim, reservation_id=reserved, usage={"total_tokens": 1}
                )
            for _ in range(101):
                await jobs.reserve_tool_call(
                    session, claim=claim, tool_name="execute_web_rule", validation=True
                )
            await session.commit()
        job = await _call(factory, jobs.check_web_rule_job, claim=claim)
        assert job.model_calls_reserved == 80 and job.total_tokens == 80
        assert job.tool_calls_reserved == 101 and job.validation_calls_reserved == 101
        with pytest.raises(jobs.JobGuardError, match="model_budget_exhausted"):
            await _call(factory, jobs.reserve_model_call, claim=claim, estimated_tokens=1)
        assert await _call(factory, jobs.heartbeat_web_rule_job, claim=claim)
        receipt = await _call(
            factory, jobs.save_web_rule_validation, claim=claim, raw_rule=RULE, report=PASS
        )
        result = await _call(
            factory,
            jobs.activate_web_rule_job,
            claim=claim,
            validation_id=receipt["execution_id"],
            raw_rule=RULE,
        )
        assert result["status"] == "succeeded"


@pytest.mark.postgres
async def test_goal_reservation_rejects_expired_and_reclaimed_leases_without_spending_budget():
    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        first = await _start(factory, source_id, now=now)
        later = now + timedelta(seconds=121)
        with pytest.raises(jobs.JobGuardError, match="lease_lost"):
            await _call(
                factory,
                jobs.reserve_goal_resumption,
                claim=first,
                feedback="Late host continuation.",
                now=later,
            )
        second = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            limits=jobs.JobLimits(),
            now=later,
        )
        assert second.lease_token != first.lease_token
        assert second.budget_snapshot["goal_resumptions_reserved"] == 0
        with pytest.raises(jobs.JobGuardError, match="lease_lost"):
            await _call(
                factory,
                jobs.reserve_goal_resumption,
                claim=first,
                feedback="Old executor continuation.",
                now=later,
            )
        assert (
            await _call(
                factory,
                jobs.reserve_goal_resumption,
                claim=second,
                feedback={"code": "incomplete_final_output"},
                now=later,
            )
            == 1
        )


@pytest.mark.postgres
async def test_final_rule_must_match_the_normalized_validated_candidate_before_activation():
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id)
        receipt = await _call(
            factory, jobs.save_web_rule_validation, claim=claim, raw_rule=RULE, report=PASS
        )
        for final_rule in ({**RULE, "id": "different-final-rule"}, {"invalid": True}):
            with pytest.raises(jobs.JobGuardError, match="final_rule_mismatch"):
                await _call(
                    factory,
                    jobs.activate_web_rule_job,
                    claim=claim,
                    validation_id=receipt["execution_id"],
                    raw_rule=final_rule,
                )
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            job = await session.get(WebRuleJob, claim.job_id)
            assert not source.config.get("web_rule")
            assert job.status == "running" and job.activated_version is None
            assert job.validation_receipt["id"] == receipt["execution_id"]
            # Parsing has already populated optional defaults in the candidate.
            assert job.candidate_rule != RULE
        result = await _call(
            factory,
            jobs.activate_web_rule_job,
            claim=claim,
            validation_id=receipt["execution_id"],
            raw_rule=RULE,
        )
        assert result["status"] == "succeeded"


@pytest.mark.postgres
async def test_execution_read_progress_and_notes_survive_diagnostic_trim_and_new_lease():
    from app.web_rule_agent.context import RuleAgentContext

    now = datetime.now(UTC)
    async with _job_database() as (factory, source_id):
        first = await _start(
            factory, source_id, now=now, limits=jobs.JobLimits(diagnostic_bytes=4096)
        )
        report = {
            **PASS,
            "items": [
                {"title": f"Article {i}", "url": first.source_url + f"/{i}"} for i in range(19)
            ],
        }
        await _call(
            factory,
            jobs.save_web_rule_validation,
            claim=first,
            raw_rule=RULE,
            report=report,
            now=now,
        )
        note = {
            "version": 1,
            "findings": {"listing": {"finding": "Cards match the main listing"}},
            "read_progress": {report["execution_id"]: 10},
        }
        await _call(
            factory,
            jobs.record_web_rule_diagnostic,
            claim=first,
            phase="author",
            code="authoring_checkpoint",
            evidence=note,
            now=now,
        )
        for _ in range(8):
            await _call(
                factory,
                jobs.record_web_rule_diagnostic,
                claim=first,
                phase="tool",
                code="noise",
                evidence={"large": "x" * 5000},
                now=now,
            )
        await _call(
            factory,
            jobs.finish_web_rule_job,
            claim=first,
            status="retry_wait",
            code="interrupted",
            retry_after_seconds=1,
            now=now,
        )
        await _call(factory, jobs.release_web_rule_slot, claim=first)
        resumed = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=ENGINE,
            limits=jobs.JobLimits(),
            now=now + timedelta(seconds=2),
        )
        assert resumed.authoring_checkpoint == note
        assert resumed.last_feedback["items"] == report["items"]
        with pytest.raises(jobs.JobGuardError, match="lease_lost"):
            await _call(
                factory,
                jobs.save_web_rule_validation,
                claim=first,
                raw_rule=RULE,
                report=report,
                now=now + timedelta(seconds=2),
            )
        context = RuleAgentContext(
            claim=resumed, store=JobStore(factory), settings=Settings(_env_file=None)
        )
        # Context restoration itself preserves read position and never restores page refs.
        assert context.memory.read_progress[report["execution_id"]] == 10
        assert context.memory.read(report["execution_id"])["items"][10]["title"] == "Article 10"
        assert not context.observed_pages and context.receipt is None


@pytest.mark.postgres
async def test_partial_activation_retains_assessment_but_first_window_error_cannot_activate():
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id)
        error = {
            **PASS,
            "status": "error",
            "first_window_completed": False,
            "errors": [{"code": "web_http_503", "window": 0}],
        }
        await _call(
            factory, jobs.save_web_rule_validation, claim=claim, raw_rule=RULE, report=error
        )
        with pytest.raises(jobs.JobGuardError, match="execution_not_usable"):
            await _call(
                factory,
                jobs.activate_web_rule_job,
                claim=claim,
                validation_id=error["execution_id"],
            )
        partial = {
            **PASS,
            "status": "partial",
            "execution_id": "partial-first",
            "errors": [{"code": "web_http_503", "window": 1}],
        }
        await _call(
            factory, jobs.save_web_rule_validation, claim=claim, raw_rule=RULE, report=partial
        )
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            state.lease_token, state.lease_expires_at = (
                uuid4(),
                datetime.now(UTC) + timedelta(minutes=1),
            )
            await session.commit()
        accepted = await _call(
            factory,
            jobs.activate_web_rule_job,
            claim=claim,
            validation_id=partial["execution_id"],
            raw_rule=RULE,
            assessment="Current article titles and URLs match the DOM.",
            limitations=["History is limited."],
        )
        assert accepted["status"] == "activation_pending"
        refreshed = await _call(
            factory,
            jobs.save_web_rule_validation,
            claim=claim,
            raw_rule=RULE,
            report={**partial, "execution_id": "partial-refreshed"},
        )
        assert refreshed["limitations"] == ["History is limited."]
        async with factory() as session:
            state = await session.get(SourceSyncState, source_id)
            state.lease_token = state.lease_expires_at = None
            await session.commit()
        result = await _call(
            factory,
            jobs.activate_web_rule_job,
            claim=claim,
            validation_id=refreshed["execution_id"],
            raw_rule=RULE,
        )
        assert result["status"] == "succeeded"
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            version = source.config["web_rule_versions"][-1]["validation"]
            assert version["status"] == "partial" and version["limitations"] == [
                "History is limited."
            ]


@pytest.mark.postgres
async def test_changed_candidate_does_not_inherit_model_assessment_or_old_coverage():
    async with _job_database() as (factory, source_id):
        claim = await _start(factory, source_id)
        async with factory() as session:
            job = await session.get(WebRuleJob, claim.job_id)
            job.candidate_rule = RULE
            job.validation_report = {
                "assessment": "Old candidate",
                "limitations": ["Old scope"],
                "protected_coverage": {"limit_exceeded": True},
            }
            await session.commit()
        report = await _call(
            factory,
            jobs.save_web_rule_validation,
            claim=claim,
            raw_rule={**RULE, "id": "changed"},
            report=PASS,
        )
        assert not ({"assessment", "limitations", "protected_coverage"} & report.keys())


@pytest.mark.postgres
@pytest.mark.parametrize("base_recovers", [False, True])
async def test_repair_preflight_preserves_candidate_execution_across_actual_lease_restart(
    monkeypatch, base_recovers
):
    import httpx

    from app.web_rule_agent.context import RuleAgentContext

    candidate = {
        **RULE,
        "id": "candidate-B",
        "listing": {"extraction": {**RULE["listing"]["extraction"], "baseSelector": ".new"}},
    }
    page = {"tag": "section"}

    async def fetch(_client, url, **kwargs):
        body = (
            "User-agent: *\nAllow: /"
            if url.endswith("robots.txt")
            else "".join(
                f'<{page["tag"]} class="new"><a href="/article-{i}"><h2>Article {i}</h2></a>'
                f"</{page['tag']}>"
                for i in range(19)
            )
        )
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    async with _job_database() as (factory, source_id):
        async with factory() as session:
            source = await session.get(FeedSource, source_id)
            source.config = {"web_rule": RULE, "web_rule_revision": 4}
            state = await session.get(SourceSyncState, source_id)
            state.web_rule_structural_failures, state.web_rule_failure_episode_id = 2, uuid4()
            await session.commit()
        await _call(factory, jobs.ensure_web_rule_job, source_id=source_id, reason="repair")
        first = await _call(
            factory, jobs.claim_web_rule_job, engine_snapshot=ENGINE, limits=jobs.JobLimits()
        )
        context = RuleAgentContext(
            claim=first, store=JobStore(factory), settings=Settings(_env_file=None)
        )
        result = await context.execute(candidate)
        execution_id = result["execution_id"]
        await context.record_findings(
            "listing", "Candidate B matches the new list.", [execution_id]
        )
        await context.read_evidence(execution_id)
        async with factory() as session:
            job = await session.get(WebRuleJob, first.job_id)
            job.validation_report = {
                **job.validation_report,
                "assessment": "Candidate B checked.",
                "limitations": ["History unverified."],
            }
            await session.commit()

        async def restart(claim):
            await _call(
                factory,
                jobs.finish_web_rule_job,
                claim=claim,
                status="retry_wait",
                code="interrupted",
                retry_after_seconds=0,
            )
            await _call(factory, jobs.release_web_rule_slot, claim=claim)
            renewed = await _call(
                factory,
                jobs.claim_web_rule_job,
                engine_snapshot=ENGINE,
                limits=jobs.JobLimits(),
                now=datetime.now(UTC) + timedelta(seconds=2),
            )
            assert renewed is not None and renewed.lease_token != claim.lease_token
            return renewed

        second = await restart(first)
        resumed = RuleAgentContext(
            claim=second,
            store=JobStore(factory),
            settings=Settings(_env_file=None, web_rule_agent_max_tool_result_bytes=1024),
        )
        assert resumed.memory.read_progress[execution_id] == 10
        page["tag"] = "article" if base_recovers else "section"
        if base_recovers:
            # Rendering may omit completion fields; scheduling uses actual scanner facts.
            monkeypatch.setattr(
                resumed, "bound", lambda report, phase: {"status": "fail", "truncated": True}
            )

            def forbidden_model(*args, **kwargs):
                pytest.fail("Recovered base rule must not construct a model")

            await worker._execute_claim_inner(resumed, None, forbidden_model)
        else:
            preflight = await worker._preflight(resumed)
            assert preflight["status"] == "error" and not preflight["first_window_completed"]
        assert resumed.latest_validation["execution_id"] == execution_id
        assert resumed.memory.read(execution_id)["items"][10]["title"] == "Article 10"
        assert resumed.memory.read_progress[execution_id] == 10
        assert resumed.assessment == "Candidate B checked."
        assert resumed.limitations == ["History unverified."]
        async with factory() as session:
            job = await session.get(WebRuleJob, second.job_id)
            assert job.validation_report["execution_id"] == execution_id
            assert job.validation_report["assessment"] == "Candidate B checked."
            assert job.candidate_rule["id"] == "candidate-B"
            assert any(entry["phase"] == "preflight" for entry in job.diagnostics)
            if base_recovers:
                assert job.status == "cancelled" and job.last_error_code == "source_recovered"
                assert job.model_calls_reserved == 0
        if not base_recovers:
            third = await restart(second)
            again = RuleAgentContext(
                claim=third, store=JobStore(factory), settings=Settings(_env_file=None)
            )
            assert again.latest_candidate["id"] == "candidate-B"
            assert again.memory.read_progress[execution_id] == 10
            assert (await again.read_evidence(execution_id))["item_start"] == 10
            assert again.assessment == "Candidate B checked." and not again.observed_pages


@pytest.mark.postgres
async def test_cli_identity_is_worker_only_for_resume_pause_and_status():
    now = datetime.now(UTC)
    identity = {
        "binary_sha256": "cli",
        "runner_sha256": "runner",
        "cli_supported_version": "0.37.1",
    }
    complete = {
        **ENGINE,
        "cli_execution_identity": identity,
        "browser_execution_identity": {"binary_sha256": "lp"},
    }
    async with _job_database() as (factory, source_id):
        await _call(factory, jobs.ensure_web_rule_job, source_id=source_id, now=now)
        first = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=complete,
            limits=jobs.JobLimits(),
            now=now,
        )
        assert first.engine_snapshot == complete
        # Expired lease with identical artifacts resumes; old lease is fenced.
        resumed = await _call(
            factory,
            jobs.claim_web_rule_job,
            engine_snapshot=complete,
            limits=jobs.JobLimits(),
            now=now + timedelta(seconds=121),
        )
        assert resumed.job_id == first.job_id and resumed.lease_token != first.lease_token
        with pytest.raises(jobs.JobGuardError, match="lease_lost"):
            await _call(
                factory, jobs.check_web_rule_job, claim=first, now=now + timedelta(seconds=122)
            )
        await _call(
            factory,
            jobs.pause_web_rule_agent,
            engine_snapshot=complete,
            claim=resumed,
            code="model_http_402",
            now=now + timedelta(seconds=122),
        )
        assert (await _call(factory, jobs.web_rule_agent_status, engine_snapshot=ENGINE))["paused"]
        assert (await _call(factory, jobs.web_rule_agent_status, engine_snapshot=complete))[
            "paused"
        ]
        async with factory() as session:
            assert (await session.get(WebRuleAgentRuntime, "default")).engine_snapshot == ENGINE
