from __future__ import annotations

from dataclasses import replace

import pytest

from app.browser_tasks.controller import LABEL_PREFIX, BrowserTaskConfig
from app.browser_tasks.docker_runtime import DockerNotFound, DockerResource
from app.browser_tasks.reaper import BrowserTaskReaper, ReaperConfig
from app.browser_tasks.state import StateUnavailable, TaskRecord, TaskStateStore


def config() -> BrowserTaskConfig:
    return BrowserTaskConfig(
        deployment_id="deployment-one",
        browser_image_ref="reader-browser:test",
        adapter_image_ref="reader-adapter:test",
        data_network="reader-data",
        egress_network="reader-egress",
        adapter_port=8092,
        policy_version="policy-one",
        lightpanda_version="0.4.0",
        lightpanda_sha256="c" * 64,
        agent_browser_version="0.37.1",
        agent_browser_sha256="d" * 64,
    )


def record(*, task_id: str, request_id: str, purpose: str) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        request_id=request_id,
        request_fingerprint=f"fingerprint-{request_id}",
        capability_nonce="A" * 43,
        owner_job_id="job-1",
        owner_hash="a" * 64,
        purpose=purpose,
        source_url="https://example.com/",
        expected_runtime_fingerprint="b" * 64,
        state="creating",
        stage="reserved",
        version=0,
        created_at=0,
        creation_deadline=0,
        lease_expires_at=0,
        hard_expires_at=0,
        updated_at=0,
        adapter_endpoint=None,
        runtime_identity={"protocol_version": "1"},
        resources_released=False,
        cleanup_reason=None,
        last_error=None,
    )


class ReaperEngine:
    def __init__(self, resources: list[DockerResource]):
        self.resources = {resource.resource_id: resource for resource in resources}
        self.removed: list[str] = []

    async def list_resources(self, *, labels: dict[str, str]) -> list[DockerResource]:
        return [
            resource
            for resource in self.resources.values()
            if all(resource.labels.get(key) == value for key, value in labels.items())
        ]

    async def remove_container(self, container_id: str) -> None:
        if self.resources.pop(container_id, None) is None:
            raise DockerNotFound("remove", status_code=404)
        self.removed.append(container_id)

    async def inspect_container(self, container_id: str) -> DockerResource:
        try:
            return self.resources[container_id]
        except KeyError as exc:
            raise DockerNotFound("inspect", status_code=404) from exc

    async def remove_volume(self, name: str) -> None:
        if self.resources.pop(name, None) is None:
            raise DockerNotFound("remove", status_code=404)
        self.removed.append(name)

    async def inspect_volume(self, name: str) -> DockerResource:
        try:
            return self.resources[name]
        except KeyError as exc:
            raise DockerNotFound("inspect", status_code=404) from exc


class UnavailableState:
    def list_records(self):
        raise StateUnavailable("offline")


def labels(task_id: str, kind: str, *, created: float, creation: float, hard: float):
    return {
        f"{LABEL_PREFIX}.managed": "true",
        f"{LABEL_PREFIX}.schema": "v1",
        f"{LABEL_PREFIX}.deployment": "deployment-one",
        f"{LABEL_PREFIX}.task": task_id,
        f"{LABEL_PREFIX}.owner": f"owner-{task_id}",
        f"{LABEL_PREFIX}.purpose": "render",
        f"{LABEL_PREFIX}.resource-kind": kind,
        f"{LABEL_PREFIX}.created-at": str(created),
        f"{LABEL_PREFIX}.creation-deadline": str(creation),
        f"{LABEL_PREFIX}.hard-deadline": str(hard),
        f"{LABEL_PREFIX}.policy": "policy-one",
        f"{LABEL_PREFIX}.runtime": "f" * 64,
        f"{LABEL_PREFIX}.image": "none" if kind == "control-volume" else "sha256:" + "a" * 64,
    }


def group(task_id: str, *, now: float, adapter_running: bool = True):
    common = {"created": now - 100, "creation": now - 50, "hard": now + 500}
    return [
        DockerResource(
            "container", f"{task_id}-browser", labels(task_id, "browser", **common), True
        ),
        DockerResource(
            "container",
            f"{task_id}-adapter",
            labels(task_id, "adapter", **common),
            adapter_running,
        ),
        DockerResource("volume", f"{task_id}-volume", labels(task_id, "control-volume", **common)),
    ]


def running_store(tmp_path, *, task_id: str, now: float, lease: float):
    value = TaskStateStore(tmp_path / "tasks.sqlite3")
    value.initialize()
    initial = replace(
        record(task_id=task_id, request_id=f"request-{task_id}", purpose="render"),
        created_at=now - 100,
        creation_deadline=now - 50,
        lease_expires_at=lease,
        hard_expires_at=now + 500,
        updated_at=now - 100,
    )
    initial, _ = value.reserve(initial, observed_tasks={}, max_tasks=2, max_explore=1)
    return value, value.transition(
        task_id,
        expected_state="creating",
        expected_version=initial.version,
        state="running",
        stage="running",
        adapter_endpoint="http://adapter:8092",
        updated_at=now - 100,
    )


