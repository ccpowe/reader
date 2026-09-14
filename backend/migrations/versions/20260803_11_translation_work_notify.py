"""Wake translation workers from the same statement that changes durable work.

Revision ID: 20260803_11
Revises: 20260803_10
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_11"
down_revision: str | Sequence[str] | None = "20260803_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.notify_reader_translation_work_v2()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = public
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                PERFORM pg_notify('reader_translation_work_v2', NEW.id::text);
            ELSIF NEW.status = 'pending' AND (
                OLD.status IS DISTINCT FROM NEW.status
                OR OLD.priority IS DISTINCT FROM NEW.priority
                OR OLD.available_at IS DISTINCT FROM NEW.available_at
            ) THEN
                PERFORM pg_notify('reader_translation_work_v2', NEW.id::text);
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_notify_reader_translation_work_v2
        AFTER INSERT OR UPDATE OF status, priority, available_at
        ON public.translation_work
        FOR EACH ROW
        EXECUTE FUNCTION public.notify_reader_translation_work_v2()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_notify_reader_translation_work_v2 ON public.translation_work"
    )
    op.execute("DROP FUNCTION IF EXISTS public.notify_reader_translation_work_v2()")
