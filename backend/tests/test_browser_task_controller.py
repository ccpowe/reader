from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.browser_tasks.controller import (
    IMAGE_LABEL_PREFIX,
    BrowserTaskConfig,
    BrowserTaskController,
    BrowserTaskError,
)
from app.browser_tasks.docker_runtime import (
    DockerEngineError,
    DockerNotFound,
    DockerResource,
    ImageIdentity,
)
from app.browser_tasks.protocol import CreateTaskRequest, TaskPurpose
from app.browser_tasks.state import TaskStateStore

IMAGE_ID = "sha256:" + "a" * 64
ADAPTER_IMAGE_ID = "sha256:" + "b" * 64


class FakeEngine:
    def __init__(self, *, fail_health_kind: str | None = None, fail_remove_once: bool = False):
        self.fail_health_kind = fail_health_kind
        self.fail_remove_once = fail_remove_once
        self.resources: dict[str, DockerResource] = {}
        self.specs: dict[str, dict] = {}
        self.created_order: list[str] = []

    async def initialize(self) -> str:
        return "1.52"

    async def inspect_image(self, reference: str) -> ImageIdentity:
        image_id = IMAGE_ID if "browser" in reference else ADAPTER_IMAGE_ID
        if "browser" in reference:
            labels = {
                f"{IMAGE_LABEL_PREFIX}.component": "browser",
                f"{IMAGE_LABEL_PREFIX}.protocol-version": "1",
                f"{IMAGE_LABEL_PREFIX}.lightpanda.version": "0.4.0",
                f"{IMAGE_LABEL_PREFIX}.lightpanda.sha256": "c" * 64,
                f"{IMAGE_LABEL_PREFIX}.agent-browser.version": "0.37.1",
                f"{IMAGE_LABEL_PREFIX}.agent-browser.sha256": "d" * 64,
            }
        else:
            labels = {
                f"{IMAGE_LABEL_PREFIX}.component": "adapter",
                f"{IMAGE_LABEL_PREFIX}.protocol-version": "1",
                f"{IMAGE_LABEL_PREFIX}.adapter-policy-version": "policy-one",
            }
        return ImageIdentity(reference, image_id, labels)

    async def list_resources(self, *, labels: dict[str, str]) -> list[DockerResource]:
        return [
            resource
            for resource in self.resources.values()
            if all(resource.labels.get(key) == value for key, value in labels.items())
        ]

    async def create_volume(self, *, name: str, labels: dict[str, str]) -> str:
        self.resources[name] = DockerResource("volume", name, labels)
        self.created_order.append("control-volume")
        return name

    async def create_container(
        self, *, name: str, image_id: str, labels: dict[str, str], spec: dict
    ) -> str:
        kind = labels["io.reader.browser-task.resource-kind"]
        self.resources[name] = DockerResource("container", name, labels, running=False)
        self.specs[kind] = {**spec, "Image": image_id}
        self.created_order.append(kind)
        return name

    async def start_container(self, container_id: str) -> None:
        resource = self.resources[container_id]
        self.resources[container_id] = DockerResource(
            "container", container_id, resource.labels, running=True, health="starting"
        )

    async def wait_container(self, container_id: str, *, timeout_seconds: float) -> int:
        return 0

    async def wait_healthy(self, container_id: str, *, timeout_seconds: float) -> DockerResource:
        kind = self.resources[container_id].labels["io.reader.browser-task.resource-kind"]
        if kind == self.fail_health_kind:
            raise DockerEngineError("container_health_timeout")
        resource = self.resources[container_id]
        healthy = DockerResource(
            "container", container_id, resource.labels, running=True, health="healthy"
        )
        self.resources[container_id] = healthy
        return healthy

    async def remove_container(self, container_id: str) -> None:
        if self.fail_remove_once:
            self.fail_remove_once = False
            raise DockerEngineError("remove_container")
        if self.resources.pop(container_id, None) is None:
            raise DockerNotFound("remove", status_code=404)

    async def inspect_container(self, container_id: str) -> DockerResource:
        try:
            return self.resources[container_id]
        except KeyError as exc:
            raise DockerNotFound("inspect", status_code=404) from exc

    async def remove_volume(self, name: str) -> None:
        if self.resources.pop(name, None) is None:
            raise DockerNotFound("remove", status_code=404)

    async def inspect_volume(self, name: str) -> DockerResource:
        try:
            return self.resources[name]
        except KeyError as exc:
            raise DockerNotFound("inspect", status_code=404) from exc


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


