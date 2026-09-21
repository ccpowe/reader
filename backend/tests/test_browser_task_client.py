from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.browser_tasks.client import BrowserTaskClient, BrowserTaskError
from app.browser_tasks.protocol import RuntimeIdentity, TaskPurpose
from app.core.settings import Settings
from app.ingestion import web_crawl
from app.ingestion.web_crawl_types import PageRecipe

SHA = "a" * 64
DIGEST = "sha256:" + "b" * 64


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


def created_payload(runtime: RuntimeIdentity) -> dict:
    now = datetime.now(UTC)
    return {
        "task_id": str(uuid4()),
        "state": "running",
        "capability": "c" * 43,
        "adapter_endpoint": "http://adapter-task:8765",
        "lease_expires_at": (now + timedelta(seconds=90)).isoformat(),
        "hard_expires_at": (now + timedelta(seconds=900)).isoformat(),
        "runtime_identity": runtime.model_dump(mode="json"),
    }


def status_payload(runtime: RuntimeIdentity, task_id: str, state: str = "running") -> dict:
    now = datetime.now(UTC)
    return {
        "task_id": task_id,
        "purpose": "explore",
        "state": state,
        "lease_expires_at": (now + timedelta(seconds=90)).isoformat(),
        "hard_expires_at": (now + timedelta(seconds=900)).isoformat(),
        "runtime_identity": runtime.model_dump(mode="json"),
    }


async def test_client_reads_controller_token_from_group_readable_secret(tmp_path: Path) -> None:
    secret = tmp_path / "controller-token"
    secret.write_text("file-secret-token\n")
    secret.chmod(0o440)
    settings = Settings(
        _env_file=None,
        browser_controller_url="http://manager:8091",
        browser_controller_token_file=secret,
    )

    client = BrowserTaskClient.from_settings(settings)

    assert client.controller_headers == {"Authorization": "Bearer file-secret-token"}
    await client.aclose()


@pytest.mark.parametrize("mode", [0o446, 0o460])
def test_client_rejects_unsafe_controller_token_file_permissions(tmp_path: Path, mode: int) -> None:
    secret = tmp_path / "controller-token"
    secret.write_text("file-secret-token")
    secret.chmod(mode)
    settings = Settings(
        _env_file=None,
        browser_controller_url="http://manager:8091",
        browser_controller_token_file=secret,
    )

    with pytest.raises(BrowserTaskError, match="unavailable or unsafe"):
        BrowserTaskClient.from_settings(settings)


def test_client_rejects_controller_token_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("file-secret-token")
    target.chmod(0o400)
    secret = tmp_path / "controller-token"
    secret.symlink_to(target)
    settings = Settings(
        _env_file=None,
        browser_controller_url="http://manager:8091",
        browser_controller_token_file=secret,
    )

    with pytest.raises(BrowserTaskError, match="unavailable or unsafe"):
        BrowserTaskClient.from_settings(settings)


async def test_create_keeps_capability_out_of_repr_and_uses_fixed_headers() -> None:
    runtime = identity()
    task = str(uuid4())

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer manager-secret"
        if request.url.path == "/v1/tasks":
            payload = created_payload(runtime)
            payload["task_id"] = task
            return httpx.Response(200, json=payload)
        assert request.url.path == f"/v1/tasks/{task}/close"
        return httpx.Response(200, json=status_payload(runtime, task, "closed"))

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BrowserTaskClient("http://manager:8764", "manager-secret", client=http)
    session = await client.create(
        purpose=TaskPurpose.EXPLORE,
        source_url="https://example.com/",
        owner_job_id="job-1",
        expected_runtime_fingerprint=runtime.fingerprint(),
    )

    assert "c" * 43 not in repr(session)
    await session.aclose()
    await http.aclose()


