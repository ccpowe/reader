"""Add safe quota audit state snapshots and operator references.

Revision ID: 20260902_22
Revises: 20260902_21
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_22"
down_revision: str | None = "20260902_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "translation_quota_audits",
        sa.Column("reference", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "translation_quota_audits",
        sa.Column("before_state", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "translation_quota_audits",
        sa.Column("after_state", postgresql.JSONB(), nullable=True),
    )

    # The CLI validates these values before opening a transaction; the same
    # restrictions live in PostgreSQL so break-glass SQL cannot write secrets
    # or arbitrary operator text into the long-lived audit table.
    op.create_check_constraint(
        "ck_translation_quota_audits_actor_safe",
        "translation_quota_audits",
        "actor ~ '^[A-Za-z0-9][A-Za-z0-9._@:+-]{0,159}$' "
        "AND lower(actor) NOT LIKE '%bearer%' "
        "AND lower(actor) NOT LIKE '%jwt%' "
        "AND lower(actor) NOT LIKE '%sk-%' "
        "AND lower(actor) NOT LIKE '%sb_%' "
        "AND actor NOT LIKE '%://%'",
    )
    op.create_check_constraint(
        "ck_translation_quota_audits_reason_code",
        "translation_quota_audits",
        "reason ~ '^[a-z][a-z0-9]*([_-][a-z0-9]+){0,7}$' "
        "AND lower(reason) NOT LIKE '%bearer%' "
        "AND lower(reason) NOT LIKE '%jwt%' "
        "AND lower(reason) NOT LIKE '%sk-%' "
        "AND lower(reason) NOT LIKE '%sb_%' "
        "AND reason NOT LIKE '%://%'",
    )
    op.create_check_constraint(
        "ck_translation_quota_audits_reference_safe",
        "translation_quota_audits",
        "reference IS NULL OR ("
        "reference ~ '^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$' "
        "AND lower(reference) NOT LIKE '%bearer%' "
        "AND lower(reference) NOT LIKE '%jwt%' "
        "AND lower(reference) NOT LIKE '%sk-%' "
        "AND lower(reference) NOT LIKE '%sb_%' "
        "AND reference NOT LIKE '%://%')",
    )
    state_keys = "ARRAY['requests', 'actual_miss_chars', 'is_disabled']"
    for column, constraint in (
        ("before_state", "ck_translation_quota_audits_before_state"),
        ("after_state", "ck_translation_quota_audits_after_state"),
    ):
        op.create_check_constraint(
            constraint,
            "translation_quota_audits",
            f"{column} IS NULL OR {column} = 'null'::jsonb OR ("
            f"jsonb_typeof({column}) = 'object' "
            f"AND {column} ?& {state_keys} "
            f"AND ({column} - 'requests' - 'actual_miss_chars' - 'is_disabled') "
            "= '{}'::jsonb)",
        )

    op.execute(
        "INSERT INTO app_schema_contracts (contract) VALUES ('reader-runtime-v20') "
        "ON CONFLICT (contract) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_schema_contracts WHERE contract = 'reader-runtime-v20'")
    for constraint in (
        "ck_translation_quota_audits_after_state",
        "ck_translation_quota_audits_before_state",
        "ck_translation_quota_audits_reference_safe",
        "ck_translation_quota_audits_reason_code",
        "ck_translation_quota_audits_actor_safe",
    ):
        op.drop_constraint(constraint, "translation_quota_audits", type_="check")
    op.drop_column("translation_quota_audits", "after_state")
    op.drop_column("translation_quota_audits", "before_state")
    op.drop_column("translation_quota_audits", "reference")
