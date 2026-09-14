import json

import httpx
import pytest

from app.core.settings import Settings
from app.llm.factory import build_chat_model, chat_model_snapshot, close_chat_model
from app.services.web_rule_jobs import JobLimits
from app.translation.engines import engine_descriptor
from app.web_rule_agent.configuration import rule_agent_snapshot
from app.web_rule_agent.model import build_rule_chat_model
from app.workers.web_rules import job_limits


@pytest.mark.parametrize("engine_id", ["deepseek-v4-flash", "openrouter-minimax-m3"])
@pytest.mark.parametrize("rule_agent", [False, True])
async def test_agent_model_uses_shared_provider_without_translation_protocol(
    monkeypatch, engine_id, rule_agent
):
    settings = Settings(_env_file=None, deepseek_api_key="test-key", openrouter_api_key="test-key")
    descriptor = engine_descriptor(settings, engine_id)
    builder = build_rule_chat_model if rule_agent else build_chat_model
    model = builder(settings, descriptor, timeout_seconds=60, max_output_tokens=4096)
    requests = []

    async def send(_client, request, **_kwargs):
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == descriptor.model_name
        assert "response_format" not in body
        assert body.get("max_tokens", body.get("max_completion_tokens")) == 4096
        assert body["tools"][0]["function"]["name"] == "inspect_web_page"
        if descriptor.provider_name == "deepseek":
            assert body["model"] == "deepseek-flash"
            assert body["thinking"] == {"type": "disabled"}
        elif rule_agent:
            assert body["max_tokens"] == 4096
            assert "max_completion_tokens" not in body
            assert body["provider"] == {"require_parameters": True}
            assert body["reasoning"] == {"exclude": True}
        else:
            assert "provider" not in body
        assert body["tool_choice"] == "required"
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "model-test",
                "object": "chat.completion",
                "created": 1,
                "model": descriptor.model_name,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "inspect-1",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_web_page",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 42, "completion_tokens": 8, "total_tokens": 50},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    try:
        reply = await model.bind_tools(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "inspect_web_page",
                        "description": "Inspect the bound source.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            tool_choice="required",
        ).ainvoke("Inspect this source.")
        assert len(requests) == 1
        assert reply.tool_calls[0]["name"] == "inspect_web_page"
        assert reply.usage_metadata["total_tokens"] == 50
        assert model.request_timeout == 60
        assert model.max_retries == 0
    finally:
        await close_chat_model(model)


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


def test_rule_token_budget_is_configurable_and_rejects_unsafe_heartbeat():
    configured = Settings(_env_file=None, web_rule_agent_max_total_tokens=4_000_000)
    assert configured.web_rule_agent_max_total_tokens == 4_000_000
    assert configured.web_rule_agent_engine_id == "disabled"
    with pytest.raises(ValueError, match="heartbeat"):
        Settings(_env_file=None, web_rule_agent_heartbeat_seconds=60)


def test_rule_job_defaults_match_worker_settings():
    settings = Settings(_env_file=None)
    assert job_limits(settings) == JobLimits()
    assert job_limits(settings).total_tokens == 3_000_000
    assert job_limits(settings).model_calls == 80
    configured = settings.model_copy(update={"web_rule_agent_max_total_tokens": 4_000_000})
    assert job_limits(configured).total_tokens == 4_000_000
    configured = settings.model_copy(update={"web_rule_agent_max_model_calls": 60})
    assert job_limits(configured).model_calls == 60


def test_token_policy_change_is_in_execution_fingerprint_and_protocol_stays_frozen():
    settings = Settings(_env_file=None)
    descriptor = engine_descriptor(settings, "openrouter-minimax-m3")
    snapshot = rule_agent_snapshot(settings, descriptor)
    assert snapshot["model_protocol"] == {
        "structured_output": "tool_strategy",
        "final_schema": "FinalWebRule",
        "failure_schema": "RuleAuthoringFailure",
        "require_parameters": True,
        "output_token_parameter": "max_tokens",
    }
    changed = settings.model_copy(update={"web_rule_agent_max_total_tokens": 4_000_000})
    assert (
        rule_agent_snapshot(changed, descriptor)["config_fingerprint"]
        != snapshot["config_fingerprint"]
    )
    changed = settings.model_copy(update={"web_rule_agent_max_model_calls": 60})
    assert (
        rule_agent_snapshot(changed, descriptor)["config_fingerprint"]
        != snapshot["config_fingerprint"]
    )
