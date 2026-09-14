"""Replace content-bound translation rows with demand-driven work and artifacts.

Revision ID: 20260803_10
Revises: 20260802_09
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_10"
down_revision: str | Sequence[str] | None = "20260802_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
_PURPOSE_CHECK = "purpose IN ('title', 'excerpt', 'body', 'paragraph', 'ranking_title')"
_SCOPE_OWNER_CHECK = (
    "(scope = 'shared' AND owner_id IS NULL) OR (scope = 'user' AND owner_id IS NOT NULL)"
)


def upgrade() -> None:
    # Translation rows are development-only cache/work data. Preferences and
    # source content deliberately survive this destructive runtime reset.
    op.drop_table("translation_jobs")
    op.drop_table("content_translations")

    op.create_table(
        "translation_artifacts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("owner_id", UUID),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_locale", sa.String(length=16), server_default="auto", nullable=False),
        sa.Column("target_locale", sa.String(length=16), nullable=False),
        sa.Column("engine_fingerprint", sa.String(length=256), nullable=False),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=160)),
        sa.Column("prompt_version", sa.String(length=64), server_default="v1", nullable=False),
        sa.Column("glossary_version", sa.String(length=64)),
        sa.Column("translated_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_PURPOSE_CHECK, name="ck_translation_artifacts_purpose"),
        sa.CheckConstraint("scope IN ('shared', 'user')", name="ck_translation_artifacts_scope"),
        sa.CheckConstraint(
            _SCOPE_OWNER_CHECK,
            name="ck_translation_artifacts_scope_owner",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_translation_artifacts_shared_identity",
        "translation_artifacts",
        [
            "purpose",
            "source_hash",
            "source_locale",
            "target_locale",
            "engine_fingerprint",
        ],
        unique=True,
        postgresql_where=sa.text("owner_id IS NULL"),
    )
    op.create_index(
        "uq_translation_artifacts_user_identity",
        "translation_artifacts",
        [
            "owner_id",
            "purpose",
            "source_hash",
            "source_locale",
            "target_locale",
            "engine_fingerprint",
        ],
        unique=True,
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )

    op.create_table(
        "translation_work",
        sa.Column("id", UUID, nullable=False),
        sa.Column("owner_id", UUID),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_locale", sa.String(length=16), server_default="auto", nullable=False),
        sa.Column("target_locale", sa.String(length=16), nullable=False),
        sa.Column("engine_fingerprint", sa.String(length=256), nullable=False),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=160)),
        sa.Column("prompt_version", sa.String(length=64), server_default="v1", nullable=False),
        sa.Column("glossary_version", sa.String(length=64)),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_token", UUID),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_finished_at", sa.DateTime(timezone=True)),
        sa.Column("provider_latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column("error_message", sa.Text()),
        sa.Column("error_retryable", sa.Boolean()),
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
        sa.CheckConstraint(_PURPOSE_CHECK, name="ck_translation_work_purpose"),
        sa.CheckConstraint("scope IN ('shared', 'user')", name="ck_translation_work_scope"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_translation_work_status",
        ),
        sa.CheckConstraint(_SCOPE_OWNER_CHECK, name="ck_translation_work_scope_owner"),
        sa.CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status != 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_translation_work_running_lease",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_translation_work_priority"),
        sa.CheckConstraint("length(source_text) > 0", name="ck_translation_work_source_text"),
        sa.ForeignKeyConstraint(["owner_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_translation_work_claim",
        "translation_work",
        ["status", "available_at", "priority", "created_at"],
    )
    op.create_index(
        "uq_translation_work_shared_identity",
        "translation_work",
        [
            "purpose",
            "source_hash",
            "source_locale",
            "target_locale",
            "engine_fingerprint",
        ],
        unique=True,
        postgresql_where=sa.text("owner_id IS NULL"),
    )
    op.create_index(
        "uq_translation_work_user_identity",
        "translation_work",
        [
            "owner_id",
            "purpose",
            "source_hash",
            "source_locale",
            "target_locale",
            "engine_fingerprint",
        ],
        unique=True,
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )

    for table in ("translation_artifacts", "translation_work"):
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    # The old cache/work payload is intentionally not recoverable. Recreate the
    # former empty schema so code at the previous revision can still start.
    op.drop_table("translation_work")
    op.drop_table("translation_artifacts")
    _create_legacy_tables()
    for table in ("content_translations", "translation_jobs"):
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def _create_legacy_tables() -> None:
    op.create_table(
        "content_translations",
        *_legacy_common_columns(),
        sa.Column("translated_text", sa.Text()),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column("error_message", sa.Text()),
        *_legacy_timestamps(),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_content_translations_lookup",
        "content_translations",
        ["content_id", "target_locale", "field"],
    )
    _create_legacy_identity_indexes("content_translations")

    op.create_table(
        "translation_jobs",
        *_legacy_common_columns(),
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
        *_legacy_timestamps(),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_translation_jobs_claim",
        "translation_jobs",
        ["status", "available_at", "created_at"],
    )
    _create_legacy_identity_indexes("translation_jobs")


def _legacy_common_columns() -> list[sa.Column]:
    return [
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
    ]


def _legacy_timestamps() -> list[sa.Column]:
    return [
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
    ]


def _create_legacy_identity_indexes(table: str) -> None:
    op.create_index(
        f"uq_{table}_shared_identity",
        table,
        ["content_id", "field", "target_locale", "engine_fingerprint"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NULL"),
    )
    op.create_index(
        f"uq_{table}_user_identity",
        table,
        ["owner_id", "content_id", "field", "target_locale", "engine_fingerprint"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )
