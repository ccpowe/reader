"""Store an official visual identity for shared feed sources.

Revision ID: 20260730_03
Revises: 20260727_02
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260730_03"
down_revision: str | Sequence[str] | None = "20260727_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feed_sources", sa.Column("avatar_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("feed_sources", "avatar_url")
