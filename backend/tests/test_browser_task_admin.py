from __future__ import annotations

from dataclasses import replace

import pytest
from test_browser_task_reaper import ReaperEngine, config, group, record

from app.browser_tasks import admin
from app.browser_tasks.manager_app import BrowserTaskRuntime
from app.browser_tasks.state import TaskStateStore


class AdminEngine(ReaperEngine):
    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_admin_drain_uses_state_and_fixed_deployment_labels(tmp_path, monkeypatch):
    now = 50_000.0
    state = TaskStateStore(tmp_path / "tasks.sqlite3")
    state.initialize()
    initial = replace(
        record(task_id="task", request_id="request-task", purpose="render"),
        created_at=now - 100,
        creation_deadline=now - 50,
        lease_expires_at=now + 100,
        hard_expires_at=now + 500,
        updated_at=now - 100,
    )
    initial, _ = state.reserve(initial, observed_tasks={}, max_tasks=2, max_explore=1)
    state.transition(
        "task",
        expected_state="creating",
        expected_version=initial.version,
        state="running",
        stage="running",
        adapter_endpoint="http://adapter:8092",
        updated_at=now - 100,
    )
    own_resources = group("task", now=now)
    foreign_resources = group("foreign", now=now)
    for resource in foreign_resources:
        resource.labels["io.reader.browser-task.deployment"] = "another-deployment"
    engine = AdminEngine(own_resources + foreign_resources)
    runtime = BrowserTaskRuntime(
        config=config(),
        state=state,
        engine=engine,  # type: ignore[arg-type]
    )

    async def build_runtime_from_environment(*, acquire_controller_lock: bool):
        assert acquire_controller_lock is False
        return runtime

    monkeypatch.setattr(admin, "build_runtime_from_environment", build_runtime_from_environment)

    assert await admin.drain() is True
    assert state.get("task").state == "closed"
    assert set(engine.resources) == {"foreign-browser", "foreign-adapter", "foreign-volume"}
