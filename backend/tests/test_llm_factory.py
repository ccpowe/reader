import json

from app.core.settings import Settings
from app.llm.factory import chat_model_snapshot
from app.translation.engines import engine_descriptor


def test_rule_model_snapshot_ignores_personal_default_and_secrets_but_tracks_actual_model():
    configured = Settings(_env_file=None, deepseek_api_key="secret-one")
    descriptor = engine_descriptor(configured, "deepseek-v4-flash")
    original = chat_model_snapshot(configured, descriptor)
    changed_preference = configured.model_copy(
        update={
            "translation_default_engine_id": "openrouter-minimax-m3",
        }
    )
    assert chat_model_snapshot(changed_preference, descriptor) == original
    assert "secret-one" not in json.dumps(original)
    changed_model = configured.model_copy(update={"deepseek_model": "another-tool-model"})
    assert (
        chat_model_snapshot(changed_model, engine_descriptor(changed_model, "deepseek-v4-flash"))[
            "config_fingerprint"
        ]
        != original["config_fingerprint"]
    )






