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
        path = Path(__file__).parents[2] / "services" / "scweet_service" / "app.py"
        spec = importlib.util.spec_from_file_location("reader_scweet_service_app", path)
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


@pytest.mark.asyncio
async def test_scweet_cursor_resumes_after_oldest_surviving_boundary_when_head_deletes() -> None:
    service = _load_service_module()

    class Runtime:
        async def get_profile_tweets(self, _usernames, limit):
            if limit == 3:
                return [_tweet("a"), _tweet("b"), _tweet("c")]
            # Tweet b disappeared after the first page. A raw offset of three
            # would start at e and skip d; the stable c boundary must start at d.
            return [_tweet("a"), _tweet("c"), _tweet("d"), _tweet("e"), _tweet("f")]

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=Runtime())))
    first = await service.profile_tweets(
        service.ProfileTweetsRequest(usernames=["OpenAI"], limit=3), request
    )
    second = await service.profile_tweets(
        service.ProfileTweetsRequest(
            usernames=["OpenAI"],
            limit=3,
            cursor=first.continuation,
            boundary_ids=first.boundary_ids,
        ),
        request,
    )

    assert [_native_id(item) for item in second.items] == ["d", "e", "f"]
    assert second.completed is True


@pytest.mark.asyncio
async def test_scweet_runtime_falls_back_to_latest_search_for_an_empty_profile_timeline() -> None:
    service = _load_service_module()
    calls: dict[str, object] = {}

    class Client:
        _accounts_repo = SimpleNamespace(count_eligible=lambda: 1)

        def get_profile_tweets(self, usernames, *, limit, save):
            return []

        def get_user_info(self, usernames, *, save):
            return [{"username": "CodexReleases"}]

        def search(self, query, **kwargs):
            calls["query"] = query
            calls.update(kwargs)
            return [
                _tweet("9", author="OtherAccount"),
                _tweet("2", author="codexreleases"),
                _tweet("10", author="CodexReleases"),
                _tweet("10", author="CodexReleases"),
            ]

    runtime = service.ScweetRuntime.__new__(service.ScweetRuntime)
    runtime._client = Client()
    runtime._lock = service.asyncio.Lock()

    items = await runtime.get_profile_tweets(["CodexReleases"], 10)

    assert [_native_id(item) for item in items] == ["10", "2"]
    assert calls["query"] == ""
    assert calls["from_users"] == ["CodexReleases"]
    assert calls["display_type"] == "Latest"
    assert calls["max_empty_pages"] == 3
    assert calls["limit"] == 10


@pytest.mark.asyncio
async def test_scweet_service_rejects_an_unverified_empty_timeline() -> None:
    service = _load_service_module()

    class Client:
        _accounts_repo = SimpleNamespace(count_eligible=lambda: 1)

        def get_profile_tweets(self, usernames, *, limit, save):
            return []

        def get_user_info(self, usernames, *, save):
            return []

        def search(self, query, **kwargs):
            return []

    runtime = service.ScweetRuntime.__new__(service.ScweetRuntime)
    runtime._client = Client()
    runtime._lock = service.asyncio.Lock()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=runtime)))

    with pytest.raises(service.HTTPException) as error:
        await service.profile_tweets(
            service.ProfileTweetsRequest(usernames=["MissingAccount"], limit=10), request
        )

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "timeline_empty_unverified"


@pytest.mark.asyncio
async def test_scweet_runtime_allows_a_confirmed_profile_with_no_recent_tweets() -> None:
    service = _load_service_module()

    class Client:
        _accounts_repo = SimpleNamespace(count_eligible=lambda: 1)

        def get_profile_tweets(self, usernames, *, limit, save):
            return []

        def get_user_info(self, usernames, *, save):
            return [{"username": "DormantAccount"}]

        def search(self, query, **kwargs):
            return []

    runtime = service.ScweetRuntime.__new__(service.ScweetRuntime)
    runtime._client = Client()
    runtime._lock = service.asyncio.Lock()

    assert await runtime.get_profile_tweets(["DormantAccount"], 10) == []


def test_scweet_account_pool_exhaustion_is_reported_as_rate_limited() -> None:
    service = _load_service_module()

    class AccountPoolExhausted(RuntimeError):
        pass

    assert service._collector_error_code(AccountPoolExhausted("No eligible account")) == (
        "rate_limited"
    )


def _tweet(native_id: str, *, author: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {"tweet_id": native_id, "text": native_id}
    if author is not None:
        item["user"] = {"screen_name": author}
    return item


def _native_id(item: dict[str, object]) -> str:
    return str(item["tweet_id"])
