"""rebuild source-owned content and add bounded runtime safety state

Revision ID: 20260811_17
Revises: 20260810_16
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260811_17"
down_revision: str | None = "20260810_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contents",
        sa.Column("authority_source_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    _reset_content_projection()
    op.drop_index("uq_contents_url_hash", table_name="contents")
    op.alter_column("contents", "authority_source_id", nullable=False)
    op.create_foreign_key(
        "fk_contents_authority_source_id_feed_sources",
        "contents",
        "feed_sources",
        ["authority_source_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_contents_id_authority_source",
        "contents",
        ["id", "authority_source_id"],
    )
    op.drop_constraint(
        "source_entries_content_id_fkey",
        "source_entries",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_source_entries_content_authority",
        "source_entries",
        "contents",
        ["content_id", "source_id"],
        ["id", "authority_source_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_contents_authority_source_id",
        "contents",
        ["authority_source_id"],
    )
    op.create_index(
        "uq_contents_authority_url_hash",
        "contents",
        ["authority_source_id", "url_hash"],
        unique=True,
        postgresql_where=sa.text("url_hash IS NOT NULL"),
    )

    op.add_column(
        "ranking_snapshots",
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "ranking_snapshots",
        sa.Column("is_ready", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.alter_column("ranking_snapshots", "is_ready", server_default=sa.false())
    op.add_column(
        "ranking_snapshots",
        sa.Column("refresh_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "ranking_snapshots",
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ranking_snapshots",
        sa.Column("manual_refresh_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_ranking_snapshots_refresh_lease",
        "ranking_snapshots",
        "(refresh_token IS NULL) = (refresh_expires_at IS NULL)",
    )
    op.create_index(
        "ix_ranking_snapshots_inactive",
        "ranking_snapshots",
        ["kind", "last_accessed_at"],
    )
    _reconcile_reddit_snapshots()
    op.execute(
        """
        UPDATE ranking_snapshots
        SET next_refresh_at = now(),
            is_ready = CASE
                WHEN jsonb_typeof(payload -> 'items') = 'array'
                    THEN jsonb_array_length(payload -> 'items') > 0
                ELSE false
            END,
            refresh_token = NULL,
            refresh_expires_at = NULL,
            last_error = NULL
        """
    )
    op.create_table(
        "ranking_provider_budgets",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_count", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "refresh_count >= 0",
            name="ck_ranking_provider_budget_count",
        ),
        sa.PrimaryKeyConstraint("provider"),
    )
    op.execute('ALTER TABLE public."ranking_provider_budgets" ENABLE ROW LEVEL SECURITY')

    op.drop_index("ix_translation_work_claim", table_name="translation_work")
    op.create_index(
        "ix_translation_work_claim",
        "translation_work",
        [
            "engine_fingerprint",
            "status",
            "available_at",
            "priority",
            "created_at",
        ],
    )
    op.create_table(
        "app_schema_contracts",
        sa.Column("contract", sa.String(length=64), nullable=False),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("contract"),
    )
    op.execute(
        "INSERT INTO app_schema_contracts (contract) VALUES ('reader-runtime-v17')"
    )
    op.execute('ALTER TABLE public."app_schema_contracts" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table("app_schema_contracts")
    op.drop_index("ix_translation_work_claim", table_name="translation_work")
    op.create_index(
        "ix_translation_work_claim",
        "translation_work",
        ["engine_id", "status", "available_at", "priority", "created_at"],
    )

    op.drop_table("ranking_provider_budgets")
    op.execute(
        "UPDATE ranking_snapshots SET next_refresh_at = now(), last_error = NULL"
    )
    op.drop_index("ix_ranking_snapshots_inactive", table_name="ranking_snapshots")
    op.drop_constraint(
        "ck_ranking_snapshots_refresh_lease",
        "ranking_snapshots",
        type_="check",
    )
    op.drop_column("ranking_snapshots", "manual_refresh_after")
    op.drop_column("ranking_snapshots", "refresh_expires_at")
    op.drop_column("ranking_snapshots", "refresh_token")
    op.drop_column("ranking_snapshots", "is_ready")
    op.drop_column("ranking_snapshots", "last_accessed_at")

    op.drop_index("uq_contents_authority_url_hash", table_name="contents")
    _reset_content_projection()
    op.drop_constraint(
        "fk_source_entries_content_authority",
        "source_entries",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "source_entries_content_id_fkey",
        "source_entries",
        "contents",
        ["content_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_contents_id_authority_source",
        "contents",
        type_="unique",
    )
    op.drop_index("ix_contents_authority_source_id", table_name="contents")
    op.drop_constraint(
        "fk_contents_authority_source_id_feed_sources",
        "contents",
        type_="foreignkey",
    )
    op.drop_column("contents", "authority_source_id")
    op.create_index(
        "uq_contents_url_hash",
        "contents",
        ["url_hash"],
        unique=True,
        postgresql_where=sa.text("url_hash IS NOT NULL"),
    )


def _reset_content_projection() -> None:
    """Discard provenance-ambiguous projections and schedule authoritative rebuilds.

    Pre-revision-17 Content was globally mutable by every Source that declared
    the same URL, so its body/media cannot be attributed safely after the fact.
    Source subscriptions survive; scanners recreate Source-owned Content.
    """
    op.execute(
        """
        UPDATE source_sync_runs
        SET status = 'partial',
            finished_at = now(),
            error_code = 'schema_rebuild',
            error_message = 'Content projection reset by schema migration.'
        WHERE status = 'running'
        """
    )
    op.execute("DELETE FROM ingestion_candidates")
    op.execute("DELETE FROM web_frontier")
    # Cascades through SourceEntry, ContentMedia, reading state, and saved rows.
    op.execute("DELETE FROM contents")
    op.execute(
        """
        UPDATE source_sync_states AS state
        SET phase = 'idle',
            committed_checkpoint = '{}'::jsonb,
            pending_checkpoint = '{}'::jsonb,
            continuation = NULL,
            provider_mode = NULL,
            initial_sync_completed = false,
            last_attempt_at = NULL,
            last_complete_at = NULL,
            next_scan_at = now(),
            consecutive_failures = 0,
            consecutive_limit_runs = 0,
            gap_detected = false,
            gap_details = NULL,
            last_error_code = NULL,
            last_error_message = NULL,
            lease_token = NULL,
            lease_expires_at = NULL,
            updated_at = now()
        FROM feed_sources AS source
        WHERE state.source_id = source.id
          AND source.kind NOT IN ('reddit', 'hackernews')
        """
    )
    op.execute(
        """
        UPDATE feed_sources
        SET status = 'pending', updated_at = now()
        WHERE status != 'paused'
          AND kind NOT IN ('reddit', 'hackernews')
        """
    )


def _reconcile_reddit_snapshots() -> None:
    """Repair the historical subscription/Snapshot two-transaction window."""
    op.execute(
        """
        /* reddit_snapshot_reconciliation */
        WITH subscribed_reddit AS (
            SELECT DISTINCT lower(split_part(source.canonical_key, ':', 2)) AS subreddit
            FROM feed_sources AS source
            JOIN source_subscriptions AS subscription ON subscription.source_id = source.id
            WHERE source.kind = 'reddit'
              AND source.canonical_key LIKE 'reddit:%'
              AND subscription.is_enabled
        )
        INSERT INTO ranking_snapshots (
            cache_key, kind, parameters, payload, fetched_at, next_refresh_at,
            last_accessed_at, is_ready, refresh_token, refresh_expires_at,
            manual_refresh_after, last_error
        )
        SELECT
            'ranking:v2:reddit:' || subscribed.subreddit || ':' || board.sort || ':' ||
                board.cache_window,
            'reddit',
            jsonb_build_object(
                'subreddit', subscribed.subreddit,
                'sort', board.sort,
                'time_filter', board.time_filter
            ),
            jsonb_build_object(
                'title', 'r/' || subscribed.subreddit,
                'subtitle', board.subtitle,
                'items', '[]'::jsonb
            ),
            now(), now(), now(), false, NULL, NULL, NULL, NULL
        FROM subscribed_reddit AS subscribed
        CROSS JOIN (VALUES
            ('hot', 'week', '-', 'Hot'),
            ('rising', 'week', '-', 'Rising'),
            ('top', 'week', 'week', 'Top')
        ) AS board(sort, time_filter, cache_window, subtitle)
        ON CONFLICT (cache_key) DO NOTHING
        """
    )
