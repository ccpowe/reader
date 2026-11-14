"""Reader baseline schema (14 tables).

Databases created before this revision must be recreated. The DDL lives in the
neighbouring ``.sql`` file.

Revision ID: 0001_baseline
Revises:
"""

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_SCHEMA = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    # asyncpg prepares single statements; its simple-query path runs the whole file.
    driver = op.get_bind().connection.driver_connection
    await_only(driver.execute(_SCHEMA.read_text(encoding="utf-8")))


def downgrade() -> None:
    raise NotImplementedError("The baseline schema cannot be downgraded.")
