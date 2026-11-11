"""FastAPI application: wiring only, no business logic."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from reader.accounts.api import auth_router, avatar_router, me_router
from reader.core.db import build_engine, build_session_factory
from reader.core.errors import install_error_handlers
from reader.core.rate_limit import RateLimiter
from reader.core.server_access import ServerAccessMiddleware
from reader.core.settings import get_settings
from reader.subscriptions.api import router as subscriptions_router
from reader.subscriptions.api import sources_router
from reader.system.api import router as system_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = build_engine(settings) if settings.database_url else None
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine) if engine else None
    try:
        yield
    finally:
        if engine is not None:
            await engine.dispose()


def _openapi_with_combined_security(app: FastAPI):
    """FastAPI lists each auth dependency as an alternative ("either"); Reader needs
    the server token and the user token together, so merge them into one requirement.
    """

    def openapi() -> dict:
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            for path in schema.get("paths", {}).values():
                for operation in path.values():
                    requirements = operation.get("security")
                    if requirements and len(requirements) > 1:
                        merged: dict[str, list] = {}
                        for requirement in requirements:
                            merged.update(requirement)
                        operation["security"] = [merged]
            app.openapi_schema = schema
        return app.openapi_schema

    return openapi


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    app = FastAPI(title="Reader API", version="3", lifespan=lifespan)
    # Middleware runs in reverse order of addition: CORS first, then the token gate.
    app.add_middleware(ServerAccessMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    # Failed access-token checks, and every password-checking request, per client.
    app.state.token_limiter = RateLimiter(
        requests=settings.auth_rate_limit_requests_per_minute, window_seconds=60
    )
    app.state.password_limiter = RateLimiter(
        requests=settings.auth_password_attempts_per_minute, window_seconds=60
    )
    for router in (
        system_router,
        auth_router,
        me_router,
        avatar_router,
        subscriptions_router,
        sources_router,
    ):
        app.include_router(router)
    app.openapi = _openapi_with_combined_security(app)
    return app


app = create_app()
