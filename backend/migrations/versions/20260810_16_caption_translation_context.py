"""persist neighboring context for caption translation work

Revision ID: 20260810_16
Revises: 20260805_15
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_16"
down_revision: str | None = "20260805_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("translation_work", sa.Column("context_before", sa.Text(), nullable=True))
    op.add_column("translation_work", sa.Column("context_after", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("translation_work", "context_after")
    op.drop_column("translation_work", "context_before")
