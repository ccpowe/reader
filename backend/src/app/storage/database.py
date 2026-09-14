"""Async SQLAlchemy persistence for self-managed PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.settings import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for all application-owned PostgreSQL tables."""


def build_engine(
    settings: Settings | None = None,
    *,
    autocommit: bool = False,
    pool_size: int | None = None,
) -> AsyncEngine:
    settings = settings or get_settings()
    if not settings.database_url:
        raise RuntimeError("APP_DATABASE_URL is required for database access.")
    connect_args = {"ssl": "require"} if settings.database_ssl else {}
    options = {
        # A pre-ping is a full remote round trip on every request checkout, but
        # avoids handing idle connections closed by hosted poolers to requests.
        # Deployments that control their database lifecycle can opt out.
        "pool_pre_ping": settings.database_pool_pre_ping and not autocommit,
        "connect_args": connect_args,
        "max_overflow": 0 if autocommit else settings.database_pool_max_overflow,
        "pool_size": pool_size if pool_size is not None else settings.database_pool_size,
        "pool_timeout": settings.database_pool_timeout_seconds,
        "pool_use_lifo": True,
    }
    if autocommit:
        options.update(
            isolation_level="AUTOCOMMIT",
            skip_autocommit_rollback=True,
        )
    return create_async_engine(settings.database_url, **options)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield one transaction-capable session per HTTP request.

    Routes will start using this dependency when the domain tables are added in
    the next phase. Keeping it here now prevents persistence details leaking
    into API routes.
    """
    session_factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database has not been configured.",
        )
    async with session_factory() as session:
        yield session
