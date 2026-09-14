"""Add translation preferences, cache rows, and idempotent jobs.

Revision ID: 20260802_07
Revises: 20260802_06
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_07"
down_revision: str | Sequence[str] | None = "20260802_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "user_translation_preferences",
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("target_locale", sa.String(length=16), server_default="zh-CN", nullable=False),
        sa.Column(
            "provider_mode",
            sa.String(length=16),
            server_default="app_default",
            nullable=False,
        ),
        sa.Column("provider_name", sa.String(length=64)),
        sa.Column("model_name", sa.String(length=160)),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider_mode IN ('app_default', 'byok')",
            name="ck_user_translation_preferences_provider_mode",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_user_translation_preferences_target_locale",
        "user_translation_preferences",
        ["target_locale"],
    )

    op.create_table(
        "content_translations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("content_id", UUID, nullable=False),
        sa.Column("owner_id", UUID),
        sa.Column("field", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("target_locale", sa.String(length=16), nullable=False),
        sa.Column("engine_fingerprint", sa.String(length=256), nullable=False),
        sa.Column("provider_name", sa.String(length=64)),
        sa.Column("model_name", sa.String(length=160)),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("translated_text", sa.Text()),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "field IN ('title', 'excerpt', 'body')", name="ck_content_translations_field"
        ),
        sa.CheckConstraint("scope IN ('shared', 'user')", name="ck_content_translations_scope"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_content_translations_status",
        ),
        sa.CheckConstraint(
            "(scope = 'shared' AND owner_id IS NULL) OR (scope = 'user' AND owner_id IS NOT NULL)",
            name="ck_content_translations_scope_owner",
        ),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_content_translations_lookup",
        "content_translations",
        ["content_id", "target_locale", "field"],
    )
    op.create_index(
        "uq_content_translations_shared_identity",
        "content_translations",
        ["content_id", "field", "target_locale", "engine_fingerprint"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NULL"),
    )
    op.create_index(
        "uq_content_translations_user_identity",
        "content_translations",
        ["owner_id", "content_id", "field", "target_locale", "engine_fingerprint"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )

    op.create_table(
        "translation_jobs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("content_id", UUID, nullable=False),
        sa.Column("owner_id", UUID),
        sa.Column("field", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("target_locale", sa.String(length=16), nullable=False),
        sa.Column("engine_fingerprint", sa.String(length=256), nullable=False),
        sa.Column("provider_name", sa.String(length=64)),
        sa.Column("model_name", sa.String(length=160)),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "field IN ('title', 'excerpt', 'body')", name="ck_translation_jobs_field"
        ),
        sa.CheckConstraint("scope IN ('shared', 'user')", name="ck_translation_jobs_scope"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_translation_jobs_status",
        ),
        sa.CheckConstraint(
            "(scope = 'shared' AND owner_id IS NULL) OR (scope = 'user' AND owner_id IS NOT NULL)",
            name="ck_translation_jobs_scope_owner",
        ),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_translation_jobs_claim",
        "translation_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "uq_translation_jobs_shared_identity",
        "translation_jobs",
        ["content_id", "field", "target_locale", "engine_fingerprint"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NULL"),
    )
    op.create_index(
        "uq_translation_jobs_user_identity",
        "translation_jobs",
        ["owner_id", "content_id", "field", "target_locale", "engine_fingerprint"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )



def downgrade() -> None:
    op.drop_index("uq_translation_jobs_user_identity", table_name="translation_jobs")
    op.drop_index("uq_translation_jobs_shared_identity", table_name="translation_jobs")
    op.drop_index("ix_translation_jobs_claim", table_name="translation_jobs")
    op.drop_table("translation_jobs")
    op.drop_index("uq_content_translations_user_identity", table_name="content_translations")
    op.drop_index("uq_content_translations_shared_identity", table_name="content_translations")
    op.drop_index("ix_content_translations_lookup", table_name="content_translations")
    op.drop_table("content_translations")
    op.drop_index(
        "ix_user_translation_preferences_target_locale",
        table_name="user_translation_preferences",
    )
    op.drop_table("user_translation_preferences")
