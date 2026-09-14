"""Idempotent profile persistence shared by authenticated write paths."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import Profile


async def ensure_profile(session: AsyncSession, user_id: UUID) -> None:
    """Create a legacy user's profile without a check-then-insert race."""
    await session.execute(
        pg_insert(Profile).values(id=user_id).on_conflict_do_nothing(index_elements=[Profile.id])
    )
