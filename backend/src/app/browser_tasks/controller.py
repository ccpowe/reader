"""Fenced lifecycle manager for isolated browser task containers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.browser_tasks.auth import (
    capability_hash,
    derive_task_capability,
    generate_capability_nonce,
)
from app.browser_tasks.docker_runtime import (
    DockerEngine,
    DockerEngineError,
    DockerNotFound,
    DockerResource,
    ImageIdentity,
)
from app.browser_tasks.protocol import (
    CreateTaskRequest,
    CreateTaskResponse,
    RuntimeDescriptorResponse,
    RuntimeIdentity,
    TaskPurpose,
    TaskState,
    TaskStatusResponse,
)
from app.browser_tasks.state import StateConflict, StateUnavailable, TaskRecord, TaskStateStore

LABEL_PREFIX = "io.reader.browser-task"
POLICY_SCHEMA = "v1"
IMAGE_LABEL_PREFIX = "io.reader.browser-runtime"
TASK_RESOURCE_KINDS = {"browser", "adapter", "control-volume", "init"}
TASK_RESOURCE_LABEL_FIELDS = {
    "managed",
    "schema",
    "deployment",
    "task",
    "owner",
    "purpose",
    "resource-kind",
    "created-at",
    "creation-deadline",
    "hard-deadline",
    "policy",
    "runtime",
    "image",
}


class BrowserTaskError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, retryable: bool = False, retry_after: int | None = None
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(frozen=True)
class BrowserTaskConfig:
    deployment_id: str
    browser_image_ref: str
    adapter_image_ref: str
    data_network: str
    egress_network: str
    adapter_port: int
    policy_version: str
    lightpanda_version: str
    lightpanda_sha256: str
    agent_browser_version: str
    agent_browser_sha256: str
    lease_seconds: int = 90
    lease_expiry_grace_seconds: int = 10
    creation_grace_seconds: int = 120
    explore_hard_ttl_seconds: int = 28_800
    render_hard_ttl_seconds: int = 900
    max_tasks: int = 2
    max_explore_tasks: int = 1
    startup_timeout_seconds: float = 30
    browser_uid: int = 999
    adapter_uid: int = 10_001
    shared_gid: int = 20_000
    browser_memory_bytes: int = 512 * 1024 * 1024
    adapter_memory_bytes: int = 256 * 1024 * 1024
    nano_cpus: int = 1_000_000_000
    pids_limit: int = 128

    def __post_init__(self) -> None:
        if self.max_tasks < 2 or self.max_explore_tasks >= self.max_tasks:
            raise ValueError("browser task capacity must reserve at least one render slot")
        if self.lease_expiry_grace_seconds >= self.lease_seconds:
            raise ValueError("lease grace must be shorter than the renewable lease")


@dataclass(frozen=True)
class ResolvedRuntime:
    browser: ImageIdentity
    adapter: ImageIdentity
    identity: RuntimeIdentity


def _utc_timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


async def _remove_container_confirmed(engine: DockerEngine, container_id: str) -> None:
    try:
        await engine.remove_container(container_id)
    except DockerNotFound:
        return
    try:
        await engine.inspect_container(container_id)
    except DockerNotFound:
        return
    raise DockerEngineError("container_cleanup_unconfirmed")


async def _remove_volume_confirmed(engine: DockerEngine, name: str) -> None:
    try:
        await engine.remove_volume(name)
    except DockerNotFound:
        return
    try:
        await engine.inspect_volume(name)
    except DockerNotFound:
        return
    raise DockerEngineError("volume_cleanup_unconfirmed")


async def cleanup_task_resources(
    engine: DockerEngine, config: BrowserTaskConfig, task_id: str
) -> str | None:
    try:
        resources = await engine.list_resources(
            labels={
                f"{LABEL_PREFIX}.managed": "true",
                f"{LABEL_PREFIX}.schema": POLICY_SCHEMA,
                f"{LABEL_PREFIX}.deployment": config.deployment_id,
                f"{LABEL_PREFIX}.task": task_id,
            }
        )
    except DockerEngineError:
        return "resource_discovery_failed"
    failures: list[str] = []
    safe_resources: list[DockerResource] = []
    for resource in resources:
        task_labels = {
            key.removeprefix(f"{LABEL_PREFIX}."): value
            for key, value in resource.labels.items()
            if key.startswith(f"{LABEL_PREFIX}.")
        }
        if (
            not TASK_RESOURCE_LABEL_FIELDS.issubset(task_labels)
            or task_labels.get("resource-kind") not in TASK_RESOURCE_KINDS
        ):
            failures.append(f"unsafe-labels:{resource.resource_id}")
            continue
        safe_resources.append(resource)
    for resource in safe_resources:
        if resource.resource_type != "container":
            continue
        try:
            await _remove_container_confirmed(engine, resource.resource_id)
        except DockerEngineError:
            failures.append(f"container:{resource.resource_id}")
    for resource in safe_resources:
        if resource.resource_type != "volume":
            continue
        try:
            await _remove_volume_confirmed(engine, resource.resource_id)
        except DockerEngineError:
            failures.append(f"volume:{resource.resource_id}")
    return "cleanup_unconfirmed" if failures else None


class BrowserTaskController:
    def __init__(
        self,
        *,
        config: BrowserTaskConfig,
        state: TaskStateStore,
        engine: DockerEngine,
        capability_key: bytes,
    ):
        self.config = config
        self.state = state
        self.engine = engine
        self.capability_key = capability_key
        self.runtime: ResolvedRuntime | None = None
        self._create_lock = asyncio.Lock()

    async def initialize(self) -> RuntimeDescriptorResponse:
        await asyncio.to_thread(self.state.initialize)
        await self.engine.initialize()
        browser, adapter = await asyncio.gather(
            self.engine.inspect_image(self.config.browser_image_ref),
            self.engine.inspect_image(self.config.adapter_image_ref),
        )
        self._validate_image_labels(browser, adapter)
        identity = RuntimeIdentity(
            policy_version=self.config.policy_version,
            browser_image_digest=browser.image_id,
            adapter_image_digest=adapter.image_id,
            lightpanda_version=self.config.lightpanda_version,
            lightpanda_sha256=self.config.lightpanda_sha256,
            agent_browser_version=self.config.agent_browser_version,
            agent_browser_sha256=self.config.agent_browser_sha256,
            config_fingerprint=self._config_fingerprint(browser, adapter),
            explore_hard_ttl_seconds=self.config.explore_hard_ttl_seconds,
            render_hard_ttl_seconds=self.config.render_hard_ttl_seconds,
        )
        self.runtime = ResolvedRuntime(browser, adapter, identity)
        return RuntimeDescriptorResponse(
            runtime_identity=identity,
            task_slots=self.config.max_tasks,
            max_explore_tasks=self.config.max_explore_tasks,
        )

    def _config_fingerprint(self, browser: ImageIdentity, adapter: ImageIdentity) -> str:
        values = {
            "protocol_version": "1",
            "policy_version": self.config.policy_version,
            "data_network": self.config.data_network,
            "egress_network": self.config.egress_network,
            "adapter_port": self.config.adapter_port,
            "browser_image_id": browser.image_id,
            "adapter_image_id": adapter.image_id,
            "adapter_runtime_profile": (
                "adapter-v1:reader-browser-adapter:reader-browser-adapter-healthcheck:"
                "/tmp/reader-crawl4ai:/run/reader-browser-task"
            ),
            "browser_uid": self.config.browser_uid,
            "adapter_uid": self.config.adapter_uid,
            "shared_gid": self.config.shared_gid,
            "browser_memory_bytes": self.config.browser_memory_bytes,
            "adapter_memory_bytes": self.config.adapter_memory_bytes,
            "nano_cpus": self.config.nano_cpus,
            "pids_limit": self.config.pids_limit,
            "max_tasks": self.config.max_tasks,
            "max_explore_tasks": self.config.max_explore_tasks,
            "lease_seconds": self.config.lease_seconds,
            "lease_expiry_grace_seconds": self.config.lease_expiry_grace_seconds,
            "creation_grace_seconds": self.config.creation_grace_seconds,
            "startup_timeout_seconds": self.config.startup_timeout_seconds,
            "explore_hard_ttl_seconds": self.config.explore_hard_ttl_seconds,
            "render_hard_ttl_seconds": self.config.render_hard_ttl_seconds,
        }
        canonical = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(canonical).hexdigest()

    def _validate_image_labels(self, browser: ImageIdentity, adapter: ImageIdentity) -> None:
        expected_browser = {
            f"{IMAGE_LABEL_PREFIX}.component": "browser",
            f"{IMAGE_LABEL_PREFIX}.protocol-version": "1",
            f"{IMAGE_LABEL_PREFIX}.lightpanda.version": self.config.lightpanda_version,
            f"{IMAGE_LABEL_PREFIX}.lightpanda.sha256": self.config.lightpanda_sha256,
            f"{IMAGE_LABEL_PREFIX}.agent-browser.version": self.config.agent_browser_version,
            f"{IMAGE_LABEL_PREFIX}.agent-browser.sha256": self.config.agent_browser_sha256,
        }
        expected_adapter = {
            f"{IMAGE_LABEL_PREFIX}.component": "adapter",
            f"{IMAGE_LABEL_PREFIX}.protocol-version": "1",
            f"{IMAGE_LABEL_PREFIX}.adapter-policy-version": self.config.policy_version,
        }
        if any(browser.labels.get(key) != value for key, value in expected_browser.items()):
            raise BrowserTaskError(
                "runtime_unavailable", "Browser image identity labels do not match policy."
            )
        if any(adapter.labels.get(key) != value for key, value in expected_adapter.items()):
            raise BrowserTaskError(
                "runtime_unavailable", "Adapter image identity labels do not match policy."
            )

    def descriptor(self) -> RuntimeDescriptorResponse:
        runtime = self._runtime()
        return RuntimeDescriptorResponse(
            runtime_identity=runtime.identity,
            task_slots=self.config.max_tasks,
            max_explore_tasks=self.config.max_explore_tasks,
        )

    def _runtime(self) -> ResolvedRuntime:
        if self.runtime is None:
            raise BrowserTaskError(
                "runtime_unavailable", "Browser runtime is not ready.", retryable=True
            )
        return self.runtime

    async def create(self, request: CreateTaskRequest) -> CreateTaskResponse:
        try:
            return await self._create(request)
        except BrowserTaskError:
            raise
        except (StateUnavailable, DockerEngineError) as exc:
            raise BrowserTaskError(
                "runtime_unavailable",
                "Browser task authority is unavailable.",
                retryable=True,
                retry_after=2,
            ) from exc

    async def _create(self, request: CreateTaskRequest) -> CreateTaskResponse:
        runtime = self._runtime()
        if request.expected_runtime_fingerprint != runtime.identity.fingerprint():
            raise BrowserTaskError("runtime_identity_mismatch", "Browser runtime identity changed.")
        fingerprint = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        async with self._create_lock:
            duplicate = await asyncio.to_thread(self.state.get_by_request, str(request.request_id))
            if duplicate is not None:
                if duplicate.request_fingerprint != fingerprint:
                    raise BrowserTaskError(
                        "request_conflict", "request_id was reused with new input"
                    )
                return self._create_response(duplicate)
            now = time.time()
            hard_ttl = (
                self.config.explore_hard_ttl_seconds
                if request.purpose == TaskPurpose.EXPLORE
                else self.config.render_hard_ttl_seconds
            )
            task_id = str(uuid4())
            nonce = generate_capability_nonce()
            owner_hash = hashlib.sha256(
                f"{self.config.deployment_id}\0{request.owner_job_id}".encode()
            ).hexdigest()
            record = TaskRecord(
                task_id=task_id,
                request_id=str(request.request_id),
                request_fingerprint=fingerprint,
                capability_nonce=nonce,
                owner_job_id=request.owner_job_id,
                owner_hash=owner_hash,
                purpose=request.purpose.value,
                source_url=str(request.source_url),
                expected_runtime_fingerprint=request.expected_runtime_fingerprint,
                state="creating",
                stage="reserved",
                version=0,
                created_at=now,
                creation_deadline=now + self.config.creation_grace_seconds,
                lease_expires_at=min(now + self.config.lease_seconds, now + hard_ttl),
                hard_expires_at=now + hard_ttl,
                updated_at=now,
                adapter_endpoint=None,
                runtime_identity=runtime.identity.model_dump(mode="json"),
                resources_released=False,
                cleanup_reason=None,
                last_error=None,
            )
            resources = await self.engine.list_resources(
                labels={
                    f"{LABEL_PREFIX}.managed": "true",
                    f"{LABEL_PREFIX}.schema": POLICY_SCHEMA,
                    f"{LABEL_PREFIX}.deployment": self.config.deployment_id,
                }
            )
            observed: dict[str, str] = {}
            for resource in resources:
                observed_task = resource.labels.get(f"{LABEL_PREFIX}.task")
                if not observed_task:
                    continue
                observed_purpose = resource.labels.get(f"{LABEL_PREFIX}.purpose", "")
                previous = observed.get(observed_task)
                observed[observed_task] = (
                    observed_purpose
                    if previous is None or previous == observed_purpose
                    else "unknown"
                )
            try:
                record, created = await asyncio.to_thread(
                    self.state.reserve,
                    record,
                    observed_tasks=observed,
                    max_tasks=self.config.max_tasks,
                    max_explore=self.config.max_explore_tasks,
                )
            except StateConflict as exc:
                if str(exc) == "capacity_exhausted":
                    raise BrowserTaskError(
                        "capacity_exhausted",
                        "Browser task capacity is busy.",
                        retryable=True,
                        retry_after=2,
                    ) from exc
                raise BrowserTaskError(
                    "request_conflict", "request_id conflicts with an existing task"
                ) from exc
            if not created:
                return self._create_response(record)
            try:
                record = await self._create_resources(record)
            except DockerEngineError as exc:
                await asyncio.shield(self._cleanup_after_create_failure(record.task_id))
                code = "startup_timeout" if "timeout" in exc.operation else "runtime_unavailable"
                raise BrowserTaskError(
                    code,
                    "Browser runtime could not start.",
                    retryable=True,
                    retry_after=2,
                ) from exc
            except (StateConflict, StateUnavailable) as exc:
                await asyncio.shield(self._cleanup_after_create_failure(record.task_id))
                raise BrowserTaskError(
                    "runtime_unavailable",
                    "Browser task creation lost its lifecycle claim.",
                    retryable=True,
                    retry_after=2,
                ) from exc
            except BaseException:
                await asyncio.shield(self._cleanup_after_create_failure(record.task_id))
                raise
            return self._create_response(record)

    async def _create_resources(self, record: TaskRecord) -> TaskRecord:
        runtime = self._runtime()
        capability = self._capability(record)
        cap_hash = capability_hash(capability)
        labels = self._base_labels(record)
        volume_name = f"reader-browser-task-{record.task_id}"
        init_id = browser_id = adapter_id = None
        record = await self._advance(record, "volume_creating")
        await self.engine.create_volume(
            name=volume_name,
            labels={
                **labels,
                f"{LABEL_PREFIX}.resource-kind": "control-volume",
                f"{LABEL_PREFIX}.image": "none",
            },
        )
        record = await self._advance(record, "volume_ready")
        try:
            record = await self._advance(record, "init_creating")
            init_id = await self.engine.create_container(
                name=f"reader-browser-init-{record.task_id}",
                image_id=runtime.adapter.image_id,
                labels={
                    **labels,
                    f"{LABEL_PREFIX}.resource-kind": "init",
                    f"{LABEL_PREFIX}.image": runtime.adapter.image_id,
                },
                spec=self._init_spec(volume_name),
            )
            await self.engine.start_container(init_id)
            if (
                await self.engine.wait_container(
                    init_id, timeout_seconds=self.config.startup_timeout_seconds
                )
                != 0
            ):
                raise DockerEngineError("init_failed")
            await _remove_container_confirmed(self.engine, init_id)
            init_id = None
            record = await self._advance(record, "volume_initialized")

            record = await self._advance(record, "browser_creating")
            browser_id = await self.engine.create_container(
                name=f"reader-browser-{record.task_id}",
                image_id=runtime.browser.image_id,
                labels={
                    **labels,
                    f"{LABEL_PREFIX}.resource-kind": "browser",
                    f"{LABEL_PREFIX}.image": runtime.browser.image_id,
                },
                spec=self._browser_spec(volume_name, record),
            )
            await self.engine.start_container(browser_id)
            await self.engine.wait_healthy(
                browser_id, timeout_seconds=self.config.startup_timeout_seconds
            )
            record = await self._advance(record, "browser_ready")

            alias = f"reader-browser-adapter-{record.task_id}"
            endpoint = f"http://{alias}:{self.config.adapter_port}"
            record = await self._advance(record, "adapter_creating")
            adapter_id = await self.engine.create_container(
                name=alias,
                image_id=runtime.adapter.image_id,
                labels={
                    **labels,
                    f"{LABEL_PREFIX}.resource-kind": "adapter",
                    f"{LABEL_PREFIX}.image": runtime.adapter.image_id,
                },
                spec=self._adapter_spec(volume_name, record, cap_hash, alias),
            )
            await self.engine.start_container(adapter_id)
            await self.engine.wait_healthy(
                adapter_id, timeout_seconds=self.config.startup_timeout_seconds
            )
            record = await self._advance(record, "adapter_ready")
            return await asyncio.to_thread(
                self.state.transition,
                record.task_id,
                expected_state="creating",
                expected_version=record.version,
                state="running",
                stage="running",
                adapter_endpoint=endpoint,
                runtime_identity=runtime.identity.model_dump(mode="json"),
                lease_expires_at=min(
                    time.time() + self.config.lease_seconds, record.hard_expires_at
                ),
                updated_at=time.time(),
            )
        except BaseException:
            # Local IDs are deliberately not the cleanup authority. Stable labels
            # recover a resource created immediately before a lost CAS response.
            raise

    async def _advance(self, record: TaskRecord, stage: str) -> TaskRecord:
        return await asyncio.to_thread(
            self.state.transition,
            record.task_id,
            expected_state="creating",
            expected_version=record.version,
            stage=stage,
            updated_at=time.time(),
        )

    async def renew(self, task_id: UUID, capability: str, request_id: UUID) -> TaskStatusResponse:
        record = await self._authorized_record(str(task_id), capability)
        try:
            record = await asyncio.to_thread(
                self.state.renew,
                record.task_id,
                request_id=str(request_id),
                now=time.time(),
                lease_seconds=self.config.lease_seconds,
            )
        except StateConflict as exc:
            raise BrowserTaskError(str(exc), "Browser task lease could not be renewed.") from exc
        except StateUnavailable as exc:
            raise BrowserTaskError(
                "runtime_unavailable", "Browser task state is unavailable.", retryable=True
            ) from exc
        return self._status_response(record)

    async def status(self, task_id: UUID, capability: str) -> TaskStatusResponse:
        return self._status_response(await self._authorized_record(str(task_id), capability))

    async def close(
        self, task_id: UUID, capability: str, reason: str, request_id: UUID
    ) -> TaskStatusResponse:
        record = await self._authorized_record(str(task_id), capability)
        if record.state == "closed" and record.resources_released:
            return self._status_response(record)
        if record.state == "cleaning":
            if record.last_close_request_id == str(request_id):
                return self._status_response(record)
            raise BrowserTaskError(
                "task_not_running", "Browser task cleanup is already in progress.", retryable=True
            )
        try:
            claimed = await asyncio.to_thread(
                self.state.claim_cleaning,
                record,
                reason=reason,
                now=time.time(),
                request_id=str(request_id),
            )
        except StateConflict as exc:
            latest = await asyncio.to_thread(self.state.get, record.task_id)
            if latest and latest.state == "closed" and latest.resources_released:
                return self._status_response(latest)
            raise BrowserTaskError(
                "task_not_running", "Browser task changed while closing.", retryable=True
            ) from exc
        except StateUnavailable as exc:
            raise BrowserTaskError(
                "runtime_unavailable", "Browser task state is unavailable.", retryable=True
            ) from exc
        error = await self.cleanup_resources(claimed.task_id)
        try:
            finished = await asyncio.to_thread(
                self.state.finish_cleanup, claimed, error=error, now=time.time()
            )
        except StateConflict as exc:
            latest = await asyncio.to_thread(self.state.get, record.task_id)
            if latest and latest.state == "closed" and latest.resources_released:
                return self._status_response(latest)
            raise BrowserTaskError(
                "runtime_unavailable", "Browser cleanup result changed.", retryable=True
            ) from exc
        except StateUnavailable as exc:
            raise BrowserTaskError(
                "runtime_unavailable", "Browser task state is unavailable.", retryable=True
            ) from exc
        if error:
            raise BrowserTaskError(
                "cleanup_failed", "Browser task cleanup must be retried.", retryable=True
            )
        return self._status_response(finished)

    async def _authorized_record(self, task_id: str, capability: str) -> TaskRecord:
        try:
            record = await asyncio.to_thread(self.state.get, task_id)
        except StateUnavailable as exc:
            raise BrowserTaskError(
                "runtime_unavailable", "Browser task state is unavailable.", retryable=True
            ) from exc
        if record is None:
            raise BrowserTaskError("task_not_found", "Browser task does not exist.")
        if not hmac.compare_digest(capability, self._capability(record)):
            raise BrowserTaskError("unauthorized", "Task capability is invalid.")
        return record

    async def _cleanup_after_create_failure(self, task_id: str) -> None:
        try:
            record = await asyncio.to_thread(self.state.get, task_id)
            if record is None:
                return
            if record.state != "cleaning":
                record = await asyncio.to_thread(
                    self.state.claim_cleaning,
                    record,
                    reason="startup_failed",
                    now=time.time(),
                )
            error = await self.cleanup_resources(task_id)
            await asyncio.to_thread(self.state.finish_cleanup, record, error=error, now=time.time())
        except (StateConflict, StateUnavailable):
            # The independent reaper owns any claim we lost.
            return

    async def cleanup_resources(self, task_id: str) -> str | None:
        return await cleanup_task_resources(self.engine, self.config, task_id)

    def _capability(self, record: TaskRecord) -> str:
        return derive_task_capability(
            self.capability_key,
            task_id=record.task_id,
            request_id=record.request_id,
            owner_job_id=record.owner_job_id,
            capability_nonce=record.capability_nonce,
        )

    def _create_response(self, record: TaskRecord) -> CreateTaskResponse:
        if record.state != "running" or not record.adapter_endpoint or not record.runtime_identity:
            raise BrowserTaskError(
                "runtime_unavailable", "Browser task creation is still recovering.", retryable=True
            )
        return CreateTaskResponse(
            task_id=UUID(record.task_id),
            state=TaskState.RUNNING,
            capability=self._capability(record),
            adapter_endpoint=record.adapter_endpoint,
            lease_expires_at=_utc_timestamp(record.lease_expires_at),
            hard_expires_at=_utc_timestamp(record.hard_expires_at),
            runtime_identity=RuntimeIdentity.model_validate(record.runtime_identity),
        )

    def _status_response(self, record: TaskRecord) -> TaskStatusResponse:
        state_map = {
            "creating": TaskState.CREATING,
            "running": TaskState.RUNNING,
            "cleaning": TaskState.CLEANING,
            "cleanup_failed": getattr(TaskState, "CLEANUP_FAILED", TaskState.FAILED),
            "closed": TaskState.CLOSED,
            "failed": TaskState.FAILED,
        }
        return TaskStatusResponse(
            task_id=UUID(record.task_id),
            purpose=TaskPurpose(record.purpose),
            state=state_map[record.state],
            lease_expires_at=_utc_timestamp(record.lease_expires_at),
            hard_expires_at=_utc_timestamp(record.hard_expires_at),
            runtime_identity=RuntimeIdentity.model_validate(record.runtime_identity),
            cleanup_error=record.last_error,
        )

    def _base_labels(self, record: TaskRecord) -> dict[str, str]:
        runtime = self._runtime()
        return {
            f"{LABEL_PREFIX}.managed": "true",
            f"{LABEL_PREFIX}.schema": POLICY_SCHEMA,
            f"{LABEL_PREFIX}.deployment": self.config.deployment_id,
            f"{LABEL_PREFIX}.task": record.task_id,
            f"{LABEL_PREFIX}.owner": record.owner_hash,
            f"{LABEL_PREFIX}.purpose": record.purpose,
            f"{LABEL_PREFIX}.created-at": str(int(record.created_at)),
            f"{LABEL_PREFIX}.creation-deadline": str(int(record.creation_deadline)),
            f"{LABEL_PREFIX}.hard-deadline": str(int(record.hard_expires_at)),
            f"{LABEL_PREFIX}.policy": self.config.policy_version,
            f"{LABEL_PREFIX}.runtime": runtime.identity.fingerprint(),
        }

    def _host_security(self, *, memory: int, network_mode: str, volume_name: str) -> dict[str, Any]:
        return {
            "NetworkMode": network_mode,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Memory": memory,
            "NanoCpus": self.config.nano_cpus,
            "PidsLimit": self.config.pids_limit,
            "Mounts": [
                {
                    "Type": "volume",
                    "Source": volume_name,
                    "Target": "/run/reader-browser-task",
                    "ReadOnly": False,
                }
            ],
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=16777216"},
        }

    def _init_spec(self, volume_name: str) -> dict[str, Any]:
        host = self._host_security(
            memory=64 * 1024 * 1024, network_mode="none", volume_name=volume_name
        )
        host["CapAdd"] = ["CHOWN"]
        return {
            "User": "0:0",
            "Entrypoint": ["/reader/init-browser-task-volume"],
            "Cmd": [
                "--browser-uid",
                str(self.config.browser_uid),
                "--adapter-uid",
                str(self.config.adapter_uid),
                "--shared-gid",
                str(self.config.shared_gid),
            ],
            "HostConfig": host,
        }

    def _browser_spec(self, volume_name: str, record: TaskRecord) -> dict[str, Any]:
        return {
            "User": f"{self.config.browser_uid}:{self.config.shared_gid}",
            "Env": [
                f"READER_BROWSER_TASK_ID={record.task_id}",
                f"READER_BROWSER_TASK_PURPOSE={record.purpose}",
            ],
            "HostConfig": self._host_security(
                memory=self.config.browser_memory_bytes,
                network_mode="none",
                volume_name=volume_name,
            ),
        }

    def _adapter_spec(
        self, volume_name: str, record: TaskRecord, expected_capability_hash: str, alias: str
    ) -> dict[str, Any]:
        host = self._host_security(
            memory=self.config.adapter_memory_bytes,
            network_mode=self.config.data_network,
            volume_name=volume_name,
        )
        return {
            "User": f"{self.config.adapter_uid}:{self.config.shared_gid}",
            "Entrypoint": ["reader-browser-adapter"],
            "Cmd": [],
            "Env": [
                f"READER_BROWSER_TASK_ID={record.task_id}",
                f"READER_BROWSER_CAPABILITY_SHA256={expected_capability_hash}",
                f"READER_BROWSER_TASK_PURPOSE={record.purpose}",
                f"READER_BROWSER_SOURCE_URL={record.source_url}",
                "CRAWL4_AI_BASE_DIRECTORY=/tmp/reader-crawl4ai",
                "READER_BROWSER_RUNTIME_IDENTITY="
                + json.dumps(
                    self._runtime().identity.model_dump(mode="json"), separators=(",", ":")
                ),
            ],
            "ExposedPorts": {f"{self.config.adapter_port}/tcp": {}},
            "Healthcheck": {
                "Test": ["CMD", "reader-browser-adapter-healthcheck"],
                "Interval": 1_000_000_000,
                "Timeout": 2_000_000_000,
                "Retries": 30,
                "StartPeriod": 2_000_000_000,
            },
            "HostConfig": host,
            "NetworkingConfig": {
                "EndpointsConfig": {
                    self.config.data_network: {"Aliases": [alias]},
                    self.config.egress_network: {},
                }
            },
        }
