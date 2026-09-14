"""Install the durable source-ingestion pipeline.

Revision ID: 20260802_09
Revises: 20260802_08
Create Date: 2026-08-02

The application has not launched, so this migration performs a clean cutover:
runtime scheduling fields and per-subscription fetch limits are removed rather
than mirrored into a compatibility engine. Existing content is retained.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_09"
down_revision: str | Sequence[str] | None = "20260802_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    _merge_reddit_view_sources()
    _merge_langchain_sources()

    op.execute(
        """
        UPDATE feed_sources
        SET config = config - ARRAY[
            'max_items', 'refresh_interval_minutes', 'ranking', 'time_filter'
        ]::text[]
        """
    )
    op.execute("UPDATE feed_sources SET status = 'active' WHERE status = 'failed'")

    op.drop_index("ix_feed_sources_next_sync_at", table_name="feed_sources")
    op.drop_constraint("ck_feed_sources_status", "feed_sources", type_="check")
    for column in (
        "etag",
        "last_modified",
        "last_synced_at",
        "next_sync_at",
        "consecutive_failures",
    ):
        op.drop_column("feed_sources", column)
    op.create_check_constraint(
        "ck_feed_sources_status",
        "feed_sources",
        "status IN ('pending', 'active', 'paused')",
    )

    op.add_column("source_sync_runs", sa.Column("provider_mode", sa.String(64)))
    op.add_column(
        "source_sync_runs",
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.execute(
        """
        UPDATE source_sync_runs
        SET metrics = jsonb_build_object(
            'raw_items', items_found,
            'candidates_created', items_created
        )
        """
    )
    op.drop_column("source_sync_runs", "items_created")
    op.drop_column("source_sync_runs", "items_found")

    op.add_column("source_entries", sa.Column("feed_sort_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE source_entries SET feed_sort_at = COALESCE(published_at, fetched_at)")
    op.alter_column("source_entries", "feed_sort_at", nullable=False)
    op.drop_index("ix_source_entries_source_published", table_name="source_entries")
    op.create_index(
        "ix_source_entries_source_feed_sort",
        "source_entries",
        ["source_id", "feed_sort_at", "id"],
    )
    op.add_column(
        "content_media",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "source_sync_states",
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("committed_checkpoint", JSONB, nullable=False),
        sa.Column("pending_checkpoint", JSONB, nullable=False),
        sa.Column("continuation", JSONB),
        sa.Column("provider_mode", sa.String(64)),
        sa.Column("initial_sync_completed", sa.Boolean(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_complete_at", sa.DateTime(timezone=True)),
        sa.Column("next_scan_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("consecutive_limit_runs", sa.Integer(), nullable=False),
        sa.Column("gap_detected", sa.Boolean(), nullable=False),
        sa.Column("gap_details", JSONB),
        sa.Column("last_error_code", sa.String(120)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("lease_token", UUID),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
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
            "phase IN ('idle', 'catching_up', 'degraded')",
            name="ck_source_sync_states_phase",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["feed_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_index(
        "ix_source_sync_states_due",
        "source_sync_states",
        ["next_scan_at", "lease_expires_at"],
    )
    op.execute(
        """
        INSERT INTO source_sync_states (
            source_id, phase, committed_checkpoint, pending_checkpoint,
            provider_mode, initial_sync_completed, next_scan_at,
            consecutive_failures, consecutive_limit_runs, gap_detected
        )
        SELECT
            id,
            'idle',
            '{}'::jsonb,
            '{}'::jsonb,
            CASE WHEN kind = 'reddit' THEN 'reddit_snapshot' END,
            kind = 'reddit',
            CASE WHEN kind = 'reddit' THEN NULL ELSE now() END,
            0,
            0,
            false
        FROM feed_sources
        """
    )

    op.create_table(
        "ingestion_candidates",
        sa.Column("id", UUID, nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("native_id", sa.String(1024), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suggested_feed_sort_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_token", UUID),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("content_id", UUID),
        sa.Column("last_error_code", sa.String(120)),
        sa.Column("last_error_message", sa.Text()),
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
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_ingestion_candidates_status",
        ),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["feed_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "native_id", name="uq_ingestion_candidates_source_native"),
    )
    op.create_index(
        "ix_ingestion_candidates_claim",
        "ingestion_candidates",
        ["status", "available_at", "observed_at"],
    )
    op.create_index(
        "ix_ingestion_candidates_source_observed",
        "ingestion_candidates",
        ["source_id", "observed_at"],
    )

    op.create_table(
        "web_frontier",
        sa.Column("id", UUID, nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("discovered_from", sa.Text()),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("etag", sa.String(512)),
        sa.Column("last_modified", sa.String(512)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
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
            "status IN ('pending', 'fetched', 'uncertain', 'rejected', 'failed')",
            name="ck_web_frontier_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["feed_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "url_hash", name="uq_web_frontier_source_url_hash"),
    )
    op.create_index(
        "ix_web_frontier_due",
        "web_frontier",
        ["source_id", "status", "available_at"],
    )
    # Existing Web entries are already accepted content.  Seed the Frontier so
    # the clean cutover does not rediscover and republish them as new URLs.
    op.execute(
        """
        INSERT INTO web_frontier (
            id, source_id, original_url, normalized_url, url_hash, canonical_url,
            discovered_from, confidence, status, evidence, available_at,
            last_fetched_at, content_hash, attempt_count, created_at, updated_at
        )
        SELECT
            entry.id,
            entry.source_id,
            entry.external_url,
            entry.external_url,
            encode(sha256(convert_to(entry.external_url, 'UTF8')), 'hex'),
            entry.external_url,
            source.canonical_url,
            1.0,
            'fetched',
            jsonb_build_object('source', 'migration', 'native_id', entry.native_id),
            entry.fetched_at,
            entry.fetched_at,
            content.content_hash,
            0,
            entry.fetched_at,
            entry.fetched_at
        FROM source_entries AS entry
        JOIN feed_sources AS source ON source.id = entry.source_id
        JOIN contents AS content ON content.id = entry.content_id
        WHERE source.kind = 'web'
        ON CONFLICT (source_id, url_hash) DO NOTHING
        """
    )

    op.create_table(
        "provider_quota_usage",
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("provider", "usage_date"),
    )

    for table in (
        "source_sync_states",
        "ingestion_candidates",
        "web_frontier",
        "provider_quota_usage",
    ):
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in (
        "provider_quota_usage",
        "web_frontier",
        "ingestion_candidates",
        "source_sync_states",
    ):
        op.drop_table(table)

    op.drop_column("content_media", "is_active")
    op.drop_index("ix_source_entries_source_feed_sort", table_name="source_entries")
    op.create_index(
        "ix_source_entries_source_published", "source_entries", ["source_id", "published_at"]
    )
    op.drop_column("source_entries", "feed_sort_at")

    op.add_column(
        "source_sync_runs",
        sa.Column("items_found", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "source_sync_runs",
        sa.Column("items_created", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_column("source_sync_runs", "metrics")
    op.drop_column("source_sync_runs", "provider_mode")

    op.drop_constraint("ck_feed_sources_status", "feed_sources", type_="check")
    op.add_column("feed_sources", sa.Column("etag", sa.String(512)))
    op.add_column("feed_sources", sa.Column("last_modified", sa.String(512)))
    op.add_column("feed_sources", sa.Column("last_synced_at", sa.DateTime(timezone=True)))
    op.add_column("feed_sources", sa.Column("next_sync_at", sa.DateTime(timezone=True)))
    op.add_column(
        "feed_sources",
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_feed_sources_status",
        "feed_sources",
        "status IN ('pending', 'active', 'paused', 'failed')",
    )
    op.create_index("ix_feed_sources_next_sync_at", "feed_sources", ["next_sync_at"])


def _merge_reddit_view_sources() -> None:
    """Collapse old Hot/Top/Rising identities without retaining runtime aliases."""
    statements = (
        """
        CREATE TEMP TABLE _reddit_source_map ON COMMIT DROP AS
        WITH ranked AS (
            SELECT
                id,
                split_part(canonical_key, ':', 2) AS subreddit,
                first_value(id) OVER (
                    PARTITION BY split_part(canonical_key, ':', 2)
                    ORDER BY (config ->> 'ranking' = 'hot') DESC, created_at, id
                ) AS winner_id
            FROM feed_sources
            WHERE kind = 'reddit'
        )
        SELECT id AS old_id, winner_id, subreddit
        FROM ranked
        """,
        """
        WITH merged AS (
            SELECT
                mapping.winner_id,
                subscription.user_id,
                bool_or(subscription.is_enabled) AS is_enabled,
                (
                    array_agg(
                        subscription.custom_name
                        ORDER BY
                            (subscription.source_id = mapping.winner_id) DESC,
                            subscription.created_at,
                            subscription.id
                    ) FILTER (
                        WHERE nullif(btrim(subscription.custom_name), '') IS NOT NULL
                    )
                )[1] AS custom_name,
                (
                    array_agg(
                        subscription.folder_name
                        ORDER BY
                            (subscription.source_id = mapping.winner_id) DESC,
                            subscription.created_at,
                            subscription.id
                    ) FILTER (
                        WHERE nullif(btrim(subscription.folder_name), '') IS NOT NULL
                    )
                )[1] AS folder_name
            FROM _reddit_source_map AS mapping
            JOIN source_subscriptions AS subscription
              ON subscription.source_id = mapping.old_id
            GROUP BY mapping.winner_id, subscription.user_id
        )
        UPDATE source_subscriptions AS winner
        SET
            is_enabled = merged.is_enabled,
            custom_name = COALESCE(merged.custom_name, winner.custom_name),
            folder_name = COALESCE(merged.folder_name, winner.folder_name)
        FROM merged
        WHERE winner.source_id = merged.winner_id
          AND winner.user_id = merged.user_id
        """,
        """
        DELETE FROM source_subscriptions AS loser
        USING _reddit_source_map AS mapping
        WHERE loser.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
          AND EXISTS (
              SELECT 1 FROM source_subscriptions AS winner
              WHERE winner.source_id = mapping.winner_id
                AND winner.user_id = loser.user_id
          )
        """,
        """
        UPDATE source_subscriptions AS subscription
        SET source_id = mapping.winner_id
        FROM _reddit_source_map AS mapping
        WHERE subscription.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
        """,
        """
        DELETE FROM source_entries AS loser
        USING _reddit_source_map AS mapping
        WHERE loser.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
          AND EXISTS (
              SELECT 1 FROM source_entries AS winner
              WHERE winner.source_id = mapping.winner_id
                AND winner.native_id = loser.native_id
          )
        """,
        """
        UPDATE source_entries AS entry
        SET source_id = mapping.winner_id
        FROM _reddit_source_map AS mapping
        WHERE entry.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
        """,
        """
        UPDATE source_sync_runs AS run
        SET source_id = mapping.winner_id
        FROM _reddit_source_map AS mapping
        WHERE run.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
        """,
        """
        DELETE FROM feed_sources AS source
        USING _reddit_source_map AS mapping
        WHERE source.id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
        """,
        """
        UPDATE feed_sources AS source
        SET
            canonical_key = 'reddit:' || mapping.subreddit,
            canonical_url = 'https://www.reddit.com/r/' || mapping.subreddit || '/',
            config = jsonb_build_object('subreddit', mapping.subreddit)
        FROM _reddit_source_map AS mapping
        WHERE source.id = mapping.winner_id
          AND mapping.old_id = mapping.winner_id
        """,
    )
    for statement in statements:
        op.execute(statement)


def _merge_langchain_sources() -> None:
    """Collapse old/new LangChain blog identities into the supported listing."""
    statements = (
        """
        CREATE TEMP TABLE _langchain_source_map ON COMMIT DROP AS
        WITH candidates AS (
            SELECT
                source.id,
                source.created_at,
                (SELECT count(*) FROM source_subscriptions s WHERE s.source_id = source.id)
                    AS subscription_count,
                (SELECT count(*) FROM source_entries e WHERE e.source_id = source.id)
                    AS entry_count
            FROM feed_sources AS source
            WHERE source.kind = 'web'
              AND (
                  source.canonical_url LIKE '%://blog.langchain.com/%'
                  OR source.canonical_url IN (
                      'https://langchain.com/blog',
                      'https://langchain.com/blog/',
                      'https://www.langchain.com/blog',
                      'https://www.langchain.com/blog/'
                  )
              )
        ), ranked AS (
            SELECT
                id,
                first_value(id) OVER (
                    ORDER BY subscription_count DESC, entry_count DESC, created_at, id
                ) AS winner_id
            FROM candidates
        )
        SELECT id AS old_id, winner_id FROM ranked
        """,
        """
        WITH merged AS (
            SELECT
                mapping.winner_id,
                subscription.user_id,
                bool_or(subscription.is_enabled) AS is_enabled,
                (
                    array_agg(
                        subscription.custom_name
                        ORDER BY
                            (subscription.source_id = mapping.winner_id) DESC,
                            subscription.created_at,
                            subscription.id
                    ) FILTER (
                        WHERE nullif(btrim(subscription.custom_name), '') IS NOT NULL
                    )
                )[1] AS custom_name,
                (
                    array_agg(
                        subscription.folder_name
                        ORDER BY
                            (subscription.source_id = mapping.winner_id) DESC,
                            subscription.created_at,
                            subscription.id
                    ) FILTER (
                        WHERE nullif(btrim(subscription.folder_name), '') IS NOT NULL
                    )
                )[1] AS folder_name
            FROM _langchain_source_map AS mapping
            JOIN source_subscriptions AS subscription
              ON subscription.source_id = mapping.old_id
            GROUP BY mapping.winner_id, subscription.user_id
        )
        UPDATE source_subscriptions AS winner
        SET
            is_enabled = merged.is_enabled,
            custom_name = COALESCE(merged.custom_name, winner.custom_name),
            folder_name = COALESCE(merged.folder_name, winner.folder_name)
        FROM merged
        WHERE winner.source_id = merged.winner_id
          AND winner.user_id = merged.user_id
        """,
        """
        DELETE FROM source_subscriptions AS loser
        USING _langchain_source_map AS mapping
        WHERE loser.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
          AND EXISTS (
              SELECT 1 FROM source_subscriptions AS winner
              WHERE winner.source_id = mapping.winner_id
                AND winner.user_id = loser.user_id
          )
        """,
        """
        UPDATE source_subscriptions AS subscription
        SET source_id = mapping.winner_id
        FROM _langchain_source_map AS mapping
        WHERE subscription.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
        """,
        """
        DELETE FROM source_entries AS loser
        USING _langchain_source_map AS mapping
        WHERE loser.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
          AND EXISTS (
              SELECT 1 FROM source_entries AS winner
              WHERE winner.source_id = mapping.winner_id
                AND winner.native_id = loser.native_id
          )
        """,
        """
        UPDATE source_entries AS entry
        SET source_id = mapping.winner_id
        FROM _langchain_source_map AS mapping
        WHERE entry.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
        """,
        """
        UPDATE source_sync_runs AS run
        SET source_id = mapping.winner_id
        FROM _langchain_source_map AS mapping
        WHERE run.source_id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
        """,
        """
        DELETE FROM feed_sources AS source
        USING _langchain_source_map AS mapping
        WHERE source.id = mapping.old_id
          AND mapping.old_id <> mapping.winner_id
        """,
        """
        UPDATE feed_sources AS source
        SET
            canonical_key = 'web:https://www.langchain.com/blog',
            canonical_url = 'https://www.langchain.com/blog'
        FROM _langchain_source_map AS mapping
        WHERE source.id = mapping.winner_id
          AND mapping.old_id = mapping.winner_id
        """,
    )
    for statement in statements:
        op.execute(statement)
