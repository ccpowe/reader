"""Worker client for the internal browser controller and task adapters."""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from uuid import UUID, uuid4

import httpx

from app.ingestion.models import SourceScanError
from app.ingestion.web_crawl_types import PageRecipe

from .protocol import (
    TASK_CAPABILITY_HEADER,
    CLICommandRequest,
    CLIReport,
    CloseReason,
    CloseTaskRequest,
    CreateTaskRequest,
    CreateTaskResponse,
    ErrorEnvelope,
    ErrorKind,
    RenderedPageResponse,
    RenderPageRequest,
    RuntimeDescriptorResponse,
    RuntimeIdentity,
    TaskMutationRequest,
    TaskPurpose,
    TaskStatusResponse,
)


class BrowserTaskError(SourceScanError):
    """Structured internal runtime failure preserving retry classification."""

    def __init__(self, error: ErrorEnvelope) -> None:
        super().__init__(
            error.code,
            error.message,
            retry_after_seconds=error.retry_after_seconds,
            long_lived=error.long_lived,
            evidence=error.evidence,
        )
        self.kind = error.kind
        self.retryable = error.retryable


def _endpoint(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _controller_token(settings) -> str | None:
    if settings.browser_controller_token is not None:
        return settings.browser_controller_token.get_secret_value()
    path = settings.browser_controller_token_file
    if path is None:
        return None
    descriptor = -1
    try:
        descriptor = os.open(Path(path), os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("not a regular file")
        # Docker secrets commonly use 0440 with a supplemental group.  Reject
        # group writes and every permission for other users without requiring
        # the bind mount's host owner to match the container UID.
        if metadata.st_mode & 0o027:
            raise ValueError("unsafe secret permissions")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("secret is too large")
        token = raw.decode("utf-8").strip()
        if not token or any(character.isspace() for character in token):
            raise ValueError("secret is empty or contains whitespace")
        return token
    except (OSError, UnicodeError, ValueError) as exc:
        raise BrowserTaskError(
            ErrorEnvelope(
                code="browser_unavailable",
                message="The browser controller token file is unavailable or unsafe.",
                kind=ErrorKind.LIFECYCLE,
                long_lived=True,
            )
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _error_from_response(response: httpx.Response) -> BrowserTaskError:
    try:
        payload: Any = response.json()
    except (ValueError, UnicodeDecodeError):
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("detail"), dict):
        payload = payload["detail"]
    try:
        error = ErrorEnvelope.model_validate(payload)
    except (ValueError, TypeError):
        error = ErrorEnvelope(
            code="browser_runtime_protocol_error",
            message=f"Browser runtime returned HTTP {response.status_code} without a valid error.",
            kind=ErrorKind.PROTOCOL,
            retryable=response.status_code >= 500,
        )
    return BrowserTaskError(error)


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    timeout: float,
    retry_transport_once: bool = False,
) -> Any:
    attempts = 2 if retry_transport_once else 1
    for attempt in range(attempts):
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            break
        except httpx.TimeoutException as exc:
            if attempt + 1 < attempts:
                continue
            raise BrowserTaskError(
                ErrorEnvelope(
                    code="browser_runtime_timeout",
                    message="Browser runtime request timed out.",
                    kind=ErrorKind.TIMEOUT,
                    retryable=True,
                )
            ) from exc
        except httpx.NetworkError as exc:
            if attempt + 1 < attempts:
                continue
            raise BrowserTaskError(
                ErrorEnvelope(
                    code="browser_runtime_unavailable",
                    message="Browser runtime connection failed.",
                    kind=ErrorKind.NETWORK,
                    retryable=True,
                )
            ) from exc
    if response.is_error:
        raise _error_from_response(response)
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise BrowserTaskError(
            ErrorEnvelope(
                code="browser_runtime_protocol_error",
                message="Browser runtime returned invalid JSON.",
                kind=ErrorKind.PROTOCOL,
            )
        ) from exc


@dataclass
class BrowserTaskSession:
    """One manager-owned browser task; capability exists only in Worker memory."""

    client: httpx.AsyncClient
    controller_url: str
    controller_headers: dict[str, str]
    task_id: UUID
    purpose: TaskPurpose
    adapter_endpoint: str
    capability: str = field(repr=False)
    runtime_identity: dict[str, Any]
    adapter_timeout_seconds: float
    close_timeout_seconds: float
    heartbeat_seconds: float
    _closed: bool = field(default=False, init=False, repr=False)
    _close_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _heartbeat_task: asyncio.Task | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_created(
        cls,
        *,
        client: httpx.AsyncClient,
        controller_url: str,
        controller_headers: dict[str, str],
        purpose: TaskPurpose,
        created: CreateTaskResponse,
        adapter_timeout_seconds: float,
        close_timeout_seconds: float,
        heartbeat_seconds: float,
    ) -> BrowserTaskSession:
        return cls(
            client=client,
            controller_url=controller_url,
            controller_headers=controller_headers,
            task_id=created.task_id,
            purpose=purpose,
            adapter_endpoint=str(created.adapter_endpoint),
            capability=created.capability,
            runtime_identity=created.runtime_identity.model_dump(mode="json"),
            adapter_timeout_seconds=adapter_timeout_seconds,
            close_timeout_seconds=close_timeout_seconds,
            heartbeat_seconds=heartbeat_seconds,
        )

    def start_heartbeat(self) -> None:
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat())

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            await self.renew()

    async def _run_guarded(self, operation_factory):
        if self._closed:
            raise BrowserTaskError(
                ErrorEnvelope(
                    code="browser_session_lost",
                    message="Browser task already ended.",
                    kind=ErrorKind.LIFECYCLE,
                )
            )
        heartbeat = self._heartbeat_task
        if heartbeat is not None and heartbeat.done():
            return heartbeat.result()
        task = asyncio.create_task(operation_factory())
        if heartbeat is None:
            return await task
        done, _pending = await asyncio.wait({task, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return heartbeat.result()

    @property
    def adapter_headers(self) -> dict[str, str]:
        return {TASK_CAPABILITY_HEADER: self.capability}

    async def command(self, argv: list[str], *, observed_urls=()) -> dict[str, Any]:
        if self.purpose != TaskPurpose.EXPLORE:
            raise ValueError("cli-command requires an explore task")
        request = CLICommandRequest(
            request_id=uuid4(),
            task_id=self.task_id,
            argv=argv,
            observed_urls=list(observed_urls),
        )
        payload = await self._run_guarded(
            lambda: _request_json(
                self.client,
                "POST",
                _endpoint(self.adapter_endpoint, "/v1/cli-command"),
                headers=self.adapter_headers,
                payload=request.model_dump(mode="json"),
                timeout=self.adapter_timeout_seconds,
                retry_transport_once=True,
            )
        )
        return CLIReport.model_validate(payload).model_dump(mode="python")

    async def render_page(
        self, *, url: str, recipe: PageRecipe, maximum_bytes: int
    ) -> RenderedPageResponse:
        if self.purpose != TaskPurpose.RENDER:
            raise ValueError("render-page requires a render task")
        request = RenderPageRequest(
            request_id=uuid4(),
            task_id=self.task_id,
            url=url,
            recipe=recipe,
            max_response_bytes=maximum_bytes,
        )
        payload = await self._run_guarded(
            lambda: _request_json(
                self.client,
                "POST",
                _endpoint(self.adapter_endpoint, "/v1/render-page"),
                headers=self.adapter_headers,
                payload=request.model_dump(mode="json"),
                timeout=self.adapter_timeout_seconds,
                retry_transport_once=True,
            )
        )
        return RenderedPageResponse.model_validate(payload)

    async def renew(self) -> TaskStatusResponse:
        request = TaskMutationRequest(request_id=uuid4())
        payload = await _request_json(
            self.client,
            "POST",
            _endpoint(self.controller_url, f"/v1/tasks/{self.task_id}/renew"),
            headers={**self.controller_headers, TASK_CAPABILITY_HEADER: self.capability},
            payload=request.model_dump(mode="json"),
            timeout=self.close_timeout_seconds,
            retry_transport_once=True,
        )
        return TaskStatusResponse.model_validate(payload)

    async def status(self) -> TaskStatusResponse:
        payload = await _request_json(
            self.client,
            "GET",
            _endpoint(self.controller_url, f"/v1/tasks/{self.task_id}"),
            headers={**self.controller_headers, TASK_CAPABILITY_HEADER: self.capability},
            timeout=self.close_timeout_seconds,
        )
        return TaskStatusResponse.model_validate(payload)

    async def _close(self, reason: CloseReason) -> None:
        self._closed = True
        heartbeat = self._heartbeat_task
        if heartbeat is not None and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        request = CloseTaskRequest(request_id=uuid4(), reason=reason)
        payload = await _request_json(
            self.client,
            "POST",
            _endpoint(self.controller_url, f"/v1/tasks/{self.task_id}/close"),
            headers={**self.controller_headers, TASK_CAPABILITY_HEADER: self.capability},
            payload=request.model_dump(mode="json"),
            timeout=self.close_timeout_seconds,
            retry_transport_once=True,
        )
        status = TaskStatusResponse.model_validate(payload)
        if status.state.value != "closed":
            raise BrowserTaskError(
                ErrorEnvelope(
                    code="browser_cleanup_failed",
                    message=(
                        status.cleanup_error or "Browser task resources were not fully removed."
                    ),
                    kind=ErrorKind.LIFECYCLE,
                    retryable=True,
                )
            )

    async def aclose(self, reason: CloseReason = CloseReason.NORMAL) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(reason))
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError


class BrowserTaskClient:
    def __init__(
        self,
        controller_url: str,
        controller_token: str,
        *,
        control_timeout_seconds: float = 45,
        adapter_timeout_seconds: float = 70,
        close_timeout_seconds: float = 15,
        heartbeat_seconds: float = 15,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.controller_url = controller_url
        self.controller_headers = {"Authorization": f"Bearer {controller_token}"}
        self.control_timeout_seconds = control_timeout_seconds
        self.adapter_timeout_seconds = adapter_timeout_seconds
        self.close_timeout_seconds = close_timeout_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(trust_env=False)

    @classmethod
    def from_settings(cls, settings, *, client: httpx.AsyncClient | None = None):
        token = _controller_token(settings)
        if settings.browser_controller_url is None or token is None:
            raise BrowserTaskError(
                ErrorEnvelope(
                    code="browser_unavailable",
                    message="Configure the internal browser task controller.",
                    kind=ErrorKind.LIFECYCLE,
                    long_lived=True,
                )
            )
        return cls(
            str(settings.browser_controller_url),
            token,
            control_timeout_seconds=settings.browser_controller_control_timeout_seconds,
            adapter_timeout_seconds=settings.browser_adapter_request_timeout_seconds,
            close_timeout_seconds=settings.browser_task_close_timeout_seconds,
            heartbeat_seconds=settings.browser_task_heartbeat_seconds,
            client=client,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def descriptor(self) -> RuntimeDescriptorResponse:
        payload = await _request_json(
            self.client,
            "GET",
            _endpoint(self.controller_url, "/v1/runtime-descriptor"),
            headers=self.controller_headers,
            timeout=self.control_timeout_seconds,
        )
        return RuntimeDescriptorResponse.model_validate(payload)

    async def create(
        self,
        *,
        purpose: TaskPurpose,
        source_url: str,
        owner_job_id: str,
        expected_runtime_fingerprint: str,
    ) -> BrowserTaskSession:
        request = CreateTaskRequest(
            request_id=uuid4(),
            owner_job_id=owner_job_id,
            purpose=purpose,
            source_url=source_url,
            expected_runtime_fingerprint=expected_runtime_fingerprint,
        )
        payload = await _request_json(
            self.client,
            "POST",
            _endpoint(self.controller_url, "/v1/tasks"),
            headers=self.controller_headers,
            payload=request.model_dump(mode="json"),
            timeout=self.control_timeout_seconds,
            retry_transport_once=True,
        )
        created = CreateTaskResponse.model_validate(payload)
        session = BrowserTaskSession.from_created(
            client=self.client,
            controller_url=self.controller_url,
            controller_headers=self.controller_headers,
            purpose=purpose,
            created=created,
            adapter_timeout_seconds=self.adapter_timeout_seconds,
            close_timeout_seconds=self.close_timeout_seconds,
            heartbeat_seconds=self.heartbeat_seconds,
        )
        if created.runtime_identity.fingerprint() != expected_runtime_fingerprint:
            try:
                await session.aclose(reason=CloseReason.CLIENT_ERROR)
            finally:
                raise BrowserTaskError(
                    ErrorEnvelope(
                        code="browser_configuration_changed",
                        message="Browser runtime identity changed during task creation.",
                        kind=ErrorKind.LIFECYCLE,
                    )
                )
        session.start_heartbeat()
        return session


def _configuration_changed(message: str) -> BrowserTaskError:
    return BrowserTaskError(
        ErrorEnvelope(
            code="browser_configuration_changed",
            message=message,
            kind=ErrorKind.LIFECYCLE,
            long_lived=True,
        )
    )


def validate_runtime_descriptor(settings, descriptor: RuntimeDescriptorResponse) -> None:
    margin = settings.browser_task_startup_shutdown_margin_seconds
    render_required = (
        max(
            60,
            settings.ingestion_source_timeout_seconds,
            settings.web_rule_agent_validate_timeout_seconds,
        )
        + margin
    )
    if descriptor.runtime_identity.render_hard_ttl_seconds < render_required:
        raise _configuration_changed(
            "Browser render hard TTL does not cover Reader's maximum operation deadline."
        )
    # The explore task spans a claimed rule job. Model requests are the durable
    # upper bound; each may be followed by one bounded inspection command.
    explore_required = (
        settings.web_rule_agent_max_model_calls
        * (
            settings.web_rule_agent_model_timeout_seconds
            + settings.web_rule_agent_inspect_timeout_seconds
        )
        + settings.web_rule_agent_validate_timeout_seconds
        + margin
    )
    if descriptor.runtime_identity.explore_hard_ttl_seconds < explore_required:
        raise _configuration_changed(
            "Browser explore hard TTL does not cover Reader's maximum rule-job deadline."
        )


class RemoteCLIBrowserSession:
    """CLIBrowserSession-compatible Worker proxy to one explore task."""

    def __init__(
        self,
        settings,
        source_url: str,
        *,
        owner_job_id: str,
        expected_runtime_identity: dict[str, Any] | None,
    ) -> None:
        self.settings = settings
        self.source_url = source_url
        self.owner_job_id = owner_job_id
        self.expected_runtime_identity = expected_runtime_identity
        self.client: BrowserTaskClient | None = None
        self.session: BrowserTaskSession | None = None
        self._closed = False
        self._lifecycle_lock = asyncio.Lock()
        self._close_task: asyncio.Task | None = None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                raise SourceScanError("browser_session_lost", "Exploration session already ended.")
            if self.session is not None:
                return
            self.client = BrowserTaskClient.from_settings(self.settings)
            try:
                descriptor = await self.client.descriptor()
                validate_runtime_descriptor(self.settings, descriptor)
                if self.expected_runtime_identity is not None:
                    expected = RuntimeIdentity.model_validate(self.expected_runtime_identity)
                    if expected != descriptor.runtime_identity:
                        raise _configuration_changed("Browser runtime identity changed.")
                self.session = await self.client.create(
                    purpose=TaskPurpose.EXPLORE,
                    source_url=self.source_url,
                    owner_job_id=self.owner_job_id,
                    expected_runtime_fingerprint=descriptor.runtime_identity.fingerprint(),
                )
            except BaseException:
                await self.client.aclose()
                self.client = None
                raise

    async def command(self, argv: list[str], *, observed_urls=()) -> dict[str, Any]:
        try:
            await self.start()
            assert self.session is not None
            return await self.session.command(argv, observed_urls=observed_urls)
        except asyncio.CancelledError:
            await self.aclose(reason=CloseReason.CANCELLED)
            raise

    async def _close(self, reason: CloseReason) -> None:
        self._closed = True
        async with self._lifecycle_lock:
            try:
                if self.session is not None:
                    await self.session.aclose(reason=reason)
            finally:
                if self.client is not None:
                    await self.client.aclose()

    async def aclose(self, reason: CloseReason = CloseReason.NORMAL) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(reason))
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError


async def render_page_with_browser_task(
    settings,
    *,
    source_url: str,
    url: str,
    recipe: PageRecipe,
    maximum_bytes: int,
    owner_job_id: str | None = None,
    expected_runtime_identity: dict[str, Any] | None = None,
) -> RenderedPageResponse:
    client = BrowserTaskClient.from_settings(settings)
    session: BrowserTaskSession | None = None
    reason = CloseReason.NORMAL
    primary: BaseException | None = None
    try:
        descriptor = await client.descriptor()
        validate_runtime_descriptor(settings, descriptor)
        if expected_runtime_identity is not None:
            expected = RuntimeIdentity.model_validate(expected_runtime_identity)
            if expected != descriptor.runtime_identity:
                raise _configuration_changed("Browser runtime identity changed.")
        session = await client.create(
            purpose=TaskPurpose.RENDER,
            source_url=source_url,
            owner_job_id=owner_job_id or "render-" + uuid4().hex,
            expected_runtime_fingerprint=descriptor.runtime_identity.fingerprint(),
        )
        return await session.render_page(url=url, recipe=recipe, maximum_bytes=maximum_bytes)
    except asyncio.CancelledError as exc:
        primary = exc
        reason = CloseReason.CANCELLED
        raise
    except BrowserTaskError as exc:
        primary = exc
        reason = CloseReason.TIMEOUT if exc.kind == ErrorKind.TIMEOUT else CloseReason.CLIENT_ERROR
        raise
    except BaseException as exc:
        primary = exc
        reason = CloseReason.CLIENT_ERROR
        raise
    finally:
        try:
            if session is not None:
                await session.aclose(reason=reason)
        except BaseException:
            if primary is None:
                raise
        finally:
            await client.aclose()
