"""add managed translation engine identities and preferences

Revision ID: 20260804_14
Revises: 20260803_13
Create Date: 2026-08-04
"""

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260804_14"
down_revision: str | None = "20260803_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _engine_fingerprint(
    *,
    engine_id: str,
    provider_name: str,
    model_name: str | None,
    prompt_version: str,
    glossary_version: str | None,
) -> str:
    payload = {
        "engine_id": engine_id,
        "glossary_version": glossary_version,
        "model": model_name,
        "prompt_version": prompt_version,
        "provider": provider_name,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _legacy_engine_fingerprint(
    *,
    provider_name: str,
    model_name: str | None,
    prompt_version: str,
    glossary_version: str | None,
) -> str:
    payload = {
        "glossary_version": glossary_version,
        "model": model_name,
        "prompt_version": prompt_version,
        "provider": provider_name,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _create_offline_ascii_json_encoder() -> None:
    """Render JSON strings byte-identically to Python ``ensure_ascii=True``."""
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION pg_temp.reader_json_ascii(value text)
        RETURNS text
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $reader_json_ascii$
        DECLARE
            encoded text := chr(34);
            current_char text;
            current_json text;
            codepoint integer;
            shifted integer;
            high_surrogate integer;
            low_surrogate integer;
        BEGIN
            FOR position IN 1..char_length(value) LOOP
                current_char := substr(value, position, 1);
                codepoint := ascii(current_char);
                IF codepoint < 127 THEN
                    current_json := to_json(current_char)::text;
                    encoded := encoded || substr(
                        current_json,
                        2,
                        char_length(current_json) - 2
                    );
                ELSIF codepoint <= 65535 THEN
                    encoded := encoded || chr(92) || 'u' ||
                        lpad(lower(to_hex(codepoint)), 4, '0');
                ELSE
                    shifted := codepoint - 65536;
                    high_surrogate := 55296 + (shifted >> 10);
                    low_surrogate := 56320 + (shifted & 1023);
                    encoded := encoded || chr(92) || 'u' ||
                        lpad(lower(to_hex(high_surrogate)), 4, '0') ||
                        chr(92) || 'u' ||
                        lpad(lower(to_hex(low_surrogate)), 4, '0');
                END IF;
            END LOOP;
            RETURN encoded || chr(34);
        END;
        $reader_json_ascii$
        """
    )


def _rekey_engine_fingerprints(table: str) -> None:
    if context.is_offline_mode():
        op.execute(
            "UPDATE "
            + table
            + """
            SET engine_fingerprint = encode(
                sha256(convert_to(
                    '{"engine_id":' || pg_temp.reader_json_ascii(engine_id) ||
                    ',"glossary_version":' ||
                        coalesce(pg_temp.reader_json_ascii(glossary_version), 'null') ||
                    ',"model":' || coalesce(pg_temp.reader_json_ascii(model_name), 'null') ||
                    ',"prompt_version":' || pg_temp.reader_json_ascii(prompt_version) ||
                    ',"provider":' || pg_temp.reader_json_ascii(provider_name) || '}',
                    'UTF8'
                )),
                'hex'
            )
            """
        )
        return
    connection = op.get_bind()
    configurations = list(
        connection.execute(
            sa.text(
                f"""
                SELECT DISTINCT
                    engine_id,
                    provider_name,
                    model_name,
                    prompt_version,
                    glossary_version
                FROM {table}
                """
            )
        ).mappings()
    )
    for configuration in configurations:
        fingerprint = _engine_fingerprint(
            engine_id=configuration["engine_id"],
            provider_name=configuration["provider_name"],
            model_name=configuration["model_name"],
            prompt_version=configuration["prompt_version"],
            glossary_version=configuration["glossary_version"],
        )
        connection.execute(
            sa.text(
                f"""
                UPDATE {table}
                SET engine_fingerprint = :fingerprint
                WHERE engine_id = :engine_id
                  AND provider_name = :provider_name
                  AND model_name IS NOT DISTINCT FROM :model_name
                  AND prompt_version = :prompt_version
                  AND glossary_version IS NOT DISTINCT FROM :glossary_version
                """
            ),
            {
                "engine_id": configuration["engine_id"],
                "fingerprint": fingerprint,
                "glossary_version": configuration["glossary_version"],
                "model_name": configuration["model_name"],
                "prompt_version": configuration["prompt_version"],
                "provider_name": configuration["provider_name"],
            },
        )


def _restore_legacy_engine_fingerprints(table: str) -> None:
    if context.is_offline_mode():
        op.execute(
            "UPDATE "
            + table
            + """
            SET engine_fingerprint = encode(
                sha256(convert_to(
                    '{"glossary_version":' ||
                    coalesce(pg_temp.reader_json_ascii(glossary_version), 'null') ||
                    ',"model":' || coalesce(pg_temp.reader_json_ascii(model_name), 'null') ||
                    ',"prompt_version":' || pg_temp.reader_json_ascii(prompt_version) ||
                    ',"provider":' || pg_temp.reader_json_ascii(provider_name) || '}',
                    'UTF8'
                )),
                'hex'
            )
            """
        )
        return
    connection = op.get_bind()
    configurations = list(
        connection.execute(
            sa.text(
                f"""
                SELECT DISTINCT
                    provider_name,
                    model_name,
                    prompt_version,
                    glossary_version
                FROM {table}
                """
            )
        ).mappings()
    )
    for configuration in configurations:
        fingerprint = _legacy_engine_fingerprint(
            provider_name=configuration["provider_name"],
            model_name=configuration["model_name"],
            prompt_version=configuration["prompt_version"],
            glossary_version=configuration["glossary_version"],
        )
        connection.execute(
            sa.text(
                f"""
                UPDATE {table}
                SET engine_fingerprint = :fingerprint
                WHERE provider_name = :provider_name
                  AND model_name IS NOT DISTINCT FROM :model_name
                  AND prompt_version = :prompt_version
                  AND glossary_version IS NOT DISTINCT FROM :glossary_version
                """
            ),
            {
                "fingerprint": fingerprint,
                "glossary_version": configuration["glossary_version"],
                "model_name": configuration["model_name"],
                "prompt_version": configuration["prompt_version"],
                "provider_name": configuration["provider_name"],
            },
        )


def upgrade() -> None:
    if context.is_offline_mode():
        _create_offline_ascii_json_encoder()
    op.add_column(
        "user_translation_preferences",
        sa.Column("engine_id", sa.String(length=64), nullable=True),
    )
    op.drop_constraint(
        "ck_user_translation_preferences_provider_mode",
        "user_translation_preferences",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_translation_preferences_provider_mode",
        "user_translation_preferences",
        "provider_mode IN ('app_default', 'app_managed', 'byok')",
    )

    for table in ("translation_artifacts", "translation_work"):
        op.add_column(table, sa.Column("engine_id", sa.String(length=64), nullable=True))
        op.execute(
            f"""
            UPDATE {table}
            SET engine_id = CASE
                WHEN provider_name = 'google_nmt' THEN 'google-nmt'
                ELSE left(provider_name || ':' || coalesce(model_name, 'default'), 64)
            END
            WHERE engine_id IS NULL
            """
        )
        op.alter_column(table, "engine_id", existing_type=sa.String(length=64), nullable=False)
        _rekey_engine_fingerprints(table)
        op.create_check_constraint(
            f"ck_{table}_engine_id",
            table,
            "length(engine_id) > 0",
        )

    op.drop_constraint("ck_translation_work_status", "translation_work", type_="check")
    op.create_check_constraint(
        "ck_translation_work_status",
        "translation_work",
        "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
    )
    op.drop_index("ix_translation_work_claim", table_name="translation_work")
    op.create_index(
        "ix_translation_work_claim",
        "translation_work",
        ["engine_id", "status", "available_at", "priority", "created_at"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        _create_offline_ascii_json_encoder()
    op.execute("DELETE FROM translation_work WHERE status = 'cancelled'")
    op.drop_index("ix_translation_work_claim", table_name="translation_work")
    op.create_index(
        "ix_translation_work_claim",
        "translation_work",
        ["status", "available_at", "priority", "created_at"],
    )
    op.drop_constraint("ck_translation_work_status", "translation_work", type_="check")
    op.create_check_constraint(
        "ck_translation_work_status",
        "translation_work",
        "status IN ('pending', 'running', 'succeeded', 'failed')",
    )

    for table in ("translation_work", "translation_artifacts"):
        _restore_legacy_engine_fingerprints(table)
        op.drop_constraint(f"ck_{table}_engine_id", table, type_="check")
        op.drop_column(table, "engine_id")

    op.drop_constraint(
        "ck_user_translation_preferences_provider_mode",
        "user_translation_preferences",
        type_="check",
    )
    op.execute(
        "UPDATE user_translation_preferences SET provider_mode = 'app_default' "
        "WHERE provider_mode = 'app_managed'"
    )
    op.create_check_constraint(
        "ck_user_translation_preferences_provider_mode",
        "user_translation_preferences",
        "provider_mode IN ('app_default', 'byok')",
    )
    op.drop_column("user_translation_preferences", "engine_id")
