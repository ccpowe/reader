from __future__ import annotations

from app.browser_tasks.client import BrowserTaskError
from app.browser_tasks.protocol import ErrorEnvelope, ErrorKind
from app.core.settings import Settings
from app.workers import web_rules as worker


async def test_remote_rule_worker_pauses_before_claim_when_controller_is_unavailable(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Store:
        async def _call(self, name, **_kwargs):
            calls.append(name)
            if name == "backfill_web_rule_jobs":
                return 0
            raise AssertionError("worker must not claim without a runtime descriptor")

    class Client:
        @classmethod
        def from_settings(cls, _settings):
            return cls()

        async def descriptor(self):
            raise BrowserTaskError(
                ErrorEnvelope(
                    code="browser_runtime_unavailable",
                    message="controller unavailable",
                    kind=ErrorKind.LIFECYCLE,
                    retryable=True,
                )
            )

        async def aclose(self):
            calls.append("browser_client_closed")

    monkeypatch.setattr(worker, "BrowserTaskClient", Client)
    result = await worker.run_web_rule_author_once(
        None,
        settings=Settings(
            _env_file=None,
            web_rule_agent_engine_id="deepseek-v4-flash",
            browser_controller_url="http://reader-browser-manager:8091",
            browser_controller_token="manager-secret-that-is-long-enough",
        ),
        store=Store(),
    )

    assert result == 0
    assert result.metrics == {
        "mode": "paused",
        "last_error_code": "browser_runtime_unavailable",
    }
    assert calls == ["backfill_web_rule_jobs", "browser_client_closed"]
