"""Internal FastAPI data plane for one isolated browser task."""

from __future__ import annotations

import json
import os
import urllib.request
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.ingestion.models import SourceScanError

from .adapter_runtime import AdapterConfiguration, AdapterProtocolError, AdapterRuntime
from .protocol import (
    TASK_CAPABILITY_HEADER,
    CLICommandRequest,
    CLIReport,
    ErrorEnvelope,
    ErrorKind,
    HealthResponse,
    RenderedPageResponse,
    RenderPageRequest,
)

_TASK_ERROR_RESPONSES = {
    401: {"model": ErrorEnvelope, "description": "Task capability is invalid."},
    409: {"model": ErrorEnvelope, "description": "Task state or request conflicts."},
    422: {"model": ErrorEnvelope, "description": "Request violates the internal protocol."},
    502: {"model": ErrorEnvelope, "description": "Controlled page retrieval failed."},
    504: {"model": ErrorEnvelope, "description": "Controlled page retrieval timed out."},
}


def _retryable_source_error(code: str) -> bool:
    return (
        code
        in {
            "source_timeout",
            "rate_limited",
            "upstream_error",
            "web_rate_limited",
            "web_upstream_error",
        }
        or code == "web_http_429"
        or code.startswith("web_http_5")
    )


def _error_response(error: ErrorEnvelope, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


def create_adapter_app(*, runtime: AdapterRuntime | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        active = runtime or AdapterRuntime(AdapterConfiguration.from_environment())
        application.state.runtime = active
        await active.start()
        try:
            yield
        finally:
            await active.aclose()

    application = FastAPI(
        title="Reader Browser Adapter",
        version="1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.exception_handler(AdapterProtocolError)
    async def adapter_protocol_error(_request: Request, exc: AdapterProtocolError):
        status = 401 if exc.error.code == "unauthorized" else 409
        return _error_response(exc.error, status)

    @application.exception_handler(SourceScanError)
    async def source_scan_error(_request: Request, exc: SourceScanError):
        error = ErrorEnvelope(
            code=exc.code,
            message=str(exc)[:2000],
            kind=ErrorKind.SOURCE_SCAN,
            retryable=_retryable_source_error(exc.code),
            retry_after_seconds=exc.retry_after_seconds,
            long_lived=exc.long_lived,
            evidence=exc.evidence,
        )
        return _error_response(error, 502)

    @application.exception_handler(httpx.TimeoutException)
    async def timeout_error(_request: Request, _exc: httpx.TimeoutException):
        return _error_response(
            ErrorEnvelope(
                code="browser_runtime_timeout",
                message="Browser adapter network request timed out.",
                kind=ErrorKind.TIMEOUT,
                retryable=True,
            ),
            504,
        )

    @application.exception_handler(httpx.NetworkError)
    async def network_error(_request: Request, _exc: httpx.NetworkError):
        return _error_response(
            ErrorEnvelope(
                code="browser_runtime_network_error",
                message="Browser adapter network request failed.",
                kind=ErrorKind.NETWORK,
                retryable=True,
            ),
            502,
        )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        return _error_response(
            ErrorEnvelope(
                code="invalid_request",
                message="Browser adapter request did not match the internal protocol.",
                kind=ErrorKind.PROTOCOL,
            ),
            422,
        )

    @application.get("/health", response_model=HealthResponse, operation_id="adapter_health")
    async def health(request: Request) -> HealthResponse:
        return request.app.state.runtime.health

    @application.post(
        "/v1/cli-command",
        response_model=CLIReport,
        responses=_TASK_ERROR_RESPONSES,
        operation_id="run_cli_command",
    )
    async def cli_command(
        body: CLICommandRequest,
        request: Request,
        capability: str | None = Header(default=None, alias=TASK_CAPABILITY_HEADER),
    ) -> CLIReport:
        return await request.app.state.runtime.cli_command(body, capability)

    @application.post(
        "/v1/render-page",
        response_model=RenderedPageResponse,
        responses=_TASK_ERROR_RESPONSES,
        operation_id="render_page",
    )
    async def render_page(
        body: RenderPageRequest,
        request: Request,
        capability: str | None = Header(default=None, alias=TASK_CAPABILITY_HEADER),
    ) -> RenderedPageResponse:
        return await request.app.state.runtime.render_page(body, capability)

    return application


app = create_adapter_app()


def main() -> None:
    uvicorn.run(
        "app.browser_tasks.adapter_app:app",
        host="0.0.0.0",
        port=int(os.environ.get("READER_BROWSER_ADAPTER_PORT", "8092")),
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )


def healthcheck_main() -> None:
    """Fail unless this task's configured adapter and Lightpanda session are ready."""
    port = int(os.environ.get("READER_BROWSER_ADAPTER_PORT", "8092"))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            payload = json.load(response)
        if payload != {"status": "ready"}:
            raise ValueError("adapter is not ready")
    except (OSError, ValueError, json.JSONDecodeError):
        raise SystemExit(1) from None
