from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from app.browser_tasks.controller import BrowserTaskError
from app.browser_tasks.manager_app import BrowserTaskRuntime, create_app
from app.browser_tasks.protocol import RuntimeIdentity, TaskPurpose, TaskState, TaskStatusResponse


class FakeController:
    def __init__(self):
        self.renew_request_id = None
        self.close_request_id = None

    def descriptor(self):
        raise AssertionError("not used")

    async def create(self, _body):
        raise BrowserTaskError(
            "capacity_exhausted", "Browser task capacity is busy.", retryable=True, retry_after=2
        )

    async def renew(self, task_id, capability, request_id):
        self.renew_request_id = request_id
        return response(task_id)

    async def close(self, task_id, capability, reason, request_id):
        self.close_request_id = request_id
        return response(task_id, state=TaskState.CLOSED)


def identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        policy_version="policy-one",
        browser_image_digest="sha256:" + "a" * 64,
        adapter_image_digest="sha256:" + "b" * 64,
        lightpanda_version="0.4.0",
        lightpanda_sha256="c" * 64,
        agent_browser_version="0.37.1",
        agent_browser_sha256="d" * 64,
        config_fingerprint="e" * 64,
        explore_hard_ttl_seconds=28_800,
        render_hard_ttl_seconds=900,
    )


def response(task_id, *, state=TaskState.RUNNING):
    now = datetime.now(UTC)
    return TaskStatusResponse(
        task_id=task_id,
        purpose=TaskPurpose.EXPLORE,
        state=state,
        lease_expires_at=now + timedelta(seconds=90),
        hard_expires_at=now + timedelta(hours=8),
        runtime_identity=identity(),
    )


@pytest.mark.asyncio
async def test_manager_requires_bearer_and_forwards_idempotency_keys():
    app = create_app()
    fake = FakeController()
    app.state.browser_runtime = BrowserTaskRuntime(
        config=None,  # type: ignore[arg-type]
        state=None,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        controller=fake,  # type: ignore[arg-type]
        bearer=b"x" * 32,
    )
    app.state.health = "ready"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://manager") as client:
        task_id = uuid4()
        request_id = uuid4()
        denied = await client.post(
            f"/v1/tasks/{task_id}/renew", json={"request_id": str(request_id)}
        )
        assert denied.status_code == 401
        assert denied.json()["code"] == "unauthorized"

        headers = {
            "Authorization": f"Bearer {'x' * 32}",
            "X-Reader-Task-Capability": "task-capability",
        }
        renewed = await client.post(
            f"/v1/tasks/{task_id}/renew",
            json={"request_id": str(request_id)},
            headers=headers,
        )
        assert renewed.status_code == 200
        assert fake.renew_request_id == request_id

        close_request_id = uuid4()
        closed = await client.post(
            f"/v1/tasks/{task_id}/close",
            json={"request_id": str(close_request_id), "reason": "normal"},
            headers=headers,
        )
        assert closed.status_code == 200
        assert fake.close_request_id == close_request_id


@pytest.mark.asyncio
async def test_manager_runtime_errors_match_declared_envelope_but_validation_stays_fastapi():
    app = create_app()
    app.state.browser_runtime = BrowserTaskRuntime(
        config=None,  # type: ignore[arg-type]
        state=None,  # type: ignore[arg-type]
        engine=None,  # type: ignore[arg-type]
        controller=FakeController(),  # type: ignore[arg-type]
        bearer=b"x" * 32,
    )
    app.state.health = "ready"
    headers = {"Authorization": f"Bearer {'x' * 32}"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://manager") as client:
        busy = await client.post(
            "/v1/tasks",
            headers=headers,
            json={
                "request_id": str(uuid4()),
                "owner_job_id": "job-1",
                "purpose": "render",
                "source_url": "https://example.com/",
                "expected_runtime_fingerprint": "a" * 64,
            },
        )
        assert busy.status_code == 429
        assert busy.headers["Retry-After"] == "2"
        assert busy.json()["code"] == "capacity_exhausted"

        invalid = await client.get("/v1/tasks/not-a-uuid", headers=headers)
        assert invalid.status_code == 422
        assert "detail" in invalid.json()
        assert "code" not in invalid.json()

    schema = app.openapi()
    create_responses = schema["paths"]["/v1/tasks"]["post"]["responses"]
    assert create_responses["429"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorEnvelope"
    }
    assert create_responses["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HTTPValidationError"
    }
    renew_responses = schema["paths"]["/v1/tasks/{task_id}/renew"]["post"]["responses"]
    assert {"401", "404", "409", "410", "422", "503"}.issubset(renew_responses)
