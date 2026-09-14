"""add translation purposes for ranking, web, and captions

Revision ID: 20260803_12
Revises: 20260803_11
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_12"
down_revision: str | None = "20260803_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_V2_PURPOSES = "'title', 'excerpt', 'body', 'paragraph', 'ranking_title'"
_SURFACE_PURPOSES = _V2_PURPOSES + ", 'ranking_description', 'web_segment', 'caption'"


def upgrade() -> None:
    _replace_purpose_constraint(
        "translation_artifacts",
        "ck_translation_artifacts_purpose",
        _SURFACE_PURPOSES,
    )
    _replace_purpose_constraint(
        "translation_work",
        "ck_translation_work_purpose",
        _SURFACE_PURPOSES,
    )


def downgrade() -> None:
    # Development-only downgrade intentionally rejects rows using the newer
    # purposes instead of silently changing their translation identity.
    _replace_purpose_constraint(
        "translation_artifacts",
        "ck_translation_artifacts_purpose",
        _V2_PURPOSES,
    )
    _replace_purpose_constraint(
        "translation_work",
        "ck_translation_work_purpose",
        _V2_PURPOSES,
    )


def _replace_purpose_constraint(table: str, name: str, purposes: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"purpose IN ({purposes})")
