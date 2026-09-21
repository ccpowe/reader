"""Independent reconciler for browser tasks whose owner or manager disappeared."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from app.browser_tasks.controller import (
    LABEL_PREFIX,
    POLICY_SCHEMA,
    TASK_RESOURCE_KINDS,
    TASK_RESOURCE_LABEL_FIELDS,
    BrowserTaskConfig,
    cleanup_task_resources,
)
from app.browser_tasks.docker_runtime import DockerEngine, DockerEngineError, DockerResource
from app.browser_tasks.state import StateConflict, StateUnavailable, TaskRecord, TaskStateStore

logger = logging.getLogger(__name__)

_RUNNING_KINDS = {"browser", "adapter", "control-volume"}


@dataclass(frozen=True)
class ReaperConfig:
    interval_seconds: float = 10
    cleanup_claim_grace_seconds: float = 30
    orphan_grace_seconds: float = 30


class BrowserTaskReaper:
    def __init__(
        self,
        *,
        task_config: BrowserTaskConfig,
        reaper_config: ReaperConfig,
        state: TaskStateStore,
        engine: DockerEngine,
    ):
        self.task_config = task_config
        self.reaper_config = reaper_config
        self.state = state
        self.engine = engine

    async def run_once(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        try:
            resources = await self.engine.list_resources(
                labels={
                    f"{LABEL_PREFIX}.managed": "true",
                    f"{LABEL_PREFIX}.schema": POLICY_SCHEMA,
                    f"{LABEL_PREFIX}.deployment": self.task_config.deployment_id,
                }
            )
        except DockerEngineError:
            logger.exception("browser task reaper could not list Docker resources")
            return
        groups = self._valid_groups(resources)
        try:
            records = {
                record.task_id: record
                for record in await asyncio.to_thread(self.state.list_records)
            }
        except StateUnavailable:
            await self._reap_without_state(groups, now=now)
            return
        for task_id, resources_for_task in groups.items():
            record = records.get(task_id)
            if record is None:
                created_at = self._label_time(resources_for_task, "created-at")
                if (
                    created_at is not None
                    and now >= created_at + self.reaper_config.orphan_grace_seconds
                ):
                    await self._cleanup_orphan(task_id)
                continue
            hydrated = await self._hydrate_containers(resources_for_task)
            if hydrated is None:
                continue
            resources_for_task = hydrated
            reason = self._cleanup_reason(record, resources_for_task, now=now)
            if reason is not None:
                await self._claim_and_cleanup(record, reason=reason, now=now)
        for task_id, record in records.items():
            if task_id in groups:
                continue
            if record.state in {"closed", "failed"} and record.resources_released:
                continue
            reason = self._cleanup_reason(record, [], now=now)
            if reason is not None:
                await self._claim_and_cleanup(record, reason=reason, now=now)

    async def run_forever(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.reaper_config.interval_seconds)

    def _cleanup_reason(
        self, record: TaskRecord, resources: list[DockerResource], *, now: float
    ) -> str | None:
        kinds = {resource.labels[f"{LABEL_PREFIX}.resource-kind"] for resource in resources}
        failed_container = self._has_failed_container(resources)
        if record.state == "creating":
            if failed_container:
                return "resource_exited"
            return "creation_expired" if now >= record.creation_deadline else None
        if record.state == "running":
            if now >= record.hard_expires_at:
                return "hard_expired"
            if now >= record.lease_expires_at + self.task_config.lease_expiry_grace_seconds:
                return "lease_expired"
            if failed_container:
                return "resource_exited"
            if any(resource.health == "unhealthy" for resource in resources):
                return "resource_unhealthy"
            if kinds != _RUNNING_KINDS:
                return "resource_set_changed"
            return None
        if record.state == "cleaning":
            if now >= record.updated_at + self.reaper_config.cleanup_claim_grace_seconds:
                return record.cleanup_reason or "cleanup_claim_expired"
            return None
        if record.state == "cleanup_failed":
            return record.cleanup_reason or "cleanup_retry"
        if resources:
            return "terminal_residual"
        if not record.resources_released:
            return "terminal_unconfirmed"
        return None

    def _has_failed_container(self, resources: list[DockerResource]) -> bool:
        for resource in resources:
            if resource.resource_type != "container" or resource.status not in {"exited", "dead"}:
                continue
            kind = resource.labels.get(f"{LABEL_PREFIX}.resource-kind")
            if kind != "init" or resource.exit_code not in (0, None):
                return True
        return False

    async def _hydrate_containers(
        self, resources: list[DockerResource]
    ) -> list[DockerResource] | None:
        hydrated: list[DockerResource] = []
        try:
            for resource in resources:
                if resource.resource_type == "container":
                    hydrated.append(await self.engine.inspect_container(resource.resource_id))
                else:
                    hydrated.append(resource)
        except DockerEngineError:
            logger.exception("browser task reaper could not inspect a task resource")
            return None
        return hydrated

    async def _claim_and_cleanup(self, record: TaskRecord, *, reason: str, now: float) -> None:
        try:
            claimed = await asyncio.to_thread(
                self.state.claim_cleaning, record, reason=reason, now=now
            )
        except (StateConflict, StateUnavailable):
            return
        error = await cleanup_task_resources(self.engine, self.task_config, record.task_id)
        try:
            await asyncio.to_thread(
                self.state.finish_cleanup, claimed, error=error, now=time.time()
            )
        except (StateConflict, StateUnavailable):
            return

    async def _cleanup_orphan(self, task_id: str) -> None:
        error = await cleanup_task_resources(self.engine, self.task_config, task_id)
        if error:
            logger.warning("browser task orphan cleanup failed: task_id=%s", task_id)

    async def _reap_without_state(
        self, groups: dict[str, list[DockerResource]], *, now: float
    ) -> None:
        """Use only immutable label facts while SQLite authority is unavailable."""
        for task_id, resources in groups.items():
            hydrated = await self._hydrate_containers(resources)
            if hydrated is None:
                continue
            resources = hydrated
            kinds = {resource.labels[f"{LABEL_PREFIX}.resource-kind"] for resource in resources}
            hard_deadline = self._label_time(resources, "hard-deadline")
            creation_deadline = self._label_time(resources, "creation-deadline")
            failed_container = self._has_failed_container(resources)
            should_clean = failed_container or (hard_deadline is not None and now >= hard_deadline)
            if (
                creation_deadline is not None
                and now >= creation_deadline
                and kinds != _RUNNING_KINDS
            ):
                should_clean = True
            if should_clean:
                await self._cleanup_orphan(task_id)

    def _valid_groups(self, resources: list[DockerResource]) -> dict[str, list[DockerResource]]:
        grouped: dict[str, list[DockerResource]] = defaultdict(list)
        for resource in resources:
            labels = self._labels(resource)
            if labels is None:
                continue
            grouped[labels["task"]].append(resource)
        valid: dict[str, list[DockerResource]] = {}
        for task_id, group in grouped.items():
            label_sets = [self._labels(resource) for resource in group]
            if any(labels is None for labels in label_sets):
                continue
            assert all(labels is not None for labels in label_sets)
            stable_fields = (
                "task",
                "owner",
                "purpose",
                "created-at",
                "creation-deadline",
                "hard-deadline",
                "policy",
                "runtime",
            )
            if any(
                any(labels[field] != label_sets[0][field] for field in stable_fields)
                for labels in label_sets
            ):
                continue
            kinds = [labels["resource-kind"] for labels in label_sets]
            if len(kinds) != len(set(kinds)) or any(
                kind not in TASK_RESOURCE_KINDS for kind in kinds
            ):
                continue
            valid[task_id] = group
        return valid

    def _labels(self, resource: DockerResource) -> dict[str, str] | None:
        labels = {
            key.removeprefix(f"{LABEL_PREFIX}."): value
            for key, value in resource.labels.items()
            if key.startswith(f"{LABEL_PREFIX}.")
        }
        if not TASK_RESOURCE_LABEL_FIELDS.issubset(labels):
            return None
        if (
            labels["managed"] != "true"
            or labels["schema"] != POLICY_SCHEMA
            or labels["deployment"] != self.task_config.deployment_id
        ):
            return None
        try:
            float(labels["created-at"])
            float(labels["creation-deadline"])
            float(labels["hard-deadline"])
        except ValueError:
            return None
        return labels

    def _label_time(self, resources: list[DockerResource], field: str) -> float | None:
        values = {
            labels[field]
            for resource in resources
            if (labels := self._labels(resource)) is not None
        }
        if len(values) != 1:
            return None
        return float(values.pop())


def main() -> None:
    from app.browser_tasks.manager_app import load_task_config

    async def run() -> None:
        task_config, state_dir, docker_socket = load_task_config()
        state = TaskStateStore(state_dir / "tasks.sqlite3")
        engine = DockerEngine(docker_socket)
        try:
            await asyncio.to_thread(state.initialize)
        except StateUnavailable:
            logger.warning("browser task reaper started without SQLite authority")
        await engine.initialize()
        try:
            reaper = BrowserTaskReaper(
                task_config=task_config,
                reaper_config=ReaperConfig(),
                state=state,
                engine=engine,
            )
            await reaper.run_forever()
        finally:
            await engine.aclose()

    asyncio.run(run())
