"""add worker-loop health and resolve stale extraction state

Revision ID: 20260828_20
Revises: 20260828_19
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_20"
down_revision: str | None = "20260828_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_loop_heartbeats",
        sa.Column("worker_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("loop_name", sa.String(length=64), nullable=False),
        sa.Column("expected_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expected_interval_seconds > 0",
            name="ck_worker_loop_heartbeats_expected_interval",
        ),
        sa.PrimaryKeyConstraint(
            "worker_instance_id",
            "loop_name",
            name="pk_worker_loop_heartbeats",
        ),
    )
    op.create_index(
        "ix_worker_loop_heartbeats_loop_updated",
        "worker_loop_heartbeats",
        ["loop_name", "updated_at"],
        unique=False,
    )
    op.execute('ALTER TABLE public."worker_loop_heartbeats" ENABLE ROW LEVEL SECURITY')
    op.execute(
        """
        UPDATE contents
        SET extraction_status = CASE
            WHEN NULLIF(btrim(COALESCE(body_text, '')), '') IS NOT NULL THEN 'success'
            WHEN kind = 'article' THEN 'failed'
            ELSE 'not_needed'
        END,
        updated_at = now()
        WHERE extraction_status = 'pending'
        """
    )
    op.execute(
        "INSERT INTO app_schema_contracts (contract) VALUES ('reader-runtime-v18') "
        "ON CONFLICT (contract) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_schema_contracts WHERE contract = 'reader-runtime-v18'")
    op.drop_index(
        "ix_worker_loop_heartbeats_loop_updated",
        table_name="worker_loop_heartbeats",
    )
    op.drop_table("worker_loop_heartbeats")
