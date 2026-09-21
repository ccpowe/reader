import os
import time
from pathlib import Path

import pytest

from app.browser_tasks.auth import load_or_create_capability_key
from app.browser_tasks.state import (
    ControllerLock,
    StateConflict,
    StateUnavailable,
    TaskRecord,
    TaskStateStore,
)


def record(*, task_id: str = "task-1", request_id: str = "request-1", purpose: str = "explore"):
    now = time.time()
    return TaskRecord(
        task_id=task_id,
        request_id=request_id,
        request_fingerprint=f"fingerprint-{request_id}",
        capability_nonce="A" * 43,
        owner_job_id="job-1",
        owner_hash="a" * 64,
        purpose=purpose,
        source_url="https://example.com/",
        expected_runtime_fingerprint="b" * 64,
        state="creating",
        stage="reserved",
        version=0,
        created_at=now,
        creation_deadline=now + 60,
        lease_expires_at=now + 90,
        hard_expires_at=now + 900,
        updated_at=now,
        adapter_endpoint=None,
        runtime_identity={"protocol_version": "1"},
        resources_released=False,
        cleanup_reason=None,
        last_error=None,
    )


def store(tmp_path: Path) -> TaskStateStore:
    value = TaskStateStore(tmp_path / "state.sqlite3")
    value.initialize()
    return value


def test_reservation_is_idempotent_and_counts_durable_and_observed_resources(tmp_path: Path):
    value = store(tmp_path)
    first, created = value.reserve(record(), observed_tasks={}, max_tasks=2, max_explore=1)
    duplicate, duplicate_created = value.reserve(
        record(), observed_tasks={}, max_tasks=2, max_explore=1
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate == first

    with pytest.raises(StateConflict, match="request_conflict"):
        value.reserve(
            TaskRecord(**{**record().__dict__, "request_fingerprint": "changed"}),
            observed_tasks={},
            max_tasks=2,
            max_explore=1,
        )
    with pytest.raises(StateConflict, match="capacity_exhausted"):
        value.reserve(
            record(task_id="task-2", request_id="request-2", purpose="render"),
            observed_tasks={"orphan": "render"},
            max_tasks=2,
            max_explore=1,
        )


def test_unknown_observed_task_conservatively_consumes_explore_slot(tmp_path: Path):
    value = store(tmp_path)

    with pytest.raises(StateConflict, match="capacity_exhausted"):
        value.reserve(
            record(),
            observed_tasks={"malformed-task": "unknown"},
            max_tasks=2,
            max_explore=1,
        )


def test_renew_wins_fence_and_cleaning_rejects_further_renewal(tmp_path: Path):
    value = store(tmp_path)
    initial, _ = value.reserve(record(), observed_tasks={}, max_tasks=2, max_explore=1)
    running = value.transition(
        initial.task_id,
        expected_state="creating",
        expected_version=initial.version,
        state="running",
        stage="running",
        adapter_endpoint="http://adapter:8092",
        updated_at=time.time(),
    )
    renewed = value.renew(running.task_id, request_id="renew-1", now=time.time(), lease_seconds=90)
    duplicate = value.renew(
        running.task_id, request_id="renew-1", now=time.time() + 10, lease_seconds=90
    )
    assert duplicate == renewed

    with pytest.raises(StateConflict, match="state_claim_lost"):
        value.claim_cleaning(running, reason="expired", now=time.time())

    cleaning = value.claim_cleaning(renewed, reason="close", now=time.time())
    with pytest.raises(StateConflict, match="task_not_running"):
        value.renew(cleaning.task_id, request_id="renew-2", now=time.time(), lease_seconds=90)

    closed = value.finish_cleanup(cleaning, now=time.time())
    assert closed.state == "closed"
    assert closed.resources_released is True


def test_state_volume_lock_rejects_a_second_controller(tmp_path: Path):
    first = ControllerLock(tmp_path / "controller.lock")
    second = ControllerLock(tmp_path / "controller.lock")
    first.acquire()
    try:
        with pytest.raises(StateUnavailable):
            second.acquire()
    finally:
        first.release()


def test_prepare_directory_precedes_lock_and_key_group_inheritance(tmp_path: Path):
    supplemental_groups = [group for group in os.getgroups() if group != os.getgid()]
    if not supplemental_groups:
        pytest.skip("supplemental group required to verify setgid inheritance")
    shared_gid = supplemental_groups[0]
    os.chown(tmp_path, -1, shared_gid)
    value = TaskStateStore(tmp_path / "tasks.sqlite3")

    value.prepare_directory()
    assert tmp_path.stat().st_mode & 0o7777 == 0o2770
    assert not value.path.exists()

    lock_path = tmp_path / "controller.lock"
    lock = ControllerLock(lock_path)
    lock.acquire()
    try:
        key_path = tmp_path / "capability.key"
        load_or_create_capability_key(key_path)
    finally:
        lock.release()
    value.initialize()

    assert lock_path.stat().st_gid == shared_gid
    assert key_path.stat().st_gid == shared_gid
    assert value.path.stat().st_gid == shared_gid
    assert lock_path.stat().st_mode & 0o777 == 0o660
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert value.path.stat().st_mode & 0o777 == 0o660


def test_non_owner_reaper_validates_shared_modes_without_chmod(tmp_path: Path, monkeypatch):
    value = store(tmp_path)
    owner = value.path.stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)

    def forbidden_chmod(*_args, **_kwargs):
        raise AssertionError("a different UID must not chmod manager-owned state")

    monkeypatch.setattr(os, "chmod", forbidden_chmod)
    value.initialize()


def test_non_owner_reaper_rejects_state_without_shared_group_write(tmp_path: Path, monkeypatch):
    value = store(tmp_path)
    value.path.chmod(0o640)
    owner = value.path.stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)

    with pytest.raises(StateUnavailable, match="writable by its shared group"):
        value.initialize()
