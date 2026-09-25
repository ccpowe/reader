"""Move candidate processing semantics out of provider metadata.

Revision ID: 20260925_30
Revises: 20260922_29
"""

from alembic import op

revision = "20260925_30"
down_revision = "20260922_29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rewrite only the payload. In particular, the stored material hash, claim
    # state, and source checkpoints describe already-observed work and stay put.
    op.execute("""
        UPDATE ingestion_candidates
        SET payload = jsonb_set(
            jsonb_set(
                CASE
                    WHEN jsonb_typeof(payload -> 'raw_metadata') = 'object'
                    THEN jsonb_set(
                        payload, '{raw_metadata}',
                        (payload -> 'raw_metadata') - 'availability' - 'web_discovery_only'
                    )
                    ELSE payload
                END,
                '{availability}',
                CASE WHEN payload -> 'raw_metadata' ->> 'availability' = 'unavailable'
                     THEN '"unavailable"'::jsonb ELSE '"available"'::jsonb END
            ),
            '{observation}',
            CASE WHEN payload -> 'raw_metadata' ->> 'web_discovery_only' = 'true'
                 AND jsonb_typeof(payload -> 'raw_metadata' -> 'web_discovery_only') = 'boolean'
                 THEN '"listing"'::jsonb ELSE '"full"'::jsonb END
        )
    """)
    op.execute(
        "INSERT INTO app_schema_contracts (contract) VALUES "
        "('reader-runtime-v26') ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_schema_contracts WHERE contract = 'reader-runtime-v26'")
    op.execute("""
        UPDATE ingestion_candidates
        SET payload = CASE
            WHEN jsonb_typeof(payload -> 'raw_metadata') = 'object'
              OR payload ->> 'availability' = 'unavailable'
              OR payload ->> 'observation' = 'listing'
            THEN jsonb_set(
                payload - 'availability' - 'observation',
                '{raw_metadata}',
                (CASE WHEN jsonb_typeof(payload -> 'raw_metadata') = 'object'
                      THEN (payload -> 'raw_metadata') - 'availability' - 'web_discovery_only'
                      ELSE '{}'::jsonb END)
                || CASE WHEN payload ->> 'availability' = 'unavailable'
                        THEN jsonb_build_object('availability', 'unavailable')
                        ELSE '{}'::jsonb END
                || CASE WHEN payload ->> 'observation' = 'listing'
                        THEN jsonb_build_object('web_discovery_only', true)
                        ELSE '{}'::jsonb END
            )
            ELSE payload - 'availability' - 'observation'
        END
    """)
