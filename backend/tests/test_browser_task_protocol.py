from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.browser_tasks.protocol import (
    CLICommandRequest,
    CreateTaskRequest,
    CreateTaskResponse,
    RenderPageRequest,
    RuntimeDescriptorResponse,
    RuntimeIdentity,
    TaskPurpose,
    TaskState,
)

SHA = "a" * 64
DIGEST = "sha256:" + "b" * 64


def identity(**changes) -> RuntimeIdentity:
    values = {
        "policy_version": "reader-browser-container-v1",
        "browser_image_digest": DIGEST,
        "adapter_image_digest": DIGEST,
        "lightpanda_version": "0.4.0",
        "lightpanda_sha256": SHA,
        "agent_browser_version": "0.37.1",
        "agent_browser_sha256": SHA,
        "config_fingerprint": SHA,
        "explore_hard_ttl_seconds": 3600,
        "render_hard_ttl_seconds": 900,
    }
    return RuntimeIdentity.model_validate({**values, **changes})


def test_create_request_does_not_accept_client_capability_material() -> None:
    request = CreateTaskRequest(
        request_id=uuid4(),
        owner_job_id="rule-job",
        purpose=TaskPurpose.EXPLORE,
        source_url="https://example.com/blog",
        expected_runtime_fingerprint=identity().fingerprint(),
    )

    with pytest.raises(ValidationError, match="Extra inputs"):
        CreateTaskRequest.model_validate({**request.model_dump(), "capability_nonce": "A" * 43})


def test_runtime_descriptor_reserves_a_render_slot() -> None:
    with pytest.raises(ValidationError, match="leave at least one render slot"):
        RuntimeDescriptorResponse(runtime_identity=identity(), task_slots=2, max_explore_tasks=2)


def test_create_response_requires_running_and_bounded_lease() -> None:
    now = datetime.now(UTC)
    response = CreateTaskResponse(
        task_id=uuid4(),
        state=TaskState.RUNNING,
        capability="c" * 43,
        adapter_endpoint="http://reader-browser-task:8765",
        lease_expires_at=now + timedelta(seconds=90),
        hard_expires_at=now + timedelta(seconds=900),
        runtime_identity=identity(),
    )
    assert "capability" not in response.safe_log_fields()
    with pytest.raises(ValidationError, match="lease expiry cannot exceed hard expiry"):
        CreateTaskResponse.model_validate(
            {
                **response.model_dump(),
                "lease_expires_at": now + timedelta(seconds=901),
                "hard_expires_at": now + timedelta(seconds=900),
            }
        )


def test_cli_request_bounds_arguments_and_observed_urls() -> None:
    with pytest.raises(ValidationError, match="12000"):
        CLICommandRequest(request_id=uuid4(), task_id=uuid4(), argv=["eval", "x" * 12_000])


def test_render_request_requires_dynamic_strict_recipe() -> None:
    base = {
        "request_id": uuid4(),
        "task_id": uuid4(),
        "url": "https://example.com/",
        "recipe": {"extraction": {"baseSelector": "article"}},
        "max_response_bytes": 1024 * 1024,
    }
    with pytest.raises(ValidationError, match="dynamic recipes"):
        RenderPageRequest.model_validate(base)
    with pytest.raises(ValidationError, match="Extra inputs"):
        RenderPageRequest.model_validate(
            {
                **base,
                "recipe": {
                    "render_js": True,
                    "extraction": {"baseSelector": "article"},
                    "unknown": True,
                },
            }
        )
