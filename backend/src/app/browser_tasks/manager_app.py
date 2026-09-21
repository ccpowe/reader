"""Private FastAPI control plane for isolated browser task lifecycles."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from app.browser_tasks.auth import (
    AuthenticationError,
    load_or_create_capability_key,
    read_secret,
    remove_stale_capability_key_temps,
    verify_bearer,
)
from app.browser_tasks.controller import BrowserTaskConfig, BrowserTaskController, BrowserTaskError
from app.browser_tasks.docker_runtime import DockerEngine
from app.browser_tasks.protocol import (
    TASK_CAPABILITY_HEADER,
    CloseTaskRequest,
    CreateTaskRequest,
    CreateTaskResponse,
    ErrorEnvelope,
    ErrorKind,
    HealthResponse,
    RuntimeDescriptorResponse,
    TaskMutationRequest,
    TaskStatusResponse,
)
from app.browser_tasks.state import ControllerLock, TaskStateStore

logger = logging.getLogger(__name__)

_ERROR_RESPONSE = {"model": ErrorEnvelope}
_AUTH_ERRORS = {401: _ERROR_RESPONSE, 503: _ERROR_RESPONSE}
_CREATE_ERRORS = {
    **_AUTH_ERRORS,
    400: _ERROR_RESPONSE,
    409: _ERROR_RESPONSE,
    429: _ERROR_RESPONSE,
    504: _ERROR_RESPONSE,
}
_TASK_READ_ERRORS = {**_AUTH_ERRORS, 404: _ERROR_RESPONSE}
_TASK_MUTATION_ERRORS = {
    **_TASK_READ_ERRORS,
    409: _ERROR_RESPONSE,
    410: _ERROR_RESPONSE,
}


@dataclass
class BrowserTaskRuntime:
    config: BrowserTaskConfig
    state: TaskStateStore
    engine: DockerEngine
    controller: BrowserTaskController | None = None
    bearer: bytes | None = None
    controller_lock: ControllerLock | None = None


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required browser task setting: {name}")
    return value


def _integer(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return default if value is None else int(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid integer browser task setting: {name}") from exc


def load_task_config() -> tuple[BrowserTaskConfig, Path, str]:
    state_dir = Path(os.getenv("APP_BROWSER_TASK_STATE_DIR", "/var/lib/reader-browser"))
    config = BrowserTaskConfig(
        deployment_id=_required("APP_BROWSER_TASK_DEPLOYMENT_ID"),
        browser_image_ref=_required("APP_BROWSER_TASK_BROWSER_IMAGE"),
        adapter_image_ref=_required("APP_BROWSER_TASK_ADAPTER_IMAGE"),
        data_network=_required("APP_BROWSER_TASK_DATA_NETWORK"),
        egress_network=_required("APP_BROWSER_TASK_EGRESS_NETWORK"),
        adapter_port=_integer("APP_BROWSER_TASK_ADAPTER_PORT", 8092),
        policy_version=_required("APP_BROWSER_TASK_POLICY_VERSION"),
        lightpanda_version=os.getenv("APP_BROWSER_TASK_LIGHTPANDA_VERSION", "0.4.0"),
        lightpanda_sha256=_required("APP_BROWSER_TASK_LIGHTPANDA_SHA256"),
        agent_browser_version=os.getenv("APP_BROWSER_TASK_AGENT_BROWSER_VERSION", "0.37.1"),
        agent_browser_sha256=_required("APP_BROWSER_TASK_AGENT_BROWSER_SHA256"),
        lease_seconds=_integer("APP_BROWSER_TASK_LEASE_SECONDS", 90),
        lease_expiry_grace_seconds=_integer("APP_BROWSER_TASK_LEASE_GRACE_SECONDS", 10),
        creation_grace_seconds=_integer("APP_BROWSER_TASK_CREATION_GRACE_SECONDS", 120),
        explore_hard_ttl_seconds=_integer("APP_BROWSER_TASK_EXPLORE_HARD_TTL_SECONDS", 28_800),
        render_hard_ttl_seconds=_integer("APP_BROWSER_TASK_RENDER_HARD_TTL_SECONDS", 900),
        max_tasks=_integer("APP_BROWSER_TASK_MAX_TASKS", 2),
        max_explore_tasks=_integer("APP_BROWSER_TASK_MAX_EXPLORE", 1),
    )
    docker_socket = os.getenv("APP_BROWSER_TASK_DOCKER_SOCKET", "/var/run/docker.sock")
    return config, state_dir, docker_socket


async def build_runtime_from_environment(*, acquire_controller_lock: bool) -> BrowserTaskRuntime:
    config, state_dir, docker_socket = load_task_config()
    state = TaskStateStore(state_dir / "tasks.sqlite3")
    engine = DockerEngine(docker_socket)
    runtime = BrowserTaskRuntime(config=config, state=state, engine=engine)
    if not acquire_controller_lock:
        await asyncio.to_thread(state.initialize)
        await engine.initialize()
        return runtime
    lock = ControllerLock(state_dir / "controller.lock")
    await asyncio.to_thread(lock.acquire)
    runtime.controller_lock = lock
    try:
        bearer_path = _required("APP_BROWSER_MANAGER_TOKEN_FILE")
        key_path = Path(
            os.getenv("APP_BROWSER_CAPABILITY_KEY_FILE", str(state_dir / "capability.key"))
        )
        remove_stale_capability_key_temps(key_path.parent)
        runtime.bearer = read_secret(bearer_path)
        key = await asyncio.to_thread(load_or_create_capability_key, key_path)
        controller = BrowserTaskController(
            config=config, state=state, engine=engine, capability_key=key
        )
        await controller.initialize()
        runtime.controller = controller
        return runtime
    except BaseException:
        lock.release()
        await engine.aclose()
        raise


def _error_status(error: BrowserTaskError) -> int:
    return {
        "unauthorized": 401,
        "task_not_found": 404,
        "request_conflict": 409,
        "task_not_running": 409,
        "lease_expired": 410,
        "capacity_exhausted": 429,
        "startup_timeout": 504,
        "runtime_unavailable": 503,
        "cleanup_failed": 503,
    }.get(error.code, 400)


def _error_kind(error: BrowserTaskError) -> ErrorKind:
    if error.code == "capacity_exhausted":
        return ErrorKind.CAPACITY
    if error.code in {"runtime_unavailable", "startup_timeout"}:
        return ErrorKind.LIFECYCLE
    return ErrorKind.PROTOCOL


def create_app() -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.health = "starting"
        runtime: BrowserTaskRuntime | None = None
        try:
            runtime = await build_runtime_from_environment(acquire_controller_lock=True)
            app.state.browser_runtime = runtime
            app.state.health = "ready"
            yield
        except BaseException:
            app.state.health = "failed"
            raise
        finally:
            if runtime is not None:
                await runtime.engine.aclose()
                if runtime.controller_lock is not None:
                    runtime.controller_lock.release()

    app = FastAPI(title="Reader Browser Task Controller", version="1", lifespan=lifespan)
    app.state.health = "starting"

    @app.exception_handler(BrowserTaskError)
    async def browser_task_error(_request: Request, error: BrowserTaskError):
        body = ErrorEnvelope(
            code=error.code,
            message=str(error),
            kind=_error_kind(error),
            retryable=error.retryable,
            retry_after_seconds=error.retry_after,
        )
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
        return JSONResponse(
            status_code=_error_status(error), content=body.model_dump(mode="json"), headers=headers
        )

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        return HealthResponse(status=request.app.state.health)

    def runtime(request: Request) -> BrowserTaskRuntime:
        value = getattr(request.app.state, "browser_runtime", None)
        if value is None or value.controller is None or value.bearer is None:
            raise BrowserTaskError(
                "runtime_unavailable", "Browser task manager is not ready.", retryable=True
            )
        return value

    def authorize(
        request: Request, authorization: str | None = Header(default=None)
    ) -> BrowserTaskRuntime:
        value = runtime(request)
        try:
            verify_bearer(authorization, value.bearer)
        except AuthenticationError as exc:
            raise BrowserTaskError("unauthorized", "Manager authorization failed.") from exc
        return value

    @app.get("/ready", response_model=HealthResponse, responses={503: _ERROR_RESPONSE})
    async def ready(request: Request) -> HealthResponse:
        if request.app.state.health != "ready":
            raise BrowserTaskError(
                "runtime_unavailable", "Browser task manager is not ready.", retryable=True
            )
        return HealthResponse(status="ready")

    @app.get(
        "/v1/runtime-descriptor",
        response_model=RuntimeDescriptorResponse,
        responses=_AUTH_ERRORS,
    )
    async def descriptor(value: BrowserTaskRuntime = Depends(authorize)):
        return value.controller.descriptor()

    @app.post("/v1/tasks", response_model=CreateTaskResponse, responses=_CREATE_ERRORS)
    async def create_task(body: CreateTaskRequest, value: BrowserTaskRuntime = Depends(authorize)):
        return await value.controller.create(body)

    @app.get(
        "/v1/tasks/{task_id}",
        response_model=TaskStatusResponse,
        responses=_TASK_READ_ERRORS,
    )
    async def task_status(
        task_id: UUID,
        capability: str | None = Header(default=None, alias=TASK_CAPABILITY_HEADER),
        value: BrowserTaskRuntime = Depends(authorize),
    ):
        if not capability:
            raise BrowserTaskError("unauthorized", "Task capability is required.")
        return await value.controller.status(task_id, capability)

    @app.post(
        "/v1/tasks/{task_id}/renew",
        response_model=TaskStatusResponse,
        responses=_TASK_MUTATION_ERRORS,
    )
    async def renew_task(
        task_id: UUID,
        body: TaskMutationRequest,
        capability: str | None = Header(default=None, alias=TASK_CAPABILITY_HEADER),
        value: BrowserTaskRuntime = Depends(authorize),
    ):
        if not capability:
            raise BrowserTaskError("unauthorized", "Task capability is required.")
        return await value.controller.renew(task_id, capability, body.request_id)

    @app.post(
        "/v1/tasks/{task_id}/close",
        response_model=TaskStatusResponse,
        responses=_TASK_MUTATION_ERRORS,
    )
    async def close_task(
        task_id: UUID,
        body: CloseTaskRequest,
        capability: str | None = Header(default=None, alias=TASK_CAPABILITY_HEADER),
        value: BrowserTaskRuntime = Depends(authorize),
    ):
        if not capability:
            raise BrowserTaskError("unauthorized", "Task capability is required.")
        return await value.controller.close(task_id, capability, body.reason.value, body.request_id)

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "app.browser_tasks.manager_app:app",
        host=os.getenv("APP_BROWSER_MANAGER_HOST", "0.0.0.0"),
        port=_integer("APP_BROWSER_MANAGER_PORT", 8091),
        workers=1,
        access_log=False,
    )
