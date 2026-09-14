"""Transaction-scoped PostgreSQL advisory locks for user-owned mutations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def acquire_user_transaction_lock(
    session: AsyncSession,
    *,
    namespace: str,
    user_id: UUID,
) -> None:
    """Serialize one narrow class of mutations for a user until commit/rollback."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"{namespace}:{user_id}"},
    )
