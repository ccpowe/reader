import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.settings import Settings
from app.translation.factory import TranslationProviderConfigurationError
from app.workers import main as worker_main


@pytest.mark.asyncio
async def test_translation_configuration_failure_does_not_stop_ingestion_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    ingestion_started = asyncio.Event()
    never = asyncio.Event()
    engine = AsyncMock()

    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "build_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(worker_main, "build_session_factory", lambda _engine: object())
    monkeypatch.setattr(
        worker_main,
        "build_translation_provider_routes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TranslationProviderConfigurationError("missing translation key")
        ),
    )

    async def idle_ingestion_loop(*_args, **_kwargs) -> None:
        ingestion_started.set()
        await never.wait()

    monkeypatch.setattr(worker_main, "_poll_loop", idle_ingestion_loop)

    task = asyncio.create_task(worker_main.run_forever(60))
    await asyncio.wait_for(ingestion_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_translation_cleanup_failures_never_escape_the_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restarted = asyncio.Event()
    build_count = 0

    class BrokenProvider:
        async def aclose(self) -> None:
            raise RuntimeError("provider close failed")

    def build_routes(*_args, **_kwargs):
        nonlocal build_count
        build_count += 1
        if build_count >= 2:
            restarted.set()
        return {"fingerprint": BrokenProvider()}

    engine = SimpleNamespace(dispose=AsyncMock(side_effect=RuntimeError("dispose failed")))
    settings = Settings(_env_file=None)
    settings.translation_idle_poll_seconds = 0
    monkeypatch.setattr(worker_main, "build_translation_provider_routes", build_routes)
    monkeypatch.setattr(worker_main, "build_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(worker_main, "build_session_factory", lambda _engine: object())

    async def fail_lane(*_args, **_kwargs) -> None:
        raise RuntimeError("lane failed")

    monkeypatch.setattr(worker_main, "_translation_loop", fail_lane)

    task = asyncio.create_task(worker_main._translation_service(settings))
    try:
        await asyncio.wait_for(restarted.wait(), timeout=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert engine.dispose.await_count >= 1


@pytest.mark.asyncio
async def test_translation_generation_cancels_sibling_lanes_before_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    settings.translation_idle_poll_seconds = 0
    generation = 0
    first_started: set[str] = set()
    first_stopped: set[str] = set()
    first_barrier = asyncio.Event()
    second_started = asyncio.Event()
    never = asyncio.Event()
    lane_tasks: set[asyncio.Task] = set()

    class Provider:
        def __init__(self, value: int) -> None:
            self.generation = value

        async def aclose(self) -> None:
            return None

    def build_routes(*_args, **_kwargs):
        nonlocal generation
        generation += 1
        return {"fingerprint": Provider(generation)}

    monkeypatch.setattr(worker_main, "build_translation_provider_routes", build_routes)
    monkeypatch.setattr(
        worker_main,
        "build_engine",
        lambda *_args, **_kwargs: SimpleNamespace(dispose=AsyncMock()),
    )
    monkeypatch.setattr(worker_main, "build_session_factory", lambda _engine: object())

    async def lane(executor, *_args, **_kwargs) -> None:
        task = asyncio.current_task()
        assert task is not None
        lane_tasks.add(task)
        provider = next(iter(executor._providers_by_fingerprint.values()))
        try:
            if provider.generation == 1:
                first_started.add(executor.lane_name)
                if len(first_started) == 2:
                    first_barrier.set()
                await first_barrier.wait()
                if executor.lane_name == "foreground":
                    raise RuntimeError("one lane crashed")
            else:
                second_started.set()
            await never.wait()
        finally:
            if provider.generation == 1:
                first_stopped.add(executor.lane_name)

    monkeypatch.setattr(worker_main, "_translation_loop", lane)

    supervisor = asyncio.create_task(worker_main._translation_service(settings))
    try:
        await asyncio.wait_for(second_started.wait(), timeout=1)
        assert first_stopped == {"foreground", "background"}
    finally:
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)
        for task in tuple(lane_tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(*lane_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_translation_engine_cleanup_is_cancelled_at_its_deadline() -> None:
    never = asyncio.Event()
    dispose_cancelled = asyncio.Event()

    class HangingEngine:
        async def dispose(self) -> None:
            try:
                await never.wait()
            finally:
                dispose_cancelled.set()

    async with asyncio.timeout(0.5):
        await worker_main._close_translation_runtime(
            {},
            HangingEngine(),
            timeout_seconds=0.01,
        )

    assert dispose_cancelled.is_set()


@pytest.mark.asyncio
async def test_translation_generation_restarts_after_provider_cleanup_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    settings.translation_idle_poll_seconds = 0
    # Use a short deterministic deadline below the production configuration
    # floor so the supervisor regression stays fast.
    settings.__dict__["translation_cleanup_timeout_seconds"] = 0.01
    generation = 0
    second_started = asyncio.Event()
    provider_cleanup_cancelled = asyncio.Event()
    never = asyncio.Event()

    class Provider:
        def __init__(self, value: int) -> None:
            self.generation = value

        async def aclose(self) -> None:
            if self.generation != 1:
                return
            try:
                await never.wait()
            finally:
                provider_cleanup_cancelled.set()

    def build_routes(*_args, **_kwargs):
        nonlocal generation
        generation += 1
        return {"fingerprint": Provider(generation)}

    monkeypatch.setattr(worker_main, "build_translation_provider_routes", build_routes)
    monkeypatch.setattr(
        worker_main,
        "build_engine",
        lambda *_args, **_kwargs: SimpleNamespace(dispose=AsyncMock()),
    )
    monkeypatch.setattr(worker_main, "build_session_factory", lambda _engine: object())

    async def lane(executor, *_args, **_kwargs) -> None:
        provider = next(iter(executor._providers_by_fingerprint.values()))
        if provider.generation == 1 and executor.lane_name == "foreground":
            raise RuntimeError("restart this generation")
        if provider.generation >= 2:
            second_started.set()
        await never.wait()

    monkeypatch.setattr(worker_main, "_translation_loop", lane)

    supervisor = asyncio.create_task(worker_main._translation_service(settings))
    try:
        await asyncio.wait_for(second_started.wait(), timeout=0.5)
        assert provider_cleanup_cancelled.is_set()
    finally:
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)
