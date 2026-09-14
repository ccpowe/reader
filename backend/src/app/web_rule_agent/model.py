"""Tool-capable rule model routing, separate from translation output protocols."""

from typing import Any

from app.core.settings import Settings
from app.llm.factory import build_chat_model
from app.translation.engines import EngineDescriptor


def rule_model_protocol(descriptor: EngineDescriptor) -> dict:
    protocol = {
        "structured_output": "tool_strategy",
        "final_schema": "FinalWebRule",
        "failure_schema": "RuleAuthoringFailure",
    }
    if descriptor.provider_name == "openrouter":
        protocol.update(require_parameters=True, output_token_parameter="max_tokens")
    return protocol


def build_rule_chat_model(
    settings: Settings,
    descriptor: EngineDescriptor,
    *,
    timeout_seconds: float | None = None,
    max_output_tokens: int | None = None,
) -> Any:
    model = build_chat_model(
        settings,
        descriptor,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )
    if descriptor.provider_name == "openrouter":
        # ChatOpenAI renames its max_tokens field to max_completion_tokens.
        # Strict OpenRouter routing requires the widely supported max_tokens
        # wire parameter; extra_body preserves that name in the actual request.
        model.max_tokens = None
        model.extra_body = {
            **(model.extra_body or {}),
            "provider": {"require_parameters": True},
            **({"max_tokens": max_output_tokens} if max_output_tokens is not None else {}),
        }
    return model
