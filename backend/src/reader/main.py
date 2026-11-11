"""FastAPI application: wiring only, no business logic."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from reader.core.db import build_engine, build_session_factory
from reader.core.errors import install_error_handlers
from reader.core.server_access import ServerAccessMiddleware
from reader.core.settings import get_settings
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
    app.include_router(system_router)
    return app


app = create_app()
