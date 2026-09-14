"""retire the Google translation engine

Revision ID: 20260828_19
Revises: 20260815_18
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260828_19"
down_revision: str | None = "20260815_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Users who explicitly selected the retired engine should inherit the
    # configured AI default after upgrading, without retaining stale routing
    # metadata in API responses.
    op.execute(
        """
        UPDATE user_translation_preferences
        SET
            provider_mode = 'app_default',
            engine_id = NULL,
            provider_name = NULL,
            model_name = NULL,
            updated_at = now()
        WHERE engine_id = 'google-nmt'
           OR provider_name = 'google_nmt'
        """
    )

    # The new worker deliberately has no route for this engine. Mark unfinished
    # rows terminal so operational tooling does not report work that can never
    # be claimed. Successful artifacts remain immutable historical cache rows.
    op.execute(
        """
        UPDATE translation_work
        SET
            status = 'cancelled',
            lease_token = NULL,
            lease_expires_at = NULL,
            finished_at = now(),
            last_attempt_finished_at = now(),
            error_code = 'translation_engine_retired',
            error_message = 'Translation engine retired by migration 20260828_19.',
            error_retryable = FALSE,
            updated_at = now()
        WHERE (engine_id = 'google-nmt' OR provider_name = 'google_nmt')
          AND status IN ('pending', 'running')
        """
    )


def downgrade() -> None:
    # The removed provider and its credentials cannot be restored by a data
    # migration. Historical artifacts remain available for an older release.
    pass
