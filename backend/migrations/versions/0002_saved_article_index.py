"""Index saved articles by article, for retention's "saved by anyone" check.

Revision ID: 0002_saved_article_index
Revises: 0001_baseline
"""

from alembic import op

revision = "0002_saved_article_index"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_saved_articles_article", "saved_articles", ["article_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_articles_article", table_name="saved_articles")
