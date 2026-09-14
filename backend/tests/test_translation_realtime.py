import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.settings import Settings
from app.domain.enums import TranslationPurpose, TranslationScope, TranslationStatus
from app.translation.domain import TranslationEngine, TranslationResult
from app.translation.projection import TextInput, TranslationContext
from app.translation.quota import TranslationQuotaExceeded
from app.translation.realtime import RealtimeCoordinator


def _context() -> TranslationContext:
    return TranslationContext(
        target_locale="zh-CN",
        enabled=True,
        engine=TranslationEngine(
            engine_id="deepseek-v4-flash",
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
        ),
        engine_label="DeepSeek V4 Flash",
    )


def _item(item_id: str = "segment", text: str = "source") -> TextInput:
    return TextInput(
        item_id=item_id,
        text=text,
        purpose=TranslationPurpose.WEB_SEGMENT,
        scope=TranslationScope.USER,
        owner_id=USER_ID,
    )


class SessionContext:
    def __init__(self, owner: "SessionFactory") -> None:
        self.owner = owner
        self.session = SimpleNamespace(commit=AsyncMock())

    async def __aenter__(self):
        self.owner.entered += 1
        return self.session

    async def __aexit__(self, *_args):
        self.owner.exited += 1


class SessionFactory:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    def __call__(self) -> SessionContext:
        return SessionContext(self)


class SingleConnectionFactory(SessionFactory):
    def __init__(self) -> None:
        super().__init__()
        self.lease = asyncio.Semaphore(1)

    def __call__(self):
        owner = self

        class Context(SessionContext):
            async def __aenter__(self):
                await asyncio.wait_for(owner.lease.acquire(), timeout=0.1)
                return await super().__aenter__()

            async def __aexit__(self, *_args):
                await super().__aexit__(*_args)
                owner.lease.release()

        return Context(self)


class BlockingProvider:
    name = "deepseek"
    model_name = "deepseek-v4-flash"

    def __init__(self, *, blocked: bool = True) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()

    async def translate_batch(self, items, *, source_locale, target_locale):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return [
            TranslationResult(item_id=item.item_id, translated_text=f"译文:{item.text}")
            for item in items
        ]


USER_ID = uuid4()


@pytest.fixture
def cache_misses(monkeypatch: pytest.MonkeyPatch):
    async def project(session, items, *, context, ensure_missing):
        assert ensure_missing is False
        return {}

    monkeypatch.setattr("app.translation.realtime.project_texts", project)


