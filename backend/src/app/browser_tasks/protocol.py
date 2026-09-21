"""Typed internal protocol shared by browser controller, adapter and Worker.

These models describe an internal service boundary.  They are intentionally
separate from Reader's public API contract and never accept Docker/runtime
configuration from callers.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.ingestion.web_crawl_types import PageRecipe

PROTOCOL_VERSION = "1"
TASK_CAPABILITY_HEADER = "X-Reader-Task-Capability"
MAX_CLI_ARGUMENTS = 16
MAX_CLI_ARGUMENT_CHARS = 12_000
MAX_OBSERVED_URLS = 128
MAX_OBSERVED_URL_CHARS = 32_768
MAX_CLI_CAPTURE_CHARS = 256 * 1024
MAX_RENDER_ROWS = 1_000

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ImageDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class StrictProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    def safe_log_fields(self) -> dict[str, Any]:
        """Return structured log fields with protocol secrets removed."""
        excluded = {
            name
            for name, info in type(self).model_fields.items()
            if (info.json_schema_extra or {}).get("sensitive")
        }
        return self.model_dump(mode="json", exclude=excluded)


class TaskPurpose(StrEnum):
    EXPLORE = "explore"
    RENDER = "render"


class TaskState(StrEnum):
    CREATING = "creating"
    RUNNING = "running"
    CLEANING = "cleaning"
    CLOSED = "closed"
    FAILED = "failed"
    CLEANUP_FAILED = "cleanup_failed"


class CloseReason(StrEnum):
    NORMAL = "normal"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    LEASE_LOST = "lease_lost"
    CLIENT_ERROR = "client_error"


class RuntimeIdentity(StrictProtocolModel):
    protocol_version: Literal["1"] = PROTOCOL_VERSION
    policy_version: str = Field(min_length=1, max_length=128)
    browser_image_digest: ImageDigest
    adapter_image_digest: ImageDigest
    lightpanda_version: Literal["0.4.0"]
    lightpanda_sha256: Sha256Hex
    agent_browser_version: Literal["0.37.1"]
    agent_browser_sha256: Sha256Hex
    config_fingerprint: Sha256Hex
    explore_hard_ttl_seconds: int = Field(ge=60, le=86_400)
    render_hard_ttl_seconds: int = Field(ge=60, le=86_400)

    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class RuntimeDescriptorResponse(StrictProtocolModel):
    runtime_identity: RuntimeIdentity
    task_slots: int = Field(ge=2, le=64)
    max_explore_tasks: int = Field(default=1, ge=1, le=63)

    @model_validator(mode="after")
    def reserve_a_render_slot(self):
        if self.max_explore_tasks >= self.task_slots:
            raise ValueError("max_explore_tasks must leave at least one render slot")
        return self


class CreateTaskRequest(StrictProtocolModel):
    request_id: UUID
    owner_job_id: str = Field(min_length=1, max_length=128)
    purpose: TaskPurpose
    source_url: AnyHttpUrl
    expected_runtime_fingerprint: Sha256Hex


class CreateTaskResponse(StrictProtocolModel):
    task_id: UUID
    state: Literal[TaskState.RUNNING]
    capability: str = Field(
        min_length=43,
        max_length=128,
        repr=False,
        json_schema_extra={"sensitive": True, "readOnly": True},
    )
    adapter_endpoint: AnyHttpUrl
    lease_expires_at: AwareDatetime
    hard_expires_at: AwareDatetime
    runtime_identity: RuntimeIdentity

    @model_validator(mode="after")
    def lease_precedes_hard_expiry(self):
        if self.lease_expires_at > self.hard_expires_at:
            raise ValueError("lease expiry cannot exceed hard expiry")
        return self


class TaskMutationRequest(StrictProtocolModel):
    request_id: UUID


class CloseTaskRequest(TaskMutationRequest):
    reason: CloseReason


class TaskStatusResponse(StrictProtocolModel):
    task_id: UUID
    purpose: TaskPurpose
    state: TaskState
    lease_expires_at: AwareDatetime
    hard_expires_at: AwareDatetime
    runtime_identity: RuntimeIdentity
    cleanup_error: str | None = Field(default=None, max_length=1_000)


class HealthResponse(StrictProtocolModel):
    status: Literal["starting", "ready", "failed"]


class ErrorKind(StrEnum):
    SOURCE_SCAN = "source_scan"
    NETWORK = "network"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    CAPACITY = "capacity"
    LIFECYCLE = "lifecycle"


class ErrorEnvelope(StrictProtocolModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2_000)
    kind: ErrorKind = ErrorKind.PROTOCOL
    retryable: bool = False
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86_400)
    long_lived: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)


class AdapterRequest(StrictProtocolModel):
    request_id: UUID
    task_id: UUID


class CLICommandRequest(AdapterRequest):
    argv: list[str] = Field(min_length=1, max_length=MAX_CLI_ARGUMENTS)
    observed_urls: list[AnyHttpUrl] = Field(default_factory=list, max_length=MAX_OBSERVED_URLS)

    @field_validator("argv")
    @classmethod
    def bounded_argv(cls, value: list[str]) -> list[str]:
        if any("\0" in item for item in value):
            raise ValueError("argv cannot contain NUL bytes")
        if sum(len(item) for item in value) > MAX_CLI_ARGUMENT_CHARS:
            raise ValueError(f"argv cannot exceed {MAX_CLI_ARGUMENT_CHARS} characters")
        return value

    @field_validator("observed_urls")
    @classmethod
    def bounded_observed_urls(cls, value: list[AnyHttpUrl]) -> list[AnyHttpUrl]:
        if sum(len(str(item)) for item in value) > MAX_OBSERVED_URL_CHARS:
            raise ValueError("observed_urls exceeds its total character limit")
        return value


class CLIError(StrictProtocolModel):
    source: Literal["wrapper", "cli"]
    exit_code: int | None = None
    message: JsonValue | None = None
    code: str | None = Field(default=None, max_length=128)


class NetworkError(StrictProtocolModel):
    url: str = Field(max_length=4_096)
    resource_type: str | None = Field(default=None, max_length=128)
    code: str | None = Field(default=None, max_length=128)
    message: str = Field(max_length=2_000)


class CLIReport(StrictProtocolModel):
    status: Literal["ok", "error"]
    url: str | None = Field(default=None, max_length=4_096)
    page_revision: Sha256Hex | None = None
    format: Literal["text", "snapshot", "html", "json"]
    output: str = Field(default="", max_length=MAX_CLI_CAPTURE_CHARS)
    truncated: bool = False
    truncated_fields: list[str] = Field(default_factory=list, max_length=64)
    error: CLIError | None = None
    stderr: str = Field(default="", max_length=MAX_CLI_CAPTURE_CHARS)
    command_timed_out: bool = False
    cli_warning: JsonValue | None = None
    network_errors: list[NetworkError] = Field(default_factory=list, max_length=3)


class RenderPageRequest(AdapterRequest):
    url: AnyHttpUrl
    recipe: PageRecipe
    max_response_bytes: int = Field(ge=256 * 1024, le=20 * 1024 * 1024)

    @model_validator(mode="after")
    def dynamic_recipe_only(self):
        if not self.recipe.render_js:
            raise ValueError("render-page accepts only dynamic recipes")
        return self


class RenderedPageResponse(StrictProtocolModel):
    final_url: AnyHttpUrl
    status_code: int = Field(ge=100, le=599)
    safe_headers: dict[str, str] = Field(default_factory=dict)
    rendered_html: str
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_RENDER_ROWS)

    @field_validator("safe_headers")
    @classmethod
    def bounded_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 128 or sum(len(key) + len(item) for key, item in value.items()) > 32_768:
            raise ValueError("response headers exceed their protocol limit")
        if any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) for key in value):
            raise ValueError("response contains an invalid header name")
        return value


class CachedAdapterResponse(StrictProtocolModel):
    """Adapter-side idempotency metadata; never sent across the wire."""

    request_id: UUID
    request_sha256: Sha256Hex
    completed_at: datetime
