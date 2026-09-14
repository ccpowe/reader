"""Store shared provider ranking snapshots.

Revision ID: 20260730_04
Revises: 20260730_03
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260730_04"
down_revision: str | Sequence[str] | None = "20260730_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ranking_snapshots",
        sa.Column("cache_key", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_index("ix_ranking_snapshots_next_refresh", "ranking_snapshots", ["next_refresh_at"])
    op.execute('ALTER TABLE public."ranking_snapshots" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_index("ix_ranking_snapshots_next_refresh", table_name="ranking_snapshots")
    op.drop_table("ranking_snapshots")