async def test_task_heartbeat_renews_during_idle_wait_and_stops_on_close() -> None:
    runtime = identity()
    task = str(uuid4())
    renewals = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal renewals
        if request.url.path == "/v1/tasks":
            payload = created_payload(runtime)
            payload["task_id"] = task
            return httpx.Response(200, json=payload)
        if request.url.path.endswith("/renew"):
            renewals += 1
            return httpx.Response(200, json=status_payload(runtime, task))
        if request.url.path.endswith("/close"):
            return httpx.Response(200, json=status_payload(runtime, task, "closed"))
        raise AssertionError(request.url)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BrowserTaskClient(
        "http://manager:8764", "manager-secret", heartbeat_seconds=0.01, client=http
    )
    session = await client.create(
        purpose=TaskPurpose.EXPLORE,
        source_url="https://example.com/",
        owner_job_id="job-1",
        expected_runtime_fingerprint=runtime.fingerprint(),
    )
    await asyncio.sleep(0.035)
    assert renewals >= 2
    await session.aclose()
    stopped_at = renewals
    await asyncio.sleep(0.02)
    assert renewals == stopped_at
    await http.aclose()


async def test_create_rejects_identity_changed_between_descriptor_and_task() -> None:
    expected = identity()
    actual = identity().model_copy(update={"policy_version": "changed"})
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=created_payload(actual))
        )
    )
    client = BrowserTaskClient("http://manager:8764", "manager-secret", client=http)

    with pytest.raises(BrowserTaskError) as caught:
        await client.create(
            purpose=TaskPurpose.RENDER,
            source_url="https://example.com/",
            owner_job_id="scan-1",
            expected_runtime_fingerprint=expected.fingerprint(),
        )
    assert caught.value.code == "browser_configuration_changed"
    await http.aclose()


async def test_structured_capacity_error_preserves_retry_fields() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                429,
                json={
                    "code": "capacity_exhausted",
                    "message": "No render slot is currently available.",
                    "kind": "capacity",
                    "retryable": True,
                    "retry_after_seconds": 2,
                },
            )
        )
    )
    client = BrowserTaskClient("http://manager:8764", "manager-secret", client=http)

    with pytest.raises(BrowserTaskError) as caught:
        await client.descriptor()
    assert caught.value.retryable and caught.value.retry_after_seconds == 2
    await http.aclose()


async def test_dynamic_crawl_uses_remote_render_without_loading_local_browser(
    monkeypatch,
) -> None:
    settings = Settings(
        _env_file=None,
        browser_controller_url="http://reader-browser-manager:8091",
        browser_controller_token="manager-secret-that-is-long-enough",
    )
    expected_identity = identity().model_dump(mode="json")
    called = {}

    async def remote_render(
        runtime_settings,
        *,
        source_url,
        url,
        recipe,
        maximum_bytes,
        expected_runtime_identity,
    ):
        called.update(
            settings=runtime_settings,
            source_url=source_url,
            url=url,
            recipe=recipe,
            maximum_bytes=maximum_bytes,
            identity=expected_runtime_identity,
        )
        from app.browser_tasks.protocol import RenderedPageResponse

        return RenderedPageResponse(
            final_url=url,
            status_code=200,
            safe_headers={"etag": "remote"},
            rendered_html="<html>remote</html>",
            rows=[{"title": "Remote"}],
        )

    monkeypatch.setattr(web_crawl, "get_settings", lambda: settings)
    monkeypatch.setattr("app.browser_tasks.client.render_page_with_browser_task", remote_render)

    async with httpx.AsyncClient() as http:
        page = await web_crawl.crawl_page(
            http,
            "https://example.com/list",
            PageRecipe.model_validate(
                {"render_js": True, "extraction": {"baseSelector": "article"}}
            ),
            maximum_bytes=1024 * 1024,
            robots_allows=lambda *_args: pytest.fail("robots runs inside the adapter"),
            browser_identity=expected_identity,
        )

    assert page.response.text == "<html>remote</html>" and page.rows == [{"title": "Remote"}]
    assert called == {
        "settings": settings,
        "source_url": "https://example.com/list",
        "url": "https://example.com/list",
        "recipe": PageRecipe.model_validate(
            {"render_js": True, "extraction": {"baseSelector": "article"}}
        ),
        "maximum_bytes": 1024 * 1024,
        "identity": expected_identity,
    }
