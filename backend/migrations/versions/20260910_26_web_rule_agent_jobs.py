"""Durable backend Web rule authoring jobs, leases and engine pause state.

Revision ID: 20260910_26
Revises: 20260909_25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "20260910_26"
down_revision = "20260909_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_sync_states",
        sa.Column("web_rule_structural_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "source_sync_states", sa.Column("web_rule_failure_episode_id", pg.UUID(as_uuid=True))
    )
    op.execute(
        "UPDATE feed_sources SET config = config || jsonb_build_object('web_rule_revision', 0) "
        "WHERE kind = 'web' AND NOT (config ? 'web_rule_revision')"
    )
    op.create_table(
        "web_rule_jobs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("feed_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("failure_episode_id", pg.UUID(as_uuid=True)),
        sa.Column(
            "retry_of",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("web_rule_jobs.id", ondelete="SET NULL"),
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("base_rule_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("base_rule_hash", sa.String(64)),
        sa.Column("base_rule", pg.JSONB()),
        sa.Column("prompt_version", sa.String(120)),
        sa.Column("rule_schema_version", sa.String(120)),
        sa.Column("validator_version", sa.String(120)),
        sa.Column("engine_snapshot", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("limits_snapshot", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(24), nullable=False, server_default="preflight"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", pg.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        *[
            sa.Column(name, sa.Integer(), nullable=False, server_default="0")
            for name in (
                "model_calls_reserved",
                "tool_calls_reserved",
                "validation_calls_reserved",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "unknown_usage_count",
                "conservative_tokens_reserved",
            )
        ],
        sa.Column("usage_reservations", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("candidate_rule", pg.JSONB()),
        sa.Column("validation_report", pg.JSONB()),
        sa.Column("validation_receipt", pg.JSONB()),
        sa.Column("activated_version", sa.String(64)),
        sa.Column("last_error_code", sa.String(120)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("diagnostics", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_web_rule_jobs_idempotency"),
        sa.CheckConstraint("reason IN ('author', 'repair')", name="ck_web_rule_jobs_reason"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', "
            "'failed', 'blocked', 'cancelled')",
            name="ck_web_rule_jobs_status",
        ),
    )
    op.create_index("ix_web_rule_jobs_due", "web_rule_jobs", ["status", "available_at"])
    op.create_index("ix_web_rule_jobs_source_created", "web_rule_jobs", ["source_id", "created_at"])
    op.create_index(
        "uq_web_rule_jobs_active_source",
        "web_rule_jobs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'retry_wait')"),
    )
    op.create_table(
        "web_rule_agent_runtime",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "current_job_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("web_rule_jobs.id", ondelete="SET NULL"),
        ),
        sa.Column("lease_token", pg.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("engine_snapshot", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("paused_code", sa.String(120)),
        sa.Column("paused_message", sa.Text()),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("resume_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("id = 'default'", name="ck_web_rule_runtime_singleton"),
    )
    op.execute("INSERT INTO web_rule_agent_runtime (id) VALUES ('default')")
    op.execute("ALTER TABLE public.web_rule_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.web_rule_agent_runtime ENABLE ROW LEVEL SECURITY")
    op.execute(
        "INSERT INTO app_schema_contracts (contract) VALUES ('reader-runtime-v22') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_schema_contracts WHERE contract = 'reader-runtime-v22'")
    op.drop_table("web_rule_agent_runtime")
    op.drop_table("web_rule_jobs")
    op.drop_column("source_sync_states", "web_rule_failure_episode_id")
    op.drop_column("source_sync_states", "web_rule_structural_failures")
