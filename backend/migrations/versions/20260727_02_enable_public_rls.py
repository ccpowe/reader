"""Enable RLS for all tables exposed in Supabase's public schema.

Revision ID: 20260727_02
Revises: 20260727_01
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260727_02"
down_revision: str | Sequence[str] | None = "20260727_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PUBLIC_TABLES = (
    "profiles",
    "alembic_version",
    "feed_sources",
    "source_subscriptions",
    "source_sync_runs",
    "contents",
    "source_entries",
    "content_media",
    "user_content_states",
    "user_saved_contents",
)


def upgrade() -> None:
    for table in PUBLIC_TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in PUBLIC_TABLES:
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
