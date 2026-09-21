from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.browser_tasks.adapter_app import create_adapter_app
from app.browser_tasks.adapter_runtime import (
    AdapterConfiguration,
    AdapterProtocolError,
    AdapterRuntime,
)
from app.browser_tasks.auth import capability_hash
from app.browser_tasks.protocol import (
    CLICommandRequest,
    HealthResponse,
    RenderPageRequest,
    RuntimeIdentity,
    TaskPurpose,
)

SHA = "a" * 64
DIGEST = "sha256:" + "b" * 64
CAPABILITY = "c" * 43


def identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        policy_version="reader-browser-container-v1",
        browser_image_digest=DIGEST,
        adapter_image_digest=DIGEST,
        lightpanda_version="0.4.0",
        lightpanda_sha256=SHA,
        agent_browser_version="0.37.1",
        agent_browser_sha256=SHA,
        config_fingerprint=SHA,
        explore_hard_ttl_seconds=7200,
        render_hard_ttl_seconds=900,
    )


def runtime(purpose: TaskPurpose) -> AdapterRuntime:
    value = AdapterRuntime(
        AdapterConfiguration(
            task_id=uuid4(),
            purpose=purpose,
            source_url="https://example.com/blog",
            capability_sha256=capability_hash(CAPABILITY),
            runtime_identity=identity(),
        )
    )
    value.health = HealthResponse(status="ready")
    return value


def test_adapter_openapi_declares_structured_error_envelopes() -> None:
    schema = create_adapter_app(runtime=runtime(TaskPurpose.EXPLORE)).openapi()
    for path in ("/v1/cli-command", "/v1/render-page"):
        responses = schema["paths"][path]["post"]["responses"]
        for status in ("401", "409", "422", "502", "504"):
            assert responses[status]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorEnvelope"
            }
    assert "HTTPValidationError" not in schema["components"]["schemas"]


async def test_adapter_validation_and_auth_errors_use_error_envelope() -> None:
    value = runtime(TaskPurpose.EXPLORE)
    app = create_adapter_app(runtime=value)
    app.state.runtime = value
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://adapter") as client:
        invalid = await client.post("/v1/cli-command", json={})
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "invalid_request"

        unauthorized = await client.post(
            "/v1/cli-command",
            json={
                "request_id": str(uuid4()),
                "task_id": str(value.configuration.task_id),
                "argv": ["snapshot"],
            },
        )
        assert unauthorized.status_code == 401
        assert unauthorized.json()["code"] == "unauthorized"


async def test_cli_report_preserves_large_native_output_and_idempotent_result() -> None:
    value = runtime(TaskPurpose.EXPLORE)
    calls = 0

    class Session:
        async def command(self, _argv, *, observed_urls):
            nonlocal calls
            calls += 1
            assert observed_urls == ["https://example.com/seen"]
            return {
                "status": "ok",
                "url": "https://example.com/blog",
                "page_revision": SHA,
                "format": "snapshot",
                "output": "x" * 200_000,
                "truncated": True,
                "truncated_fields": ["stdout_capture"],
                "error": None,
                "stderr": "",
                "network_errors": [],
            }

    value.cli_session = Session()
    request = CLICommandRequest(
        request_id=uuid4(),
        task_id=value.configuration.task_id,
        argv=["snapshot", "-u"],
        observed_urls=["https://example.com/seen"],
    )

    first = await value.cli_command(request, CAPABILITY)
    second = await value.cli_command(request, CAPABILITY)
    assert len(first.output) == 200_000 and second == first and calls == 1


async def test_adapter_rejects_wrong_capability_and_cross_task_request() -> None:
    value = runtime(TaskPurpose.EXPLORE)
    request = CLICommandRequest(
        request_id=uuid4(), task_id=value.configuration.task_id, argv=["snapshot"]
    )
    with pytest.raises(AdapterProtocolError) as caught:
        await value.cli_command(request, "wrong")
    assert caught.value.code == "unauthorized"

    cross_task = request.model_copy(update={"task_id": uuid4()})
    with pytest.raises(AdapterProtocolError) as caught:
        await value.cli_command(cross_task, CAPABILITY)
    assert caught.value.code == "task_mismatch"


async def test_render_is_one_logical_same_origin_request(monkeypatch) -> None:
    value = runtime(TaskPurpose.RENDER)
    calls = 0

    async def fake_crawl(_client, url, recipe, **kwargs):
        nonlocal calls
        calls += 1
        assert recipe.render_js and kwargs["maximum_bytes"] == 1024 * 1024
        return SimpleNamespace(
            response=httpx.Response(
                200,
                headers={"etag": "v1", "set-cookie": "secret"},
                text="<html>rendered</html>",
                request=httpx.Request("GET", url),
            ),
            rows=[{"url": "/one", "title": "One"}],
        )

    monkeypatch.setattr("app.browser_tasks.adapter_runtime.crawl_page", fake_crawl)
    request = RenderPageRequest(
        request_id=uuid4(),
        task_id=value.configuration.task_id,
        url="https://example.com/blog",
        recipe={"render_js": True, "extraction": {"baseSelector": "article"}},
        max_response_bytes=1024 * 1024,
    )
    first = await value.render_page(request, CAPABILITY)
    second = await value.render_page(request, CAPABILITY)
    assert second == first and calls == 1
    assert first.safe_headers["etag"] == "v1" and "set-cookie" not in first.safe_headers

    with pytest.raises(AdapterProtocolError) as caught:
        await value.render_page(request.model_copy(update={"request_id": uuid4()}), CAPABILITY)
    assert caught.value.code == "render_already_used"


async def test_render_rejects_cross_origin_before_network(monkeypatch) -> None:
    value = runtime(TaskPurpose.RENDER)
    monkeypatch.setattr(
        "app.browser_tasks.adapter_runtime.crawl_page",
        lambda *_args, **_kwargs: pytest.fail("network must not start"),
    )
    request = RenderPageRequest(
        request_id=uuid4(),
        task_id=value.configuration.task_id,
        url="https://other.example/blog",
        recipe={"render_js": True, "extraction": {"baseSelector": "article"}},
        max_response_bytes=1024 * 1024,
    )
    with pytest.raises(AdapterProtocolError) as caught:
        await value.render_page(request, CAPABILITY)
    assert caught.value.code == "invalid_render_url"


async def test_failed_render_request_id_cannot_be_reused_with_different_input(
    monkeypatch,
) -> None:
    value = runtime(TaskPurpose.RENDER)

    async def fail_crawl(*_args, **_kwargs):
        raise httpx.ConnectError("fixture unavailable")

    monkeypatch.setattr("app.browser_tasks.adapter_runtime.crawl_page", fail_crawl)
    request = RenderPageRequest(
        request_id=uuid4(),
        task_id=value.configuration.task_id,
        url="https://example.com/blog",
        recipe={"render_js": True, "extraction": {"baseSelector": "article"}},
        max_response_bytes=1024 * 1024,
    )
    with pytest.raises(httpx.ConnectError):
        await value.render_page(request, CAPABILITY)

    changed = RenderPageRequest.model_validate(
        {**request.model_dump(mode="json"), "url": "https://example.com/other"}
    )
    with pytest.raises(AdapterProtocolError) as caught:
        await value.render_page(changed, CAPABILITY)
    assert caught.value.code == "request_conflict"
