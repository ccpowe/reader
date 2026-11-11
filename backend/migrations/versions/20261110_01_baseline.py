"""Reader baseline schema.

Replaces the earlier migration history; databases created before this revision
must be recreated. The DDL lives in the neighbouring ``.sql`` file.

Revision ID: 20261110_01
Revises:
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "20261110_01"
down_revision = None
branch_labels = None
depends_on = None

_SCHEMA = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    # asyncpg prepares single statements; its simple-query path runs the dump.
    driver = op.get_bind().connection.driver_connection
    await_only(driver.execute(_SCHEMA.read_text(encoding="utf-8")))
    op.execute("INSERT INTO app_schema_contracts (contract) VALUES ('reader-runtime-v27')")


def downgrade() -> None:
    raise NotImplementedError("The baseline schema cannot be downgraded.")
