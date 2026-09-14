"""Persist bounded host-requested rule Agent Goal continuations.

Revision ID: 20260910_27
Revises: 20260910_26
"""

import sqlalchemy as sa
from alembic import op

revision = "20260910_27"
down_revision = "20260910_26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "web_rule_jobs",
        sa.Column("goal_resumptions_reserved", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_web_rule_jobs_goal_resumptions", "web_rule_jobs", "goal_resumptions_reserved >= 0"
    )
    op.execute(
        "INSERT INTO app_schema_contracts (contract) VALUES ('reader-runtime-v23') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_schema_contracts WHERE contract = 'reader-runtime-v23'")
    op.drop_constraint("ck_web_rule_jobs_goal_resumptions", "web_rule_jobs", type_="check")
    op.drop_column("web_rule_jobs", "goal_resumptions_reserved")
