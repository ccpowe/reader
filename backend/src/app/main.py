"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.avatars import router as avatars_router
from app.api.discovery import router as discovery_router
from app.api.router import api_router
from app.core.auth import AuthRateLimiter, ReaderJWTVerifier
from app.core.passwords import PasswordService
from app.core.server_access import ServerAccessMiddleware
from app.core.settings import get_settings
from app.services.auth import AuthService
from app.storage.database import build_engine, build_session_factory
from app.storage.schema import (
    REQUIRED_SCHEMA_CONTRACT,
    REQUIRED_SCHEMA_REVISION,
    RuntimeReadinessProbe,
)
from app.translation.factory import build_translation_providers, close_translation_providers
from app.translation.quota import TranslationQuotaStore
from app.translation.realtime import RealtimeCoordinator
from app.workers.health import load_worker_readiness

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create process-wide services once per API process."""
    settings = get_settings()
    app.state.auth_rate_limiter = AuthRateLimiter(
        requests=settings.auth_rate_limit_requests_per_minute,
        window_seconds=60,
    )
    engine = build_engine(settings) if settings.database_url else None
    translation_providers = build_translation_providers(settings)
    app.state.database_engine = engine
    app.state.session_factory = build_session_factory(engine) if engine else None
    app.state.jwt_verifier = ReaderJWTVerifier(settings, app.state.session_factory)
    app.state.auth_service = AuthService(settings, PasswordService())
    app.state.avatar_limiter = anyio.CapacityLimiter(1)
    app.state.password_rate_limiter = AuthRateLimiter(
        requests=settings.auth_password_attempts_per_minute,
        window_seconds=60,
    )
    app.state.translation_quota = (
        TranslationQuotaStore(app.state.session_factory, settings)
        if app.state.session_factory is not None
        else None
    )
    app.state.translation_providers = translation_providers
    app.state.realtime_translation_coordinator = RealtimeCoordinator(
        session_factory=app.state.session_factory,
        quota=app.state.translation_quota,
        max_concurrency=settings.translation_realtime_max_concurrency,
    )
    if engine is not None:
        readiness_failures = await app.state.readiness_probe.prime(
            settings,
            engine,
            timeout_seconds=settings.readiness_startup_timeout_seconds,
        )
        if readiness_failures:
            logger.warning(
                "Runtime readiness warm-up did not complete: failures=%s",
                ",".join(readiness_failures),
            )
    try:
        yield
    finally:
        await app.state.readiness_probe.close()
        await app.state.realtime_translation_coordinator.aclose(
            settings.translation_cleanup_timeout_seconds,
        )
        await close_translation_providers(
            translation_providers,
            timeout_seconds=settings.translation_cleanup_timeout_seconds,
        )
        await app.state.jwt_verifier.aclose()
        if engine is not None:
            await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title="Reader API",
        version="0.1.0",
        description="Backend for shared-source information aggregation.",
        lifespan=lifespan,
    )
    app.state.readiness_probe = RuntimeReadinessProbe(
        cache_seconds=settings.readiness_cache_seconds,
        timeout_seconds=settings.readiness_timeout_seconds,
        refresh_timeout_seconds=settings.readiness_startup_timeout_seconds,
    )
    app.add_middleware(ServerAccessMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # The deployment token protects discovery and every /v1 route before routing.
    app.include_router(discovery_router)
    app.include_router(api_router)
    app.include_router(avatars_router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc: RequestValidationError):
        # Pydantic inputs can contain passwords. Return locations and messages only.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                    for error in exc.errors()
                ]
            },
        )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    @app.get("/ready", tags=["system"])
    async def readiness_check() -> dict[str, str]:
        failures = await app.state.readiness_probe.check(
            settings,
            getattr(app.state, "database_engine", None),
        )
        if failures:
            raise HTTPException(
                status_code=503,
                detail={"status": "not_ready", "failures": failures},
            )
        return {
            "status": "ready",
            "schema_contract": REQUIRED_SCHEMA_CONTRACT,
            "build_schema_revision": REQUIRED_SCHEMA_REVISION,
        }

    @app.get("/worker-ready", tags=["system"])
    async def worker_readiness_check() -> dict[str, object]:
        session_factory = getattr(app.state, "session_factory", None)
        if session_factory is None:
            raise HTTPException(
                status_code=503,
                detail={"status": "not_ready", "failures": ["database_unavailable"]},
            )
        try:
            async with session_factory() as session:
                report = await load_worker_readiness(session)
        except Exception:
            logger.exception("Worker readiness check failed.")
            raise HTTPException(
                status_code=503,
                detail={"status": "not_ready", "failures": ["worker_health_unavailable"]},
            ) from None
        if report["status"] != "ready":
            raise HTTPException(status_code=503, detail=report)
        return report

    return app


app = create_app()
