"""Make shared content and media persistence safe under concurrent syncs.

Revision ID: 20260802_06
Revises: 20260801_05
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_06"
down_revision: str | Sequence[str] | None = "20260801_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # asyncpg prepares each op.execute() statement separately.  Keep every SQL
    # command in its own call while preserving Alembic's surrounding transaction
    # so the temporary mapping table remains available throughout the merge.
    for statement in _dedupe_statements():
        op.execute(statement)

    op.create_index(
        "uq_contents_url_hash",
        "contents",
        ["url_hash"],
        unique=True,
        postgresql_where=sa.text("url_hash IS NOT NULL"),
    )


def _dedupe_statements() -> tuple[str, ...]:
    """Return one PostgreSQL command per string for asyncpg compatibility."""
    return (
        """
        CREATE TEMP TABLE _content_dedupe_map (
            duplicate_id uuid PRIMARY KEY,
            survivor_id uuid NOT NULL
        ) ON COMMIT DROP
        """,
        """
        INSERT INTO _content_dedupe_map (duplicate_id, survivor_id)
        SELECT id, survivor_id
        FROM (
            SELECT
                id,
                FIRST_VALUE(id) OVER (
                    PARTITION BY url_hash
                    ORDER BY created_at, id
                ) AS survivor_id,
                ROW_NUMBER() OVER (
                    PARTITION BY url_hash
                    ORDER BY created_at, id
                ) AS duplicate_rank
            FROM contents
            WHERE url_hash IS NOT NULL
        ) ranked
        WHERE duplicate_rank > 1
        """,
        """
        UPDATE source_entries AS entry
        SET content_id = mapping.survivor_id
        FROM _content_dedupe_map AS mapping
        WHERE entry.content_id = mapping.duplicate_id
        """,
        """
        WITH ranked_media AS (
            SELECT
                media.id,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(mapping.survivor_id, media.content_id), media.original_url
                    ORDER BY (mapping.duplicate_id IS NULL) DESC, media.id
                ) AS duplicate_rank
            FROM content_media AS media
            LEFT JOIN _content_dedupe_map AS mapping
                ON media.content_id = mapping.duplicate_id
        )
        DELETE FROM content_media AS media
        USING ranked_media
        WHERE media.id = ranked_media.id
          AND ranked_media.duplicate_rank > 1
        """,
        """
        UPDATE content_media AS media
        SET content_id = mapping.survivor_id
        FROM _content_dedupe_map AS mapping
        WHERE media.content_id = mapping.duplicate_id
        """,
        """
        WITH merged_state AS (
            SELECT
                mapping.survivor_id,
                duplicate.user_id,
                BOOL_OR(duplicate.is_read) AS is_read,
                MAX(duplicate.read_at) AS read_at,
                BOOL_OR(duplicate.offline_requested) AS offline_requested,
                MAX(duplicate.last_opened_at) AS last_opened_at,
                MAX(duplicate.progress) AS progress,
                MAX(duplicate.updated_at) AS updated_at
            FROM user_content_states AS duplicate
            JOIN _content_dedupe_map AS mapping
                ON duplicate.content_id = mapping.duplicate_id
            GROUP BY mapping.survivor_id, duplicate.user_id
        )
        UPDATE user_content_states AS survivor
        SET
            is_read = survivor.is_read OR merged_state.is_read,
            read_at = COALESCE(
                GREATEST(survivor.read_at, merged_state.read_at),
                survivor.read_at,
                merged_state.read_at
            ),
            offline_requested = survivor.offline_requested OR merged_state.offline_requested,
            last_opened_at = COALESCE(
                GREATEST(survivor.last_opened_at, merged_state.last_opened_at),
                survivor.last_opened_at,
                merged_state.last_opened_at
            ),
            progress = COALESCE(
                GREATEST(survivor.progress, merged_state.progress),
                survivor.progress,
                merged_state.progress
            ),
            updated_at = GREATEST(survivor.updated_at, merged_state.updated_at)
        FROM merged_state
        WHERE survivor.user_id = merged_state.user_id
          AND survivor.content_id = merged_state.survivor_id
        """,
        """
        DELETE FROM user_content_states AS state
        USING _content_dedupe_map AS mapping
        WHERE state.content_id = mapping.duplicate_id
          AND EXISTS (
              SELECT 1
              FROM user_content_states AS survivor
              WHERE survivor.user_id = state.user_id
                AND survivor.content_id = mapping.survivor_id
          )
        """,
        """
        WITH ranked_states AS (
            SELECT
                state.user_id,
                state.content_id,
                ROW_NUMBER() OVER (
                    PARTITION BY state.user_id, mapping.survivor_id
                    ORDER BY state.updated_at DESC, state.content_id
                ) AS duplicate_rank
            FROM user_content_states AS state
            JOIN _content_dedupe_map AS mapping
                ON state.content_id = mapping.duplicate_id
        )
        DELETE FROM user_content_states AS state
        USING ranked_states
        WHERE state.user_id = ranked_states.user_id
          AND state.content_id = ranked_states.content_id
          AND ranked_states.duplicate_rank > 1
        """,
        """
        UPDATE user_content_states AS state
        SET content_id = mapping.survivor_id
        FROM _content_dedupe_map AS mapping
        WHERE state.content_id = mapping.duplicate_id
        """,
        """
        WITH merged_saved AS (
            SELECT
                mapping.survivor_id,
                duplicate.user_id,
                MAX(duplicate.saved_at) AS saved_at,
                MAX(duplicate.collection_name) FILTER (
                    WHERE duplicate.collection_name IS NOT NULL
                ) AS collection_name,
                MAX(duplicate.note) FILTER (WHERE duplicate.note IS NOT NULL) AS note
            FROM user_saved_contents AS duplicate
            JOIN _content_dedupe_map AS mapping
                ON duplicate.content_id = mapping.duplicate_id
            GROUP BY mapping.survivor_id, duplicate.user_id
        )
        UPDATE user_saved_contents AS survivor
        SET
            saved_at = GREATEST(survivor.saved_at, merged_saved.saved_at),
            collection_name = COALESCE(survivor.collection_name, merged_saved.collection_name),
            note = COALESCE(survivor.note, merged_saved.note)
        FROM merged_saved
        WHERE survivor.user_id = merged_saved.user_id
          AND survivor.content_id = merged_saved.survivor_id
        """,
        """
        DELETE FROM user_saved_contents AS saved
        USING _content_dedupe_map AS mapping
        WHERE saved.content_id = mapping.duplicate_id
          AND EXISTS (
              SELECT 1
              FROM user_saved_contents AS survivor
              WHERE survivor.user_id = saved.user_id
                AND survivor.content_id = mapping.survivor_id
          )
        """,
        """
        WITH ranked_saved AS (
            SELECT
                saved.id,
                ROW_NUMBER() OVER (
                    PARTITION BY saved.user_id, mapping.survivor_id
                    ORDER BY saved.saved_at DESC, saved.id
                ) AS duplicate_rank
            FROM user_saved_contents AS saved
            JOIN _content_dedupe_map AS mapping
                ON saved.content_id = mapping.duplicate_id
        )
        DELETE FROM user_saved_contents AS saved
        USING ranked_saved
        WHERE saved.id = ranked_saved.id
          AND ranked_saved.duplicate_rank > 1
        """,
        """
        UPDATE user_saved_contents AS saved
        SET content_id = mapping.survivor_id
        FROM _content_dedupe_map AS mapping
        WHERE saved.content_id = mapping.duplicate_id
        """,
        """
        DELETE FROM contents AS content
        USING _content_dedupe_map AS mapping
        WHERE content.id = mapping.duplicate_id
        """,
    )


def downgrade() -> None:
    op.drop_index("uq_contents_url_hash", table_name="contents")
