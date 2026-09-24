"""Offline coverage of credential ownership, rotation, recovery and process races."""

import asyncio
import base64
import json
import multiprocessing
import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from filelock import FileLock

from app.admin.cli import main
from app.core.settings import Settings
from app.llm import codex_auth
from app.llm.codex_auth import (
    CLIENT_ID,
    TOKEN_URL,
    CodexAuthenticationError,
    CodexRefreshError,
    import_subscription_auth,
    subscription_auth_status,
    subscription_headers,
    subscription_unavailable_reason,
)
from app.llm.codex_subscription import CodexSubscriptionModel


def token(expiry, account="test-account"):
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"exp": expiry, "https://api.openai.com/auth": {"chatgpt_account_id": account}}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"synthetic.{encoded}.signature"


def login(expiry=None):
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": token(expiry or time.time() + 3600),
            "refresh_token": "synthetic-refresh-original",
            "id_token": "synthetic-id-original",
        },
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Unexpected live OAuth request")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    path = tmp_path / "owned" / "auth.json"
    import_subscription_auth(path, json.dumps(login(time.time() - 60)).encode())
    return path


def mock_refresh(monkeypatch, *, status=200, payload=None, callback=None):
    requests = []
    payload = (
        payload
        if payload is not None
        else {
            "access_token": token(time.time() + 7200),
            "refresh_token": "synthetic-refresh-rotated",
            "id_token": "synthetic-id-rotated",
        }
    )

    def send(_client, request, **kwargs):
        requests.append(request)
        if callback:
            callback(request)
        return httpx.Response(status, request=request, json=payload)

    monkeypatch.setattr(httpx.Client, "send", send)
    return requests, payload


def test_refresh_updates_complete_pair_and_atomic_private_store(store, monkeypatch):
    assert subscription_unavailable_reason(store) is None
    requests, result = mock_refresh(monkeypatch)
    headers = subscription_headers(store)
    assert headers["Authorization"] == "Bearer " + result["access_token"]
    assert str(requests[0].url) == TOKEN_URL
    from urllib.parse import parse_qs

    assert parse_qs(requests[0].content.decode()) == {
        "grant_type": ["refresh_token"],
        "client_id": [CLIENT_ID],
        "refresh_token": ["synthetic-refresh-original"],
    }
    written = json.loads(store.read_text())
    assert written["tokens"] == result
    assert "reader_refresh" not in written
    assert written["last_refresh"]
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.parent.stat().st_mode) == 0o700
    assert not list(store.parent.glob(".codex-auth-*"))
    subscription_headers(store)
    assert len(requests) == 1


def test_refresh_preserves_optional_omitted_tokens(store, monkeypatch):
    mock_refresh(monkeypatch, payload={"access_token": token(time.time() + 3600)})
    subscription_headers(store)
    auth = json.loads(store.read_text())
    assert auth["tokens"]["refresh_token"] == "synthetic-refresh-original"
    assert auth["tokens"]["id_token"] == "synthetic-id-original"


@pytest.mark.parametrize("status", [400, 401, 403])
def test_terminal_refresh_is_persisted_and_not_replayed(store, monkeypatch, status):
    requests, _ = mock_refresh(
        monkeypatch,
        status=status,
        payload={"error": "invalid_grant", "message": "synthetic-refresh-original"},
    )
    for _ in range(2):
        with pytest.raises(CodexAuthenticationError, match="reauth_required") as error:
            subscription_headers(store)
        assert "synthetic" not in str(error.value)
    assert len(requests) == 1
    assert subscription_unavailable_reason(store) == "codex_subscription_reauth_required"
    with pytest.raises(CodexAuthenticationError, match="requires_fresh_login"):
        import_subscription_auth(store, json.dumps(login()).encode(), replace=True)
    renewed = login()
    renewed["tokens"]["refresh_token"] = "fresh-login-refresh"
    import_subscription_auth(store, json.dumps(renewed).encode(), replace=True)
    assert subscription_unavailable_reason(store) is None
    subscription_headers(store)
    assert len(requests) == 1


