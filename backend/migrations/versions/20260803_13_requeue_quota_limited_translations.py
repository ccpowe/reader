"""requeue translations exhausted by provider quota

Revision ID: 20260803_13
Revises: 20260803_12
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_13"
down_revision: str | None = "20260803_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Quota failures are operationally recoverable. Earlier executor versions
    # could exhaust max_attempts and permanently poison an otherwise valid
    # Translation Demand, even after an operator raised the provider quota.
    op.execute(
        """
        UPDATE translation_work
        SET
            status = 'pending',
            attempt_count = 0,
            available_at = now(),
            lease_token = NULL,
            lease_expires_at = NULL,
            finished_at = NULL,
            error_retryable = TRUE,
            updated_at = now()
        WHERE status = 'failed'
          AND error_code IN (
              'google_dailyLimitExceeded',
              'google_quotaExceeded',
              'google_rateLimitExceeded',
              'google_userRateLimitExceeded'
          )
        """
    )


def downgrade() -> None:
    # Requeued work cannot be distinguished from newly requested work and is
    # intentionally left pending when rolling the schema back.
    pass
