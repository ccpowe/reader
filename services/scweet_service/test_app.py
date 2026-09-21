from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_service_module():
    scweet_stub = ModuleType("Scweet")
    scweet_stub.Scweet = object
    scweet_stub.ScweetConfig = object
    previous = sys.modules.get("Scweet")
    sys.modules["Scweet"] = scweet_stub
    try:
        path = Path(__file__).with_name("app.py")
        spec = importlib.util.spec_from_file_location("reader_scweet_service_unit_app", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("Scweet", None)
        else:
            sys.modules["Scweet"] = previous


def _runtime_with_diagnostics(service, diagnostics, *, eligible=1):
    runtime = service.ScweetRuntime.__new__(service.ScweetRuntime)
    repo = SimpleNamespace(
        eligibility_diagnostics=lambda **_kwargs: diagnostics,
        count_eligible=lambda: eligible,
    )
    runtime._client = SimpleNamespace(_accounts_repo=repo)
    return runtime


def test_account_configuration_accepts_a_temporarily_cooling_down_account() -> None:
    service = _load_service_module()
    runtime = _runtime_with_diagnostics(
        service,
        {
            "total": 1,
            "eligible": 0,
            "blocked_samples": [{"reasons": ["cooldown"]}],
        },
    )

    runtime._assert_account_configured()


@pytest.mark.parametrize(
    "diagnostics",
    [
        {"total": 0, "eligible": 0, "blocked_samples": []},
        {
            "total": 1,
            "eligible": 0,
            "blocked_samples": [{"reasons": ["status", "missing_csrf", "missing_cookies"]}],
        },
    ],
)
def test_account_configuration_rejects_missing_local_auth_material(diagnostics) -> None:
    service = _load_service_module()
    runtime = _runtime_with_diagnostics(service, diagnostics)

    with pytest.raises(service.ScweetConfigurationError):
        runtime._assert_account_configured()


def test_unavailable_account_fails_immediately_as_rate_limited() -> None:
    service = _load_service_module()
    runtime = _runtime_with_diagnostics(service, {}, eligible=0)

    with pytest.raises(service.AccountTemporarilyUnavailable) as error:
        runtime._assert_account_available()

    assert service._collector_error_code(error.value) == "rate_limited"


def test_missing_account_repository_fails_closed() -> None:
    service = _load_service_module()
    runtime = service.ScweetRuntime.__new__(service.ScweetRuntime)
    runtime._client = SimpleNamespace()

    with pytest.raises(service.ScweetConfigurationError):
        runtime._assert_account_available()

    with pytest.raises(service.ScweetConfigurationError):
        runtime.is_ready()


@pytest.mark.asyncio
async def test_ready_tracks_current_account_eligibility() -> None:
    service = _load_service_module()
    runtime = _runtime_with_diagnostics(service, {}, eligible=0)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=runtime)))

    with pytest.raises(service.HTTPException) as error:
        await service.ready(request)

    assert error.value.status_code == 503
    runtime._client._accounts_repo.count_eligible = lambda: 1
    assert await service.ready(request) == {"status": "ready"}
