"""Discovery, health and readiness endpoints."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text

from reader.core.errors import AppError, error_responses
from reader.core.server_access import document_server_token
from reader.core.settings import Settings, get_settings
from reader.system.heartbeats import load_loop_reports

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

# Bumped whenever the client and server stop understanding each other.
PROTOCOL_VERSION = 3
# The Alembic revision this build expects; /ready fails on any other.
SCHEMA_REVISION = "0003_mcp_token"


class DiscoveryResponse(BaseModel):
    protocol_version: Literal[3]
    server_id: str
    api_base_url: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    environment: str


class ReadyResponse(BaseModel):
    status: Literal["ready"]
    schema_revision: str


class LoopStatus(BaseModel):
    loop_name: str
    state: str
    last_succeeded_at: datetime | None
    last_error_code: str | None


class WorkerReadyResponse(BaseModel):
    status: Literal["ready"]
    loops: list[LoopStatus]


@router.get(
    "/.well-known/reader.json",
    response_model=DiscoveryResponse,
    dependencies=[Depends(document_server_token)],
    responses=error_responses((503, "server_not_configured"), access="server"),
)
async def discovery(request: Request, settings: Settings = Depends(get_settings)):
    """Server identity, protocol version and API base URL for the client."""
    missing = settings.missing_server_settings()
    if missing:
        raise AppError(503, "server_not_configured", "Missing settings: " + ", ".join(missing))
    assert settings.server_id is not None
    base = str(settings.public_api_base_url or request.base_url)
    return DiscoveryResponse(
        protocol_version=PROTOCOL_VERSION,
        server_id=settings.server_id,
        api_base_url=base.rstrip("/") or base,
    )


@router.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)):
    """The process is up; says nothing about the database."""
    return HealthResponse(status="ok", environment=settings.environment)


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses=error_responses((503, "not_ready"), access="public"),
)
async def ready(request: Request, settings: Settings = Depends(get_settings)):
    """Configuration is complete, the database answers and its schema is current."""
    missing = settings.missing_server_settings()
    if missing:
        raise AppError(503, "not_ready", "Missing settings: " + ", ".join(missing))
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise AppError(503, "not_ready", "The database is not configured.")
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            async with engine.connect() as connection:
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:
        logger.warning("Readiness check could not query the database.", exc_info=True)
        raise AppError(503, "not_ready", "The database is unavailable.") from None
    if revision != SCHEMA_REVISION:
        raise AppError(
            503, "not_ready", f"Database schema is {revision}, expected {SCHEMA_REVISION}."
        )
    return ReadyResponse(status="ready", schema_revision=revision)


@router.get(
    "/worker-ready",
    response_model=WorkerReadyResponse,
    responses=error_responses((503, "worker_not_ready"), access="public"),
)
async def worker_ready(request: Request, settings: Settings = Depends(get_settings)):
    """Every background loop has succeeded recently."""
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise AppError(503, "worker_not_ready", "The database is not configured.")
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            async with factory() as session:
                reports = await load_loop_reports(session)
    except Exception:
        logger.warning("Worker readiness could not be loaded.", exc_info=True)
        raise AppError(503, "worker_not_ready", "Worker status is unavailable.") from None
    unhealthy = [
        f"{report.loop_name}:{report.state}" for report in reports if report.state != "healthy"
    ]
    if unhealthy:
        raise AppError(503, "worker_not_ready", "Loops not healthy: " + ", ".join(unhealthy))
    return WorkerReadyResponse(
        status="ready",
        loops=[
            LoopStatus(
                loop_name=report.loop_name,
                state=report.state,
                last_succeeded_at=report.last_succeeded_at,
                last_error_code=report.last_error_code,
            )
            for report in reports
        ],
    )
