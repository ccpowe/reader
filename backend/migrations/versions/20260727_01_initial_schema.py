"""Create the information aggregation schema.

Revision ID: 20260727_01
Revises:
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260727_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", UUID, nullable=False),
        sa.Column("display_name", sa.String(length=120)),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "feed_sources",
        sa.Column("id", UUID, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("canonical_key", sa.String(length=1024), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(length=300)),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("owner_id", UUID),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("etag", sa.String(length=512)),
        sa.Column("last_modified", sa.String(length=512)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("next_sync_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("visibility IN ('shared', 'private')", name="ck_feed_sources_visibility"),
        sa.CheckConstraint("status IN ('pending', 'active', 'paused', 'failed')", name="ck_feed_sources_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_key", name="uq_feed_sources_canonical_key"),
    )
    op.create_index("ix_feed_sources_next_sync_at", "feed_sources", ["next_sync_at"])
    op.create_table(
        "contents",
        sa.Column("id", UUID, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("url_hash", sa.String(length=64)),
        sa.Column("title", sa.String(length=1000), nullable=False),
        sa.Column("author_name", sa.String(length=300)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("excerpt", sa.Text()),
        sa.Column("body_text", sa.Text()),
        sa.Column("body_html", sa.Text()),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("extraction_status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('article', 'post', 'video', 'podcast')", name="ck_contents_kind"),
        sa.CheckConstraint(
            "extraction_status IN ('not_needed', 'pending', 'success', 'failed')",
            name="ck_contents_extraction_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contents_published_at", "contents", ["published_at"])
    op.create_index("ix_contents_canonical_url", "contents", ["canonical_url"])
    op.create_index("ix_contents_content_hash", "contents", ["content_hash"])
    op.create_table(
        "source_subscriptions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("custom_name", sa.String(length=300)),
        sa.Column("folder_name", sa.String(length=120)),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["feed_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "source_id", name="uq_source_subscriptions_user_source"),
    )
    op.create_index("ix_source_subscriptions_source_id", "source_subscriptions", ["source_id"])
    op.create_table(
        "source_sync_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("items_found", sa.Integer(), nullable=False),
        sa.Column("items_created", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint("status IN ('running', 'success', 'partial', 'failed')", name="ck_source_sync_runs_status"),
        sa.ForeignKeyConstraint(["source_id"], ["feed_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_source_sync_runs_source_started", "source_sync_runs", ["source_id", "started_at"])
    op.create_table(
        "source_entries",
        sa.Column("id", UUID, nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("content_id", UUID, nullable=False),
        sa.Column("native_id", sa.String(length=1024), nullable=False),
        sa.Column("external_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=False),
        sa.Column("author_name", sa.String(length=300)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("raw_metadata", JSONB, nullable=False),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["feed_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "native_id", name="uq_source_entries_source_native"),
    )
    op.create_index("ix_source_entries_content_id", "source_entries", ["content_id"])
    op.create_index("ix_source_entries_source_published", "source_entries", ["source_id", "published_at"])
    op.create_table(
        "content_media",
        sa.Column("id", UUID, nullable=False),
        sa.Column("content_id", UUID, nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("cached_path", sa.Text()),
        sa.Column("cache_status", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=200)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("poster_url", sa.Text()),
        sa.Column("sha256", sa.String(length=64)),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("media_type IN ('image', 'audio', 'video', 'embed')", name="ck_content_media_type"),
        sa.CheckConstraint(
            "cache_status IN ('external', 'queued', 'cached', 'failed')",
            name="ck_content_media_cache_status",
        ),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_id", "original_url", name="uq_content_media_content_url"),
    )
    op.create_table(
        "user_content_states",
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("content_id", UUID, nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("offline_requested", sa.Boolean(), nullable=False),
        sa.Column("last_opened_at", sa.DateTime(timezone=True)),
        sa.Column("progress", sa.Integer()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "content_id"),
    )
    op.create_table(
        "user_saved_contents",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("content_id", UUID, nullable=False),
        sa.Column("collection_name", sa.String(length=120)),
        sa.Column("note", sa.Text()),
        sa.Column("saved_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_id", name="uq_user_saved_contents_user_content"),
    )
    op.create_index("ix_user_saved_contents_user_saved", "user_saved_contents", ["user_id", "saved_at"])


def downgrade() -> None:
    op.drop_index("ix_user_saved_contents_user_saved", table_name="user_saved_contents")
    op.drop_table("user_saved_contents")
    op.drop_table("user_content_states")
    op.drop_table("content_media")
    op.drop_index("ix_source_entries_source_published", table_name="source_entries")
    op.drop_index("ix_source_entries_content_id", table_name="source_entries")
    op.drop_table("source_entries")
    op.drop_index("ix_source_sync_runs_source_started", table_name="source_sync_runs")
    op.drop_table("source_sync_runs")
    op.drop_index("ix_source_subscriptions_source_id", table_name="source_subscriptions")
    op.drop_table("source_subscriptions")
    op.drop_index("ix_contents_content_hash", table_name="contents")
    op.drop_index("ix_contents_canonical_url", table_name="contents")
    op.drop_index("ix_contents_published_at", table_name="contents")
    op.drop_table("contents")
    op.drop_index("ix_feed_sources_next_sync_at", table_name="feed_sources")
    op.drop_table("feed_sources")
    op.drop_table("profiles")
