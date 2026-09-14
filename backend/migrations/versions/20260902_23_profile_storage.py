"""Install the profile schema contract.

Revision ID: 20260902_23
Revises: 20260902_22
Create Date: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260902_23"
down_revision: str | Sequence[str] | None = "20260902_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Supabase owns the storage schema. Deployments create the public avatars
    # bucket with the Storage API or dashboard, never by mutating its tables.
    op.execute(
        """
        INSERT INTO app_schema_contracts (contract)
        VALUES ('reader-runtime-v21')
        ON CONFLICT (contract) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_schema_contracts WHERE contract = 'reader-runtime-v21'")