async def controller(tmp_path: Path, engine: FakeEngine) -> BrowserTaskController:
    value = BrowserTaskController(
        config=config(),
        state=TaskStateStore(tmp_path / "tasks.sqlite3"),
        engine=engine,  # type: ignore[arg-type]
        capability_key=b"k" * 32,
    )
    await value.initialize()
    return value


def request(value: BrowserTaskController, *, request_id=None) -> CreateTaskRequest:
    return CreateTaskRequest(
        request_id=request_id or uuid4(),
        owner_job_id="worker-job-1",
        purpose=TaskPurpose.EXPLORE,
        source_url="https://example.com/",
        expected_runtime_fingerprint=value.descriptor().runtime_identity.fingerprint(),
    )


@pytest.mark.asyncio
async def test_create_is_staged_secured_and_stable_across_controller_restart(tmp_path: Path):
    engine = FakeEngine()
    first_controller = await controller(tmp_path, engine)
    body = request(first_controller)

    first = await first_controller.create(body)
    assert engine.created_order == ["control-volume", "init", "browser", "adapter"]
    assert set(engine.resources) == {
        f"reader-browser-task-{first.task_id}",
        f"reader-browser-{first.task_id}",
        f"reader-browser-adapter-{first.task_id}",
    }
    assert engine.specs["init"]["User"] == "0:0"
    assert engine.specs["init"]["HostConfig"]["NetworkMode"] == "none"
    assert engine.specs["init"]["HostConfig"]["CapAdd"] == ["CHOWN"]
    for kind in ("browser", "adapter"):
        spec = engine.specs[kind]
        assert spec["User"] != "0:0"
        assert spec["HostConfig"]["CapDrop"] == ["ALL"]
        assert spec["HostConfig"]["ReadonlyRootfs"] is True
        assert spec["HostConfig"]["SecurityOpt"] == ["no-new-privileges:true"]
    assert engine.specs["browser"]["HostConfig"]["NetworkMode"] == "none"
    assert engine.specs["adapter"]["Entrypoint"] == ["reader-browser-adapter"]
    assert engine.specs["adapter"]["Healthcheck"]["Test"] == [
        "CMD",
        "reader-browser-adapter-healthcheck",
    ]
    assert "CRAWL4_AI_BASE_DIRECTORY=/tmp/reader-crawl4ai" in engine.specs["adapter"]["Env"]

    restarted = await controller(tmp_path, engine)
    duplicate = await restarted.create(body)
    assert duplicate.task_id == first.task_id
    assert duplicate.capability == first.capability
    assert engine.created_order == ["control-volume", "init", "browser", "adapter"]


@pytest.mark.asyncio
async def test_startup_timeout_returns_retryable_error_and_cleans_whole_group(tmp_path: Path):
    engine = FakeEngine(fail_health_kind="adapter")
    value = await controller(tmp_path, engine)

    with pytest.raises(BrowserTaskError) as caught:
        await value.create(request(value))

    assert caught.value.code == "startup_timeout"
    assert caught.value.retryable is True
    assert engine.resources == {}
    records = value.state.list_records()
    assert len(records) == 1
    assert records[0].state == "closed"
    assert records[0].resources_released is True


@pytest.mark.asyncio
async def test_cleanup_failure_is_visible_and_same_close_request_can_retry(tmp_path: Path):
    engine = FakeEngine()
    value = await controller(tmp_path, engine)
    created = await value.create(request(value))
    engine.fail_remove_once = True
    close_request_id = uuid4()

    with pytest.raises(BrowserTaskError) as caught:
        await value.close(created.task_id, created.capability, "normal", close_request_id)
    assert caught.value.code == "cleanup_failed"
    failed = value.state.get(str(created.task_id))
    assert failed.state == "cleanup_failed"
    assert failed.resources_released is False

    closed = await value.close(created.task_id, created.capability, "normal", close_request_id)
    assert closed.state == "closed"
    assert closed.cleanup_error is None
    assert engine.resources == {}


@pytest.mark.asyncio
async def test_runtime_fingerprint_is_derived_from_effective_isolation_config(tmp_path: Path):
    first = await controller(tmp_path / "first", FakeEngine())
    changed_config = config()
    changed_config = BrowserTaskConfig(
        **{
            **changed_config.__dict__,
            "adapter_port": changed_config.adapter_port + 1,
        }
    )
    second = BrowserTaskController(
        config=changed_config,
        state=TaskStateStore(tmp_path / "second" / "tasks.sqlite3"),
        engine=FakeEngine(),  # type: ignore[arg-type]
        capability_key=b"k" * 32,
    )
    await second.initialize()

    assert (
        first.descriptor().runtime_identity.config_fingerprint
        != second.descriptor().runtime_identity.config_fingerprint
    )