@pytest.mark.asyncio
async def test_reaper_cleans_expired_group_without_touching_active_or_decoy(tmp_path):
    now = 10_000.0
    state, expired = running_store(tmp_path, task_id="expired", now=now, lease=now - 20)
    active_initial = replace(
        record(task_id="active", request_id="request-active", purpose="render"),
        created_at=now - 100,
        creation_deadline=now - 50,
        lease_expires_at=now + 100,
        hard_expires_at=now + 500,
        updated_at=now - 100,
    )
    active_initial, _ = state.reserve(active_initial, observed_tasks={}, max_tasks=2, max_explore=1)
    state.transition(
        "active",
        expected_state="creating",
        expected_version=active_initial.version,
        state="running",
        stage="running",
        adapter_endpoint="http://active:8092",
        updated_at=now - 100,
    )
    decoy_labels = labels("decoy", "browser", created=now - 100, creation=now - 50, hard=now - 1)
    decoy_labels.pop(f"{LABEL_PREFIX}.owner")
    engine = ReaperEngine(
        group("expired", now=now)
        + group("active", now=now)
        + [DockerResource("container", "unmanaged-decoy", decoy_labels, False)]
    )
    reaper = BrowserTaskReaper(
        task_config=config(),
        reaper_config=ReaperConfig(),
        state=state,
        engine=engine,  # type: ignore[arg-type]
    )

    await reaper.run_once(now=now)

    assert state.get(expired.task_id).state == "closed"
    assert all(not item.startswith("expired-") for item in engine.resources)
    assert all(
        item in engine.resources for item in ("active-browser", "active-adapter", "active-volume")
    )
    assert "unmanaged-decoy" in engine.resources


@pytest.mark.asyncio
async def test_reaper_cleanup_claim_loses_to_renew_and_leaves_resources(tmp_path):
    now = 20_000.0
    state, _running = running_store(tmp_path, task_id="race", now=now, lease=now - 20)
    original_claim = state.claim_cleaning

    def renew_before_claim(stale, *, reason, now):
        state.renew(stale.task_id, request_id="race-renew", now=now - 21, lease_seconds=90)
        return original_claim(stale, reason=reason, now=now)

    state.claim_cleaning = renew_before_claim  # type: ignore[method-assign]
    engine = ReaperEngine(group("race", now=now))
    reaper = BrowserTaskReaper(
        task_config=config(),
        reaper_config=ReaperConfig(),
        state=state,
        engine=engine,  # type: ignore[arg-type]
    )

    await reaper.run_once(now=now)

    assert state.get("race").state == "running"
    assert set(engine.resources) == {"race-browser", "race-adapter", "race-volume"}


@pytest.mark.asyncio
async def test_reaper_without_database_uses_only_immutable_failure_fences():
    now = 30_000.0
    active = group("active", now=now)
    expired = group("expired", now=now)
    for resource in expired:
        resource.labels[f"{LABEL_PREFIX}.hard-deadline"] = str(now - 1)
    partial_labels = labels(
        "partial", "control-volume", created=now - 100, creation=now - 1, hard=now + 500
    )
    decoy_labels = labels("decoy", "browser", created=now - 100, creation=now - 1, hard=now - 1)
    decoy_labels.pop(f"{LABEL_PREFIX}.policy")
    engine = ReaperEngine(
        active
        + expired
        + [
            DockerResource("volume", "partial-volume", partial_labels),
            DockerResource("container", "decoy-browser", decoy_labels, False),
        ]
    )
    reaper = BrowserTaskReaper(
        task_config=config(),
        reaper_config=ReaperConfig(),
        state=UnavailableState(),  # type: ignore[arg-type]
        engine=engine,  # type: ignore[arg-type]
    )

    await reaper.run_once(now=now)

    assert all(
        item in engine.resources for item in ("active-browser", "active-adapter", "active-volume")
    )
    assert all(not item.startswith("expired-") for item in engine.resources)
    assert "partial-volume" not in engine.resources
    assert "decoy-browser" in engine.resources


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "status", "exit_code", "cleaned"),
    [
        ("init", "exited", 0, False),
        ("browser", "created", 0, False),
        ("init", "exited", 1, True),
        ("browser", "exited", 0, True),
    ],
)
async def test_reaper_distinguishes_normal_create_windows_from_failures(
    tmp_path, kind, status, exit_code, cleaned
):
    now = 40_000.0
    state = TaskStateStore(tmp_path / "tasks.sqlite3")
    state.initialize()
    initial = replace(
        record(task_id="creating", request_id="request-creating", purpose="render"),
        created_at=now - 5,
        creation_deadline=now + 100,
        lease_expires_at=now + 90,
        hard_expires_at=now + 500,
        updated_at=now - 5,
    )
    state.reserve(initial, observed_tasks={}, max_tasks=2, max_explore=1)
    common = {"created": now - 5, "creation": now + 100, "hard": now + 500}
    engine = ReaperEngine(
        [
            DockerResource(
                "container",
                f"creating-{kind}",
                labels("creating", kind, **common),
                running=status == "running",
                exit_code=exit_code,
                status=status,
            ),
            DockerResource(
                "volume",
                "creating-volume",
                labels("creating", "control-volume", **common),
            ),
        ]
    )
    reaper = BrowserTaskReaper(
        task_config=config(),
        reaper_config=ReaperConfig(),
        state=state,
        engine=engine,  # type: ignore[arg-type]
    )

    await reaper.run_once(now=now)

    assert (not engine.resources) is cleaned
    assert state.get("creating").state == ("closed" if cleaned else "creating")
