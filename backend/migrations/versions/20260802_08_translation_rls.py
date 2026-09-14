"""Enable RLS on translation tables.

Revision ID: 20260802_08
Revises: 20260802_07
Create Date: 2026-08-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_08"
down_revision: str | Sequence[str] | None = "20260802_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRANSLATION_TABLES = (
    "user_translation_preferences",
    "content_translations",
    "translation_jobs",
)


def upgrade() -> None:
    """Prevent direct public-schema access until explicit policies exist."""
    for table in _TRANSLATION_TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in _TRANSLATION_TABLES:
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
