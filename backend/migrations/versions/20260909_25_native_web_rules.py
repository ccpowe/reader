"""Retire existing Web rules so subscriptions are re-authored with Crawl4AI.

Revision ID: 20260909_25
Revises: 20260908_24

The owner explicitly requested removal, not compatibility. Stop API/worker
processes for migration. Subscriptions, contents, saved entries and RSS sources
survive; unprocessed Web work and rule-derived progress do not.
"""

from alembic import op

revision = "20260909_25"
down_revision = "20260908_24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE feed_sources
        SET config = config - ARRAY[
            'web_rule', 'web_rule_active_version', 'web_rule_versions',
            'web_rule_candidate', 'profile_id', 'web_profile_id', 'profile',
            'feed_url', 'web_rss'
        ]::text[], updated_at = now()
        WHERE kind = 'web'
    """)
    op.execute("""
        INSERT INTO source_sync_states (
            source_id, phase, committed_checkpoint, pending_checkpoint,
            initial_sync_completed, consecutive_failures, consecutive_limit_runs,
            gap_detected, last_error_code, last_error_message
        )
        SELECT id, 'degraded', '{}', '{}', false, 0, 0, false,
            'web_rule_required', 'A new native Crawl4AI rule must be authored for this Web source.'
        FROM feed_sources WHERE kind = 'web'
        ON CONFLICT (source_id) DO NOTHING
    """)
    op.execute("""
        UPDATE source_sync_states AS state
        SET phase = 'degraded', committed_checkpoint = '{}', pending_checkpoint = '{}',
            continuation = NULL, provider_mode = NULL, next_scan_at = NULL,
            lease_token = NULL, lease_expires_at = NULL,
            consecutive_failures = 0, consecutive_limit_runs = 0,
            gap_detected = false, gap_details = NULL,
            last_error_code = 'web_rule_required',
            last_error_message = 'A new native Crawl4AI rule must be authored for this Web source.',
            updated_at = now()
        FROM feed_sources AS source
        WHERE state.source_id = source.id AND source.kind = 'web'
    """)
    op.execute("""
        UPDATE source_sync_runs AS run
        SET status = 'partial', finished_at = now(), error_code = 'web_rule_required',
            error_message = 'Existing Web rules retired; re-author a native Crawl4AI rule.'
        FROM feed_sources AS source
        WHERE run.source_id = source.id AND source.kind = 'web' AND run.status = 'running'
    """)
    op.execute("""
        DELETE FROM ingestion_candidates AS candidate USING feed_sources AS source
        WHERE candidate.source_id = source.id AND source.kind = 'web'
            AND candidate.status <> 'completed'
    """)
    op.execute("""
        DELETE FROM web_frontier AS frontier USING feed_sources AS source
        WHERE frontier.source_id = source.id AND source.kind = 'web'
    """)


def downgrade() -> None:
    # Retired rule definitions and unprocessed payloads are intentionally not
    # recreated. Existing subscriptions/content stay intact in either direction.
    pass