@pytest.mark.asyncio
async def test_cache_session_closes_before_provider_wait(cache_misses) -> None:
    sessions = SessionFactory()
    provider = BlockingProvider()
    coordinator = RealtimeCoordinator(session_factory=sessions, max_concurrency=1)

    result = await coordinator.resolve(
        [_item()],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0.01,
    )

    assert result["segment"].status == TranslationStatus.PENDING
    assert provider.started.is_set()
    assert sessions.entered == sessions.exited == 1
    provider.release.set()
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_slow_provider_does_not_block_next_cache_checkout(cache_misses) -> None:
    sessions = SingleConnectionFactory()
    provider = BlockingProvider()
    coordinator = RealtimeCoordinator(session_factory=sessions, max_concurrency=1)
    first = asyncio.create_task(
        coordinator.resolve(
            [_item("first")],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=1,
        )
    )
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    second = await coordinator.resolve(
        [_item("second")],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0,
    )

    assert second["second"].status == TranslationStatus.PENDING
    assert sessions.entered == sessions.exited == 2
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    provider.release.set()
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_provider_result_does_not_wait_for_persistence_and_terminal_keeps_text(
    cache_misses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = SessionFactory()
    provider = BlockingProvider(blocked=False)
    persist_started = asyncio.Event()
    persist_release = asyncio.Event()

    async def persist(*_args):
        persist_started.set()
        await persist_release.wait()

    monkeypatch.setattr("app.translation.realtime.persist_interactive_artifacts", persist)
    coordinator = RealtimeCoordinator(session_factory=sessions)

    first = await coordinator.resolve(
        [_item("first")],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=1,
    )
    assert first["first"].translated_text == "译文:source"
    await asyncio.wait_for(persist_started.wait(), timeout=1)
    assert not persist_release.is_set()

    second = await coordinator.resolve(
        [_item("second")],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=1,
    )
    assert second["second"].status == TranslationStatus.SUCCEEDED
    assert second["second"].translated_text == "译文:source"
    assert provider.calls == 1

    persist_release.set()
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_runner(cache_misses) -> None:
    provider = BlockingProvider()
    coordinator = RealtimeCoordinator(session_factory=SessionFactory())
    first_waiter = asyncio.create_task(
        coordinator.resolve(
            [_item("first")],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=10,
        )
    )
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    second_waiter = asyncio.create_task(
        coordinator.resolve(
            [_item("second")],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=1,
        )
    )
    await asyncio.sleep(0)
    provider.release.set()
    second = await second_waiter

    assert second["second"].translated_text == "译文:source"
    assert provider.calls == 1
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_pending_poll_joins_without_duplicate_quota_or_provider(cache_misses) -> None:
    provider = BlockingProvider()
    quota = SimpleNamespace(reserve=AsyncMock(), check_allowed=AsyncMock())
    coordinator = RealtimeCoordinator(session_factory=SessionFactory(), quota=quota)

    first = await coordinator.resolve(
        [_item("first")],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0,
    )
    await asyncio.wait_for(provider.started.wait(), timeout=1)
    second = await coordinator.resolve(
        [_item("second")],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0,
    )

    assert first["first"].status == TranslationStatus.PENDING
    assert second["second"].status == TranslationStatus.PENDING
    assert quota.reserve.await_count == 1
    assert provider.calls == 1

    provider.release.set()
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_quota_wait_does_not_block_join_and_reserves_once(cache_misses) -> None:
    provider = BlockingProvider(blocked=False)
    quota_started = asyncio.Event()
    quota_release = asyncio.Event()
    quota_calls = 0

    async def reserve(*_args, **_kwargs):
        nonlocal quota_calls
        quota_calls += 1
        quota_started.set()
        await quota_release.wait()

    coordinator = RealtimeCoordinator(
        session_factory=SessionFactory(),
        quota=SimpleNamespace(reserve=reserve, check_allowed=AsyncMock()),
    )
    first = asyncio.create_task(
        coordinator.resolve(
            [_item("first")],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=1,
        )
    )
    await asyncio.wait_for(quota_started.wait(), timeout=1)
    second = asyncio.create_task(
        coordinator.resolve(
            [_item("second")],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=1,
        )
    )
    await asyncio.sleep(0)

    assert not second.done()
    assert quota_calls == 1
    quota_release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result["first"].translated_text == "译文:source"
    assert second_result["second"].translated_text == "译文:source"
    assert quota_calls == provider.calls == 1
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_quota_failure_releases_coverage_without_terminal(cache_misses) -> None:
    provider = BlockingProvider(blocked=False)
    quota = SimpleNamespace(
        check_allowed=AsyncMock(),
        reserve=AsyncMock(
            side_effect=[
                TranslationQuotaExceeded("translation_quota_requests_exceeded", 2),
                None,
            ]
        ),
    )
    coordinator = RealtimeCoordinator(session_factory=SessionFactory(), quota=quota)

    with pytest.raises(TranslationQuotaExceeded):
        await coordinator.resolve(
            [_item()],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=1,
        )

    retried = await coordinator.resolve(
        [_item()],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=1,
    )
    assert retried["segment"].translated_text == "译文:source"
    assert quota.reserve.await_count == 2
    assert provider.calls == 1
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_close_drains_late_persistence_and_rejects_new_batches(
    cache_misses,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = BlockingProvider(blocked=False)
    persist_started = asyncio.Event()
    persist_release = asyncio.Event()

    async def persist(*_args):
        persist_started.set()
        await persist_release.wait()

    monkeypatch.setattr("app.translation.realtime.persist_interactive_artifacts", persist)
    coordinator = RealtimeCoordinator(session_factory=SessionFactory())
    completed = await coordinator.resolve(
        [_item()],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=1,
    )
    assert completed["segment"].status == TranslationStatus.SUCCEEDED
    await asyncio.wait_for(persist_started.wait(), timeout=1)

    closing = asyncio.create_task(coordinator.aclose(1))
    await asyncio.sleep(0)
    rejected = await coordinator.resolve(
        [_item("other", "other source")],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0,
    )
    assert rejected["other"].status == TranslationStatus.PENDING
    assert provider.calls == 1
    assert not closing.done()

    persist_release.set()
    await closing
    assert not coordinator._owned_tasks


@pytest.mark.asyncio
async def test_close_timeout_cancels_runner_and_releases_coverage(cache_misses) -> None:
    provider = BlockingProvider()
    coordinator = RealtimeCoordinator(session_factory=SessionFactory())
    pending = await coordinator.resolve(
        [_item()],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0,
    )
    assert pending["segment"].status == TranslationStatus.PENDING
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    await coordinator.aclose(0)

    assert not coordinator._coverage
    assert not coordinator._owned_tasks


@pytest.mark.asyncio
async def test_close_timeout_does_not_wait_forever_for_cancellation_suppression(
    cache_misses,
) -> None:
    class StubbornProvider(BlockingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.cancel_suppressed = asyncio.Event()
            self.final_release = asyncio.Event()

        async def translate_batch(self, items, *, source_locale, target_locale):
            self.calls += 1
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancel_suppressed.set()
                await self.final_release.wait()
            return [
                TranslationResult(item_id=item.item_id, translated_text=f"译文:{item.text}")
                for item in items
            ]

    provider = StubbornProvider()
    coordinator = RealtimeCoordinator(session_factory=SessionFactory())
    pending = await coordinator.resolve(
        [_item()],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0,
    )
    assert pending["segment"].status == TranslationStatus.PENDING
    await asyncio.wait_for(provider.started.wait(), timeout=1)

    await asyncio.wait_for(coordinator.aclose(0), timeout=0.75)

    assert provider.cancel_suppressed.is_set()
    stubborn = list(coordinator._owned_tasks)
    assert stubborn
    provider.final_release.set()
    await asyncio.wait_for(asyncio.gather(*stubborn, return_exceptions=True), timeout=1)
    await asyncio.sleep(0)
    assert not coordinator._owned_tasks


@pytest.mark.asyncio
async def test_lifespan_closes_coordinator_before_provider_and_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    order: list[str] = []

    class Probe:
        async def prime(self, *_args, **_kwargs):
            return []

        async def close(self):
            order.append("probe")

    class Engine:
        async def dispose(self):
            order.append("database")

    class Coordinator:
        def __init__(self, **_kwargs):
            pass

        async def aclose(self, _timeout):
            order.append("coordinator")

    class Verifier:
        async def aclose(self):
            order.append("verifier")

    async def close_providers(*_args, **_kwargs):
        order.append("providers")

    settings = Settings(
        _env_file=None,
        database_url="postgresql://reader:test@example.com/reader",
    )
    engine = Engine()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "build_engine", lambda _settings: engine)
    monkeypatch.setattr(main, "build_session_factory", lambda _engine: object())
    monkeypatch.setattr(main, "build_translation_providers", lambda _settings: {})
    monkeypatch.setattr(main, "RealtimeCoordinator", Coordinator)
    monkeypatch.setattr(main, "ReaderJWTVerifier", lambda *_args: Verifier())
    monkeypatch.setattr(main, "close_translation_providers", close_providers)
    app = SimpleNamespace(state=SimpleNamespace(readiness_probe=Probe()))

    async with main.lifespan(app):
        pass

    assert order.index("coordinator") < order.index("providers") < order.index("database")


@pytest.mark.asyncio
async def test_retryable_failure_recovers_without_new_subscriber_task(cache_misses, monkeypatch):
    from app.translation.domain import TranslationProviderError

    provider = BlockingProvider(blocked=False)

    async def translate(items, **_kwargs):
        provider.calls += 1
        if provider.calls == 1:
            raise TranslationProviderError("invalid", code="invalid_response", retryable=True)
        return [
            TranslationResult(item_id=item.item_id, translated_text="恢复译文") for item in items
        ]

    provider.translate_batch = translate
    coordinator = RealtimeCoordinator(session_factory=SessionFactory(), retry_delays=(0, 0))
    first = await coordinator.resolve(
        [_item("first")], context=_context(), provider=provider, user_id=USER_ID, wait_seconds=0.01
    )
    assert first["first"].status == TranslationStatus.PENDING
    second = await coordinator.resolve(
        [_item("second")], context=_context(), provider=provider, user_id=USER_ID, wait_seconds=2
    )
    assert second["second"].translated_text == "恢复译文"
    assert provider.calls == 2
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_failure_budget_cooldown_and_expiry(cache_misses):
    from app.translation.domain import TranslationProviderError

    provider = BlockingProvider(blocked=False)

    async def translate(items, **_kwargs):
        provider.calls += 1
        raise TranslationProviderError("invalid", code="invalid_response", retryable=True)

    provider.translate_batch = translate
    coordinator = RealtimeCoordinator(
        session_factory=SessionFactory(), retry_delays=(0, 0), failure_ttl=0.05
    )
    result = await coordinator.resolve(
        [_item()], context=_context(), provider=provider, user_id=USER_ID, wait_seconds=4
    )
    assert result["segment"].status == TranslationStatus.FAILED
    assert result["segment"].error_retryable is False
    assert provider.calls == 3
    await coordinator.resolve(
        [_item("changed-id")],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0,
    )
    assert provider.calls == 3
    await asyncio.sleep(0.06)
    await coordinator.resolve(
        [_item("reopened")],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0.01,
    )
    assert provider.calls == 4
    await coordinator.aclose(0)


@pytest.mark.parametrize(
    ("translated", "reason"),
    [
        ("预期", "missing_token"),
        ("⟪READER_OPEN_0⟫预期⟪READER_CLOSE_0⟫⟪READER_CLOSE_0⟫", "unknown_or_duplicate_token"),
        ("⟪READER_CLOSE_0⟫预期⟪READER_OPEN_0⟫", "invalid_token_nesting"),
        ("⟪READER_OPEN_0⟫预期⟪READER_CLOSE_0⟫", None),
    ],
)
def test_spotify_link_contract(translated, reason):
    from app.translation.rich_text import validate_tokens

    assert validate_tokens("⟪READER_OPEN_0⟫expected⟪READER_CLOSE_0⟫", translated) == reason


@pytest.mark.asyncio
async def test_invalid_link_translation_is_retried_and_only_valid_result_persisted(
    cache_misses, monkeypatch
):
    calls = []
    saved = []
    provider = BlockingProvider(blocked=False)
    source = "It is ⟪READER_OPEN_0⟫expected⟪READER_CLOSE_0⟫."

    async def translate(items, **_kwargs):
        calls.append(items)
        text = "这是预期的。" if len(calls) == 1 else "这是⟪READER_OPEN_0⟫预期⟪READER_CLOSE_0⟫的。"
        return [TranslationResult(item_id=item.item_id, translated_text=text) for item in items]

    async def persist(_factory, artifacts):
        saved.append(artifacts)

    provider.translate_batch = translate
    monkeypatch.setattr("app.translation.realtime.persist_interactive_artifacts", persist)
    coordinator = RealtimeCoordinator(session_factory=SessionFactory(), retry_delays=(0, 0))
    result = await coordinator.resolve(
        [_item(text=source)], context=_context(), provider=provider, user_id=USER_ID, wait_seconds=3
    )
    await coordinator.aclose(1)
    assert result["segment"].status == TranslationStatus.SUCCEEDED
    assert len(calls) == 2
    assert len(saved) == 1
    assert "⟪READER_OPEN_0⟫" in saved[0].translated_texts["segment"]


def test_reader_paragraph_format_has_versioned_identity_without_changing_plain_caption():
    from app.translation.domain import TranslationDemand, source_text_hash

    for purpose, text, versioned in [
        (TranslationPurpose.PARAGRAPH, "plain paragraph", False),
        (TranslationPurpose.CAPTION, "⟪READER_0⟫ caption literal", False),
        (TranslationPurpose.PARAGRAPH, "Read ⟪READER_OPEN_0⟫this⟪READER_CLOSE_0⟫", True),
        (TranslationPurpose.WEB_SEGMENT, "plain webpage", True),
    ]:
        demand = TranslationDemand(
            item_id="test", text=text, purpose=purpose, target_locale="zh-CN"
        )
        assert (
            demand.identity(_context().engine).source_hash != source_text_hash(text)
        ) is versioned


@pytest.mark.asyncio
async def test_split_isolation_counts_each_source_call_against_budget(cache_misses):
    from collections import Counter

    from app.translation.domain import TranslationProviderError

    counts = Counter()
    provider = BlockingProvider(blocked=False)

    async def translate(items, **_kwargs):
        counts.update(item.text for item in items)
        raise TranslationProviderError(
            "bad response", code="invalid_response", retryable=True, isolate_items=True
        )

    provider.translate_batch = translate
    coordinator = RealtimeCoordinator(session_factory=SessionFactory())
    items = [_item(str(index), f"source {index}") for index in range(8)]
    result = await coordinator.resolve(
        items, context=_context(), provider=provider, user_id=USER_ID, wait_seconds=1
    )
    assert all(projection.status == TranslationStatus.FAILED for projection in result.values())
    assert set(counts.values()) == {3}
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_terminal_pressure_does_not_evict_live_failure_cooldown(cache_misses):
    from app.translation.domain import TranslationProviderError

    provider = BlockingProvider(blocked=False)

    async def translate(items, **_kwargs):
        provider.calls += 1
        raise TranslationProviderError("rejected", retryable=False)

    provider.translate_batch = translate
    coordinator = RealtimeCoordinator(session_factory=SessionFactory(), terminal_limit=1)
    first = await coordinator.resolve(
        [_item("first")], context=_context(), provider=provider, user_id=USER_ID, wait_seconds=1
    )
    assert first["first"].status == TranslationStatus.FAILED
    overflow = await coordinator.resolve(
        [_item("other", "another source")],
        context=_context(),
        provider=provider,
        user_id=USER_ID,
        wait_seconds=0,
    )
    assert overflow["other"].status == TranslationStatus.PENDING
    assert overflow["other"].retry_after_ms == 1000
    await coordinator.resolve(
        [_item("new-id")], context=_context(), provider=provider, user_id=USER_ID, wait_seconds=0
    )
    assert provider.calls == 1
    await coordinator.aclose(1)


@pytest.mark.asyncio
async def test_token_retry_sends_item_specific_correction_to_actual_adapter(
    cache_misses, monkeypatch
):
    import json

    from app.translation.providers.langchain import LangChainChatModelProvider

    captured = []
    source = "Costs are ⟪READER_OPEN_0⟫expected⟪READER_CLOSE_0⟫ to rise."

    class Runnable:
        async def ainvoke(self, messages):
            payload = json.loads(messages[1][1])
            captured.append((messages[0][1], payload))
            return {
                "items": [
                    {
                        "item_id": item["item_id"],
                        "translated_text": (
                            "成本⟪READER_OPEN_0⟫预计⟪READER_CLOSE_0⟫会上升。"
                            if item.get("validation_feedback")
                            else "成本预计会上升。"
                        ),
                    }
                    for item in payload["items"]
                ]
            }

    class Model:
        def with_structured_output(self, _schema, **_kwargs):
            return Runnable()

    monkeypatch.setattr("app.translation.realtime.persist_interactive_artifacts", AsyncMock())
    coordinator = RealtimeCoordinator(session_factory=SessionFactory(), retry_delays=(0, 0))
    result = await coordinator.resolve(
        [_item("linked", source), _item("plain", "A separate plain paragraph.")],
        context=_context(),
        provider=LangChainChatModelProvider(Model()),
        user_id=USER_ID,
        wait_seconds=3,
    )
    await coordinator.aclose(1)
    assert result["linked"].status == TranslationStatus.SUCCEEDED
    assert result["plain"].status == TranslationStatus.SUCCEEDED
    assert len(captured) == 2
    expected = ["⟪READER_OPEN_0⟫", "⟪READER_CLOSE_0⟫"]
    assert captured[0][1]["items"][0]["expected_tokens"] == expected
    assert "validation_feedback" not in captured[0][1]["items"][0]
    assert [item["item_id"] for item in captured[1][1]["items"]] == ["linked"]
    assert captured[1][1]["items"][0]["validation_feedback"] == {
        "code": "placeholder_mismatch",
        "required_tokens": expected,
    }
    assert "成本⟪READER_OPEN_0⟫预计⟪READER_CLOSE_0⟫会上升。" in captured[1][0]
    assert "previous attempt failed" in captured[1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits",
    [dict(max_inflight_tasks=2), dict(max_inflight_tasks=10, max_inflight_chars=12)],
)
async def test_inflight_capacity_rejects_before_charge_and_still_joins(cache_misses, limits):
    quota = SimpleNamespace(reserve=AsyncMock(), check_allowed=AsyncMock())
    provider = BlockingProvider()
    coordinator = RealtimeCoordinator(
        session_factory=SessionFactory(), quota=quota, max_concurrency=1, **limits
    )
    try:
        for index in range(20):
            result = await coordinator.resolve(
                [_item(str(index), f"text-{index}")],
                context=_context(),
                provider=provider,
                user_id=USER_ID,
                wait_seconds=0,
            )
            assert result[str(index)].status == TranslationStatus.PENDING
            if index >= 2:
                assert result[str(index)].retry_after_ms == 1000
        assert len(coordinator._owned_tasks) == 2
        assert len(coordinator._coverage) == 2
        assert coordinator._inflight_chars == 12
        assert quota.reserve.await_count == 2
        await coordinator.resolve(
            [_item("join", "text-0")],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=0,
        )
        assert quota.reserve.await_count == 2
        assert len(coordinator._owned_tasks) == 2
        assert provider.calls == 1
    finally:
        await coordinator.aclose(0)
    assert coordinator._inflight_chars == 0
    assert not coordinator._coverage


@pytest.mark.asyncio
async def test_capacity_remains_reserved_during_persistence(cache_misses, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def persist(*args, **kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr("app.translation.realtime.persist_interactive_artifacts", persist)
    quota = SimpleNamespace(reserve=AsyncMock(), check_allowed=AsyncMock())
    provider = BlockingProvider(blocked=False)
    coordinator = RealtimeCoordinator(
        session_factory=SessionFactory(),
        quota=quota,
        max_inflight_tasks=1,
    )
    try:
        first = await coordinator.resolve(
            [_item()],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=1,
        )
        assert first["segment"].status == TranslationStatus.SUCCEEDED
        await asyncio.wait_for(started.wait(), 1)
        second = await coordinator.resolve(
            [_item("other", "different")],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=0,
        )
        assert second["other"].status == TranslationStatus.PENDING
        assert quota.reserve.await_count == 1
        assert coordinator._inflight_chars == len("source")
    finally:
        release.set()
        await coordinator.aclose(1)
    assert coordinator._inflight_chars == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["join", "terminal", "cache"])
async def test_disabled_subscriber_rejected_without_duplicate_charge(
    cache_misses,
    monkeypatch,
    path,
):
    from app.translation.quota import TranslationDisabledForUser

    quota = SimpleNamespace(reserve=AsyncMock(), check_allowed=AsyncMock())
    provider = BlockingProvider(blocked=path == "join")
    coordinator = RealtimeCoordinator(session_factory=SessionFactory(), quota=quota)
    try:
        result = await coordinator.resolve(
            [_item()],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=0 if path == "join" else 1,
        )
        if path == "cache":
            monkeypatch.setattr(coordinator, "_project_cached", AsyncMock(return_value=result))
        await coordinator.resolve(
            [_item()],
            context=_context(),
            provider=provider,
            user_id=USER_ID,
            wait_seconds=0,
        )
        assert quota.check_allowed.await_count == 2
        assert quota.reserve.await_count == 1
        quota.check_allowed.side_effect = TranslationDisabledForUser()
        disabled_user = uuid4()
        with pytest.raises(TranslationDisabledForUser):
            await coordinator.resolve(
                [_item()],
                context=_context(),
                provider=provider,
                user_id=disabled_user,
                wait_seconds=0,
            )
        quota.check_allowed.assert_awaited_with(disabled_user)
        assert quota.reserve.await_count == 1
    finally:
        provider.release.set()
        await coordinator.aclose(1)
