"""Replace the managed OpenRouter GLM engine with MiniMax M3.

Revision ID: 20260908_24
Revises: 20260902_23
"""

from alembic import op

revision = "20260908_24"
down_revision = "20260902_23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE user_translation_preferences
        SET engine_id = 'openrouter-minimax-m3', provider_name = 'openrouter',
            model_name = 'minimax/minimax-m3', updated_at = now()
        WHERE engine_id = 'openrouter-glm-5.3-flash'
    """)
    # Stop API/worker before migration. Never execute old fingerprints using
    # the new model; demand will create new work under the new engine identity.
    op.execute("""
        UPDATE translation_work
        SET status = 'cancelled', lease_token = NULL, lease_expires_at = NULL,
            finished_at = now(), last_attempt_finished_at = now(),
            error_code = 'translation_engine_retired',
            error_message = 'OpenRouter GLM engine replaced by MiniMax M3.',
            error_retryable = FALSE, updated_at = now()
        WHERE engine_id = 'openrouter-glm-5.3-flash'
          AND status IN ('pending', 'running')
    """)


def downgrade() -> None:
    # The older release has only GLM. Keep successful cache identities intact;
    # cancelled work can be recreated by that release's demand-driven runtime.
    op.execute("""
        UPDATE user_translation_preferences
        SET engine_id = 'openrouter-glm-5.3-flash', provider_name = 'openrouter',
            model_name = 'z-ai/glm-5.3-flash', updated_at = now()
        WHERE engine_id = 'openrouter-minimax-m3'
    """)
    op.execute("""
        UPDATE translation_work
        SET status = 'cancelled', lease_token = NULL, lease_expires_at = NULL,
            finished_at = now(), last_attempt_finished_at = now(),
            error_code = 'translation_engine_retired', error_retryable = FALSE,
            updated_at = now()
        WHERE engine_id = 'openrouter-minimax-m3'
          AND status IN ('pending', 'running')
    """)
