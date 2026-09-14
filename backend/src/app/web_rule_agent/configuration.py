"""Lightweight execution identity shared by the API projection and rule worker."""

from __future__ import annotations

import hashlib
import json

from app.core.settings import Settings
from app.llm.factory import chat_model_snapshot
from app.translation.engines import EngineDescriptor

from . import PROMPT_VERSION, RULE_SCHEMA_VERSION, VALIDATOR_VERSION
from .model import rule_model_protocol


def rule_agent_snapshot(settings: Settings, descriptor: EngineDescriptor) -> dict:
    snapshot = {
        **chat_model_snapshot(settings, descriptor),
        "model_protocol": rule_model_protocol(descriptor),
        "execution_options": {
            key.removeprefix("web_rule_agent_"): value
            for key, value in settings.model_dump().items()
            if key.startswith("web_rule_agent_")
            and key not in {"web_rule_agent_engine_id", "web_rule_agent_poll_seconds"}
        },
        "ingestion_browser_engine": settings.ingestion_browser_engine,
        "ingestion_lightpanda_executable_path": settings.ingestion_lightpanda_executable_path,
        "prompt_version": PROMPT_VERSION,
        "rule_schema_version": RULE_SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
    }
    snapshot["config_fingerprint"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return snapshot