@pytest.mark.parametrize("status", [429, 503])
def test_transient_failure_preserves_tokens_and_backs_off(store, monkeypatch, status):
    requests, _ = mock_refresh(monkeypatch, status=status)
    for _ in range(2):
        with pytest.raises(CodexRefreshError):
            subscription_headers(store)
    assert len(requests) == 1
    assert json.loads(store.read_text())["tokens"]["refresh_token"] == "synthetic-refresh-original"
    assert subscription_unavailable_reason(store) is None
    auth = json.loads(store.read_text())
    auth["reader_refresh"]["retry_at"] = 0
    store.write_text(json.dumps(auth))
    requests, result = mock_refresh(monkeypatch)
    assert subscription_headers(store)["Authorization"] == "Bearer " + result["access_token"]


@pytest.mark.parametrize(
    "exception,expected",
    [
        (httpx.ConnectError("synthetic-secret"), CodexRefreshError),
        (httpx.ReadTimeout("synthetic-secret"), CodexAuthenticationError),
    ],
)
def test_transport_errors_do_not_expose_tokens_or_replay_uncertain_rotation(
    store, monkeypatch, exception, expected
):
    def send(*args, **kwargs):
        raise exception

    monkeypatch.setattr(httpx.Client, "send", send)
    with pytest.raises(expected) as error:
        subscription_headers(store)
    assert "synthetic-secret" not in str(error.value)
    assert error.value.__suppress_context__
    state = json.loads(store.read_text())["reader_refresh"]["status"]
    assert state == ("retry" if expected is CodexRefreshError else "uncertain")


def test_interrupted_exchange_is_not_replayed_after_restart(store):
    auth = json.loads(store.read_text())
    auth["reader_refresh"] = {"status": "pending"}
    store.write_text(json.dumps(auth))
    with pytest.raises(CodexAuthenticationError, match="reauth_required"):
        subscription_headers(store)


def test_failed_atomic_write_preserves_previous_file_and_pending_marker(store, monkeypatch):
    mock_refresh(monkeypatch)
    real_replace = os.replace
    calls = 0

    def replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("write failed")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(CodexAuthenticationError, match="not_writable"):
        subscription_headers(store)
    auth = json.loads(store.read_text())
    assert auth["tokens"]["refresh_token"] == "synthetic-refresh-original"
    assert auth["reader_refresh"]["status"] == "pending"
    assert not list(store.parent.glob(".codex-auth-*"))
    with pytest.raises(CodexAuthenticationError, match="reauth_required"):
        subscription_headers(store)


def test_import_refuses_accidental_overwrite_and_unmanaged_runtime_login(store, tmp_path):
    with pytest.raises(CodexAuthenticationError, match="already_exists"):
        import_subscription_auth(store, json.dumps(login()).encode())
    external = tmp_path / "external.json"
    external.write_text(json.dumps(login()))
    external.chmod(0o600)
    assert subscription_unavailable_reason(external) == "unmanaged_codex_subscription_auth"
    invalid = login()
    invalid["tokens"].pop("refresh_token")
    with pytest.raises(CodexAuthenticationError):
        import_subscription_auth(store, json.dumps(invalid).encode(), replace=True)
    assert subscription_unavailable_reason(store) is None


