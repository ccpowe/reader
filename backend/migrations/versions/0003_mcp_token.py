"""A personal token per user for the /mcp endpoint (stored as a SHA-256 hash).

Revision ID: 0003_mcp_token
Revises: 0002_saved_article_index
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_mcp_token"
down_revision = "0002_saved_article_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mcp_token_hash", sa.String(64), nullable=True))
    op.create_unique_constraint("users_mcp_token_hash_key", "users", ["mcp_token_hash"])


def downgrade() -> None:
    op.drop_constraint("users_mcp_token_hash_key", "users", type_="unique")
    op.drop_column("users", "mcp_token_hash")
