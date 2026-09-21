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
    remote_browser = settings.browser_controller_url is not None
    snapshot = {
        **chat_model_snapshot(settings, descriptor),
        "model_protocol": rule_model_protocol(descriptor),
        "execution_options": {
            key.removeprefix("web_rule_agent_"): getattr(settings, key)
            for key in (
                "web_rule_agent_max_model_calls",
                "web_rule_agent_model_timeout_seconds",
                "web_rule_agent_max_output_tokens",
                "web_rule_agent_max_total_tokens",
                "web_rule_agent_max_tool_result_bytes",
                "web_rule_agent_max_diagnostic_bytes",
                "web_rule_agent_inspect_timeout_seconds",
                "web_rule_agent_validate_timeout_seconds",
                "web_rule_agent_lease_seconds",
                "web_rule_agent_heartbeat_seconds",
                "web_rule_agent_validation_ttl_seconds",
            )
        },
        "ingestion_browser_engine": settings.ingestion_browser_engine,
        "browser_runtime": "container-task-v1" if remote_browser else "local-bwrap-v1",
        "prompt_version": PROMPT_VERSION,
        "rule_schema_version": RULE_SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
    }
    if not remote_browser:
        snapshot["ingestion_lightpanda_executable_path"] = (
            settings.ingestion_lightpanda_executable_path
        )
        snapshot["execution_options"]["cli_executable_path"] = (
            settings.web_rule_agent_cli_executable_path
        )
    snapshot["config_fingerprint"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return snapshot