def test_thread_contention_rotates_once(store, monkeypatch):
    requests, _ = mock_refresh(monkeypatch, callback=lambda _: time.sleep(0.1))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(subscription_headers, [store] * 4))
    assert len(requests) == 1
    assert all(result == results[0] for result in results)


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="fork test")
def test_api_and_worker_processes_share_one_rotation(store, monkeypatch):
    context = multiprocessing.get_context("fork")
    count = context.Value("i", 0)

    def observe(_request):
        with count.get_lock():
            count.value += 1
        time.sleep(0.1)

    mock_refresh(monkeypatch, callback=observe)
    queue = context.Queue()

    def run():
        try:
            subscription_headers(store)
            queue.put("ok")
        except Exception as exc:
            queue.put(type(exc).__name__)

    processes = [context.Process(target=run) for _ in range(4)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0
        assert [queue.get(timeout=1) for _ in processes] == ["ok"] * 4
        assert count.value == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        queue.close()


def test_lock_timeout_does_not_call_oauth(store, monkeypatch):
    monkeypatch.setattr(codex_auth, "LOCK_TIMEOUT_SECONDS", 0.01)
    with FileLock(str(store) + ".lock"):
        with pytest.raises(CodexRefreshError):
            subscription_headers(store)


async def test_cancelled_caller_still_persists_rotation_before_unlock(store, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def exchange(_):
        started.set()
        assert release.wait(timeout=3)

    requests, _ = mock_refresh(monkeypatch, callback=exchange)
    model = CodexSubscriptionModel(auth_file=store)
    task = asyncio.create_task(model.ainvoke("Hello"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    await asyncio.to_thread(subscription_headers, store)
    assert len(requests) == 1
    assert "reader_refresh" not in json.loads(store.read_text())


async def test_401_refreshes_and_retries_only_once(store, monkeypatch):
    import_subscription_auth(store, json.dumps(login()).encode(), replace=True)
    refresh_requests, _ = mock_refresh(monkeypatch)
    inference = []

    async def send(_client, request, **kwargs):
        inference.append(request.headers["authorization"])
        return httpx.Response(401, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    from app.llm.codex_subscription import CodexSubscriptionHTTPError

    with pytest.raises(CodexSubscriptionHTTPError) as error:
        await CodexSubscriptionModel(auth_file=store).ainvoke("Hello")
    assert error.value.status_code == 401
    assert len(refresh_requests) == 1
    assert len(inference) == 2 and inference[0] != inference[1]


def test_late_401_adopts_peer_rotation_without_refreshing_again(store, monkeypatch):
    original = json.loads(store.read_text())["tokens"]["access_token"]
    requests, _ = mock_refresh(monkeypatch)
    headers = subscription_headers(store)
    assert subscription_headers(store, rejected_access_token=original) == headers
    assert len(requests) == 1


def test_admin_import_status_without_database_or_secret_output(tmp_path, monkeypatch, capsys):
    source = tmp_path / "login.json"
    source.write_text(json.dumps(login()))
    destination = tmp_path / "owned" / "auth.json"
    settings = Settings(_env_file=None, codex_subscription_auth_file=destination)
    monkeypatch.setattr("app.admin.cli.get_settings", lambda: settings)
    monkeypatch.setattr("app.admin.cli.build_engine", lambda *_: pytest.fail("database used"))
    assert main(["codex-auth", "import", "--source", str(source)]) == 0
    assert main(["codex-auth", "status"]) == 0
    output = capsys.readouterr().out
    assert "synthetic" not in output
    assert '"refreshable": true' in output
    assert subscription_auth_status(destination)["available"]


def test_proactive_transient_failure_uses_still_valid_access_token(store, monkeypatch):
    auth = login(time.time() + 60)
    import_subscription_auth(store, json.dumps(auth).encode(), replace=True)
    requests, _ = mock_refresh(monkeypatch, status=503)
    for _ in range(2):
        assert (
            subscription_headers(store)["Authorization"]
            == "Bearer " + auth["tokens"]["access_token"]
        )
    assert len(requests) == 1
    with pytest.raises(CodexRefreshError):
        subscription_headers(store, rejected_access_token=auth["tokens"]["access_token"])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"access_token": "broken"},
        {"access_token": token(time.time() + 3600, "different-account")},
    ],
)
def test_invalid_refresh_response_requires_reauthorization(store, monkeypatch, payload):
    mock_refresh(monkeypatch, payload=payload)
    with pytest.raises(CodexAuthenticationError, match="reauth_required"):
        subscription_headers(store)
    assert subscription_unavailable_reason(store) == "codex_subscription_reauth_required"


def test_oversized_refresh_reply_is_closed_and_not_replayed(store, monkeypatch):
    closed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * (codex_auth.MAX_AUTH_BYTES + 1)

        def close(self):
            closed.append(True)

    def send(_client, request, **kwargs):
        return httpx.Response(200, request=request, stream=Stream())

    monkeypatch.setattr(httpx.Client, "send", send)
    with pytest.raises(CodexAuthenticationError, match="reauth_required"):
        subscription_headers(store)
    assert closed == [True]
    assert subscription_unavailable_reason(store) == "codex_subscription_reauth_required"
