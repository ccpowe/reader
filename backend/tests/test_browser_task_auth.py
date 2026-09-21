import base64
import os
from pathlib import Path

import pytest

from app.browser_tasks.auth import (
    AuthenticationError,
    capability_hash,
    derive_task_capability,
    generate_capability_nonce,
    load_or_create_capability_key,
    remove_stale_capability_key_temps,
    verify_bearer,
    verify_task_capability,
)


def test_capability_key_and_task_capability_are_stable_without_storing_capability(tmp_path: Path):
    path = tmp_path / "capability.key"
    first_key = load_or_create_capability_key(path)
    second_key = load_or_create_capability_key(path)
    nonce = generate_capability_nonce()

    first = derive_task_capability(
        first_key,
        task_id="task",
        request_id="request",
        owner_job_id="owner",
        capability_nonce=nonce,
    )
    second = derive_task_capability(
        second_key,
        task_id="task",
        request_id="request",
        owner_job_id="owner",
        capability_nonce=nonce,
    )

    assert first_key == second_key
    assert first == second
    assert len(base64.urlsafe_b64decode(first + "=")) == 32
    assert len(capability_hash(first)) == 64
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_control_and_task_credentials_fail_closed():
    verify_bearer("Bearer stable-secret-value", b"stable-secret-value")
    verify_task_capability("task-secret", "task-secret")

    with pytest.raises(AuthenticationError):
        verify_bearer("Bearer wrong", b"stable-secret-value")
    with pytest.raises(AuthenticationError):
        verify_bearer(None, b"stable-secret-value")
    with pytest.raises(AuthenticationError):
        verify_task_capability("wrong", "task-secret")


def test_capability_key_rejects_group_or_world_access(tmp_path: Path):
    path = tmp_path / "capability.key"
    path.write_bytes(b"x" * 32)
    path.chmod(0o640)

    with pytest.raises(AuthenticationError, match="controller-only"):
        load_or_create_capability_key(path)


def test_stale_controller_owned_key_temp_is_removed(tmp_path: Path):
    stale = tmp_path / ".capability-key-crash"
    stale.write_bytes(b"x" * 32)
    stale.chmod(0o600)

    remove_stale_capability_key_temps(tmp_path)

    assert not stale.exists()
