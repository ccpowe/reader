"""Add durable per-channel update cursors and home inclusion.

Revision ID: 20260922_29
Revises: 20260912_28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260922_29"
down_revision = "20260912_28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE source_entry_update_sequence")
    op.add_column(
        "feed_sources",
        sa.Column(
            "latest_update_sequence",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "source_subscriptions",
        sa.Column(
            "include_in_home",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "source_subscriptions",
        sa.Column(
            "last_viewed_update_sequence",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "ingestion_candidates",
        sa.Column(
            "counts_as_update",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "source_entries",
        sa.Column("update_sequence", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_source_entries_source_update_sequence",
        "source_entries",
        ["source_id", "update_sequence"],
    )
    # Existing entries and candidates are the installed baseline. A Reddit
    # source with no completed scan, entry, or candidate has not received its
    # first hot snapshot yet and must keep that future batch as baseline.
    op.execute("""
        UPDATE source_sync_states AS state
        SET committed_checkpoint = coalesce(state.committed_checkpoint, '{}'::jsonb)
            || jsonb_build_object('reddit_hot_baseline_received', true)
        FROM feed_sources AS source
        WHERE source.id = state.source_id
          AND source.kind = 'reddit'
          AND (
            state.last_complete_at IS NOT NULL
            OR EXISTS (
              SELECT 1 FROM source_entries AS entry
              WHERE entry.source_id = source.id
            )
            OR EXISTS (
              SELECT 1 FROM ingestion_candidates AS candidate
              WHERE candidate.source_id = source.id
            )
          )
    """)
    op.execute(
        "INSERT INTO app_schema_contracts (contract) VALUES "
        "('reader-runtime-v25') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_schema_contracts WHERE contract = 'reader-runtime-v25'")
    op.drop_index("ix_source_entries_source_update_sequence", table_name="source_entries")
    op.drop_column("source_entries", "update_sequence")
    op.drop_column("ingestion_candidates", "counts_as_update")
    op.drop_column("source_subscriptions", "last_viewed_update_sequence")
    op.drop_column("source_subscriptions", "include_in_home")
    op.drop_column("feed_sources", "latest_update_sequence")
    op.execute("DROP SEQUENCE source_entry_update_sequence")
