"""Add indexes for user-scoped feed and cursor-page lookups."""

from alembic import op


revision = "20260801_05"
down_revision = "20260730_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_source_subscriptions_user_enabled_folder_source",
        "source_subscriptions",
        ["user_id", "is_enabled", "folder_name", "source_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_subscriptions_user_enabled_folder_source", table_name="source_subscriptions")
