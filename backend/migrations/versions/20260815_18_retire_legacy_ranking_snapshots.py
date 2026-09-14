"""retire legacy ranking snapshot keys

Revision ID: 20260815_18
Revises: 20260811_17
Create Date: 2026-08-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260815_18"
down_revision: str | None = "20260811_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # These rows are derived caches from the pre-v2 key space. Keeping them due
    # makes a v2 worker refresh a different row forever, once per polling cycle.
    op.execute(
        """
        DELETE FROM ranking_snapshots
        WHERE cache_key IN ('ranking:hacker_news', 'ranking:github')
           OR cache_key LIKE 'ranking:reddit:%'
        """
    )


def downgrade() -> None:
    # Deleted cache rows are derived data and must not be reconstructed as stale
    # pre-v2 keys; current snapshots are recreated by the ranking worker.
    pass
