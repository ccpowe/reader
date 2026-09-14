"""Replace hosted auth/storage with Reader-owned PostgreSQL tables.

Revision ID: 20260912_28
Revises: 20260910_27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260912_28"
down_revision = "20260910_27"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # Preserve every profile UUID and dependent product row. The legacy schema
    # is optional: a fresh PostgreSQL installation never needs auth or storage.
    op.execute("""
        DO $$ DECLARE constraint_name text; BEGIN
          FOR constraint_name IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'public.profiles'::regclass
              AND contype = 'f'
              AND confrelid = to_regclass('auth.users')
          LOOP
            EXECUTE format('ALTER TABLE public.profiles DROP CONSTRAINT %I', constraint_name);
          END LOOP;
          IF to_regclass('auth.users') IS NOT NULL THEN
            DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
          END IF;
          DROP FUNCTION IF EXISTS public.handle_new_user();
        END $$;
    """)
    op.create_table(
        "reader_users",
        sa.Column("id", UUID, sa.ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "reader_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("reader_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_reader_sessions_user_id", "reader_sessions", ["user_id"])
    op.create_table(
        "reader_refresh_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("session_id", UUID, sa.ForeignKey("reader_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_reader_refresh_tokens_session_id", "reader_refresh_tokens", ["session_id"])
    op.create_table(
        "profile_avatars",
        sa.Column("user_id", UUID, sa.ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", UUID, nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("octet_length(data) <= 5242880", name="ck_profile_avatars_size"),
    )
    # JSON projection tolerates legacy auth fixtures/older deployments that do
    # not contain all modern GoTrue columns. Duplicate normalized emails abort
    # the migration transaction, allowing the operator to resolve them safely.
    op.execute("""
        DO $$ BEGIN
          IF to_regclass('auth.users') IS NOT NULL THEN
            INSERT INTO public.reader_users (id, email, password_hash)
            SELECT u.id, lower(trim(to_jsonb(u)->>'email')), to_jsonb(u)->>'encrypted_password'
            FROM auth.users u JOIN public.profiles p ON p.id = u.id
            WHERE trim(coalesce(to_jsonb(u)->>'email', '')) <> ''
              AND (to_jsonb(u)->>'encrypted_password' LIKE '$2%'
                   OR to_jsonb(u)->>'encrypted_password' LIKE '$argon2%')
              AND to_jsonb(u)->>'deleted_at' IS NULL
              AND (to_jsonb(u)->>'banned_until' IS NULL
                   OR (to_jsonb(u)->>'banned_until')::timestamptz <= now());
          END IF;
        END $$;
    """)
    # RLS protects old hosted API roles, while Reader operates using the table
    # owner over a private PostgreSQL connection. New credential tables have no
    # public/anon grants or policies.
    for table in ("reader_users", "reader_sessions", "reader_refresh_tokens", "profile_avatars"):
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute("INSERT INTO app_schema_contracts (contract) VALUES ('reader-runtime-v24') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Never silently discard new accounts, password changes or avatar bytes.
    raise RuntimeError("Reader auth migration requires an explicit data-preserving restore; downgrade is disabled.")
