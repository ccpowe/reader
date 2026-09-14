"""Exercise the data migration against isolated rows without production access."""

import importlib.util
import sqlite3
from pathlib import Path
from types import SimpleNamespace


def test_model_replacement_preserves_finished_work_and_other_engines(monkeypatch):
    path = Path(__file__).parents[1] / "migrations/versions/20260908_24_openrouter_minimax.py"
    spec = importlib.util.spec_from_file_location("model_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    db = sqlite3.connect(":memory:")
    db.create_function("now", 0, lambda: "2026-09-08")
    db.execute(
        "CREATE TABLE user_translation_preferences "
        "(engine_id, provider_name, model_name, updated_at)"
    )
    db.execute(
        "CREATE TABLE translation_work (engine_id, status, lease_token, lease_expires_at, "
        "finished_at, last_attempt_finished_at, error_code, error_message, "
        "error_retryable, updated_at)"
    )
    old, new, other = "openrouter-glm-5.3-flash", "openrouter-minimax-m3", "deepseek-v4-flash"
    for engine in [old, other]:
        db.execute(
            "INSERT INTO user_translation_preferences VALUES (?, ?, ?, NULL)",
            (engine, "original", "original"),
        )
        for status in ["pending", "running", "succeeded", "failed", "cancelled"]:
            db.execute(
                "INSERT INTO translation_work (engine_id,status,lease_token) VALUES (?,?,?)",
                (engine, status, "lease"),
            )
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=db.execute))
    migration.upgrade()
    assert db.execute(
        "SELECT engine_id,model_name FROM user_translation_preferences WHERE engine_id=?", (new,)
    ).fetchone() == (new, "minimax/minimax-m3")
    assert (
        db.execute(
            'SELECT count(*) FROM translation_work WHERE engine_id=? '
            'AND status="cancelled" AND lease_token IS NULL',
            (old,),
        ).fetchone()[0]
        == 2
    )
    assert (
        db.execute(
            'SELECT count(*) FROM translation_work WHERE engine_id=? AND status="succeeded"', (old,)
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            'SELECT count(*) FROM translation_work WHERE engine_id=? '
            'AND status IN ("pending","running")',
            (other,),
        ).fetchone()[0]
        == 2
    )
    migration.downgrade()
    assert (
        db.execute(
            "SELECT model_name FROM user_translation_preferences WHERE engine_id=?", (old,)
        ).fetchone()[0]
        == "z-ai/glm-5.3-flash"
    )
    db.close()
