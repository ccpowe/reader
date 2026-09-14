"""Add durable translation quota overrides, audit history, and worker metrics.

Revision ID: 20260902_21
Revises: 20260828_20
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_21"
down_revision: str | None = "20260828_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ranking_snapshots",
        sa.Column("last_metrics", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "worker_loop_heartbeats",
        sa.Column("last_metrics", postgresql.JSONB(), nullable=True),
    )

    op.create_table(
        "translation_quota_buckets",
        sa.Column("bucket_key", sa.String(length=128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requests_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actual_miss_chars_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "requests_used >= 0", name="ck_translation_quota_requests_used"
        ),
        sa.CheckConstraint(
            "actual_miss_chars_used >= 0",
            name="ck_translation_quota_actual_miss_chars_used",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bucket_key"),
    )
    op.create_index(
        "ix_translation_quota_buckets_user_window",
        "translation_quota_buckets",
        ["user_id", "window_started_at"],
    )

    op.create_table(
        "translation_quota_overrides",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=True),
        sa.Column("actual_miss_chars", sa.Integer(), nullable=True),
        sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
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
            "requests IS NULL OR requests >= 0",
            name="ck_translation_override_requests",
        ),
        sa.CheckConstraint(
            "actual_miss_chars IS NULL OR actual_miss_chars >= 0",
            name="ck_translation_override_chars",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "translation_quota_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=True),
        sa.Column("actual_miss_chars", sa.Integer(), nullable=True),
        sa.Column("is_disabled", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_translation_quota_audits_user_created",
        "translation_quota_audits",
        ["user_id", "created_at"],
    )

    # These are backend-owned tables. RLS prevents accidental direct access
    # through Supabase's public PostgREST surface; the service connection is
    # the only supported access path.
    for table in (
        "translation_quota_buckets",
        "translation_quota_overrides",
        "translation_quota_audits",
    ):
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')

    # Audit history is deliberately append-only. The database guard protects
    # this invariant even if a future CLI or break-glass SQL session is
    # accidentally granted broader privileges.
    op.execute(
        """
        CREATE FUNCTION public.prevent_translation_quota_audit_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'translation quota audit is append-only';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER translation_quota_audits_append_only
        BEFORE UPDATE OR DELETE ON public.translation_quota_audits
        FOR EACH ROW EXECUTE FUNCTION public.prevent_translation_quota_audit_mutation()
        """
    )
    op.execute(
        "INSERT INTO app_schema_contracts (contract) VALUES ('reader-runtime-v19') "
        "ON CONFLICT (contract) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_schema_contracts WHERE contract = 'reader-runtime-v19'")
    op.execute(
        "DROP TRIGGER IF EXISTS translation_quota_audits_append_only "
        "ON public.translation_quota_audits"
    )
    op.execute("DROP FUNCTION IF EXISTS public.prevent_translation_quota_audit_mutation()")
    op.drop_index(
        "ix_translation_quota_audits_user_created",
        table_name="translation_quota_audits",
    )
    op.drop_table("translation_quota_audits")
    op.drop_table("translation_quota_overrides")
    op.drop_index(
        "ix_translation_quota_buckets_user_window",
        table_name="translation_quota_buckets",
    )
    op.drop_table("translation_quota_buckets")
    op.drop_column("worker_loop_heartbeats", "last_metrics")
    op.drop_column("ranking_snapshots", "last_metrics")
