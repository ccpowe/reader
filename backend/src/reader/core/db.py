"""Async SQLAlchemy engine, sessions and the declarative base."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from reader.core.errors import AppError
from reader.core.settings import Settings


class Base(DeclarativeBase):
    """Base class of every Reader table."""


def build_engine(settings: Settings) -> AsyncEngine:
    if not settings.database_url:
        raise RuntimeError("APP_DATABASE_URL is required for database access.")
    return create_async_engine(
        settings.database_url,
        connect_args={"ssl": "require"} if settings.database_ssl else {},
        pool_pre_ping=True,
        # Errors and logs never show bound values (texts, hashes, tokens).
        hide_parameters=True,
        pool_size=settings.database_pool_size,
        max_overflow=0,
        pool_timeout=settings.database_pool_timeout_seconds,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """One session per HTTP request; routes commit explicitly."""
    factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "session_factory", None
    )
    if factory is None:
        raise AppError(503, "database_unavailable", "The database is not configured.")
    async with factory() as session:
        yield session
