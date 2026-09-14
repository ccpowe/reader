"""bound exhausted provider quota retries

Revision ID: 20260805_15
Revises: 20260804_14
Create Date: 2026-08-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260805_15"
down_revision: str | None = "20260804_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Older executors intentionally retried Google quota failures forever.
    # Cancel only already-exhausted, non-running work. A later explicit demand
    # can reactivate a cancelled identity with a fresh attempt budget.
    op.execute(
        """
        UPDATE translation_work
        SET
            status = 'cancelled',
            lease_token = NULL,
            lease_expires_at = NULL,
            finished_at = now(),
            last_attempt_finished_at = now(),
            error_retryable = FALSE,
            error_message = concat(
                coalesce(error_message, 'Provider quota retry budget exhausted.'),
                ' Automatic retries stopped by bounded retry migration.'
            ),
            updated_at = now()
        WHERE status = 'pending'
          AND attempt_count >= 8
          AND error_code IN (
              'google_dailyLimitExceeded',
              'google_quotaExceeded',
              'google_rateLimitExceeded',
              'google_userRateLimitExceeded'
          )
        """
    )


def downgrade() -> None:
    # Cancelled work is indistinguishable from demand cancelled for another
    # reason and is deliberately not requeued during rollback.
    pass
