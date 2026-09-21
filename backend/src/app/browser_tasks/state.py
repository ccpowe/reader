"""SQLite authority for isolated browser task lifecycle and leases."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
ACTIVE_STATES = ("creating", "running", "cleaning", "cleanup_failed")


class StateUnavailable(RuntimeError):
    """The controller state volume cannot currently provide authority."""


class StateConflict(RuntimeError):
    """A conditional state transition lost its fencing claim."""


class ControllerLock:
    """Single-controller fence backed by the shared local state volume."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._descriptor: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o660)
        try:
            os.fchmod(descriptor, 0o660)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            raise StateUnavailable("another browser task controller owns the state volume") from exc
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is not None:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    request_id: str
    request_fingerprint: str
    capability_nonce: str
    owner_job_id: str
    owner_hash: str
    purpose: str
    source_url: str
    expected_runtime_fingerprint: str
    state: str
    stage: str
    version: int
    created_at: float
    creation_deadline: float
    lease_expires_at: float
    hard_expires_at: float
    updated_at: float
    adapter_endpoint: str | None
    runtime_identity: dict | None
    resources_released: bool
    cleanup_reason: str | None
    last_error: str | None
    last_renew_request_id: str | None = None
    last_close_request_id: str | None = None


def _row_to_record(row: sqlite3.Row) -> TaskRecord:
    values = dict(row)
    identity = values.pop("runtime_identity_json")
    values["runtime_identity"] = json.loads(identity) if identity else None
    values["resources_released"] = bool(values["resources_released"])
    return TaskRecord(**values)


class TaskStateStore:
    """Short SQLite transactions; Docker work must happen outside this class."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=2000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        except sqlite3.Error as exc:
            raise StateUnavailable("browser task state is unavailable") from exc

    def prepare_directory(self) -> None:
        """Prepare the shared-GID directory before any lifecycle file is created."""
        self.path.parent.mkdir(parents=True, mode=0o2770, exist_ok=True)
        self._ensure_shared_permissions(self.path.parent, directory=True)

    def initialize(self) -> None:
        self.prepare_directory()
        connection = self._connect()
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, SCHEMA_VERSION):
                raise StateUnavailable(f"unsupported browser task state schema {version}")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS browser_tasks (
                    task_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    request_fingerprint TEXT NOT NULL,
                    capability_nonce TEXT NOT NULL,
                    owner_job_id TEXT NOT NULL,
                    owner_hash TEXT NOT NULL,
                    purpose TEXT NOT NULL CHECK (purpose IN ('explore', 'render')),
                    source_url TEXT NOT NULL,
                    expected_runtime_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    creation_deadline REAL NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    hard_expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    adapter_endpoint TEXT,
                    runtime_identity_json TEXT,
                    resources_released INTEGER NOT NULL DEFAULT 0,
                    cleanup_reason TEXT,
                    last_error TEXT,
                    last_renew_request_id TEXT,
                    last_close_request_id TEXT
                );
                CREATE INDEX IF NOT EXISTS browser_tasks_state_idx
                    ON browser_tasks(state, resources_released);
                PRAGMA user_version=1;
                COMMIT;
                """
            )
            self._ensure_shared_permissions(self.path, directory=False)
        except (OSError, sqlite3.Error, StateUnavailable) as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, StateUnavailable):
                raise
            raise StateUnavailable("could not initialize browser task state") from exc
        finally:
            connection.close()

    @staticmethod
    def _ensure_shared_permissions(path: Path, *, directory: bool) -> None:
        """Set owner-created paths or validate a shared-GID path owned by another UID."""
        metadata = path.stat()
        desired = 0o2770 if directory else 0o660
        if metadata.st_uid == os.geteuid():
            os.chmod(path, desired)
            return
        required_group = 0o070 if directory else 0o060
        if metadata.st_mode & required_group != required_group or metadata.st_mode & 0o007:
            raise StateUnavailable(
                f"browser task state {'directory' if directory else 'database'} "
                "must be private and writable by its shared group"
            )

    def get(self, task_id: str) -> TaskRecord | None:
        return self._one("SELECT * FROM browser_tasks WHERE task_id = ?", (task_id,))

    def get_by_request(self, request_id: str) -> TaskRecord | None:
        return self._one("SELECT * FROM browser_tasks WHERE request_id = ?", (request_id,))

    def list_records(self) -> list[TaskRecord]:
        connection = self._connect()
        try:
            return [
                _row_to_record(row) for row in connection.execute("SELECT * FROM browser_tasks")
            ]
        except sqlite3.Error as exc:
            raise StateUnavailable("could not list browser tasks") from exc
        finally:
            connection.close()

    def _one(self, query: str, parameters: tuple) -> TaskRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(query, parameters).fetchone()
            return _row_to_record(row) if row else None
        except sqlite3.Error as exc:
            raise StateUnavailable("could not read browser task state") from exc
        finally:
            connection.close()

    def reserve(
        self,
        record: TaskRecord,
        *,
        observed_tasks: dict[str, str],
        max_tasks: int,
        max_explore: int,
    ) -> tuple[TaskRecord, bool]:
        """Reserve capacity or return the durable idempotent task."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT * FROM browser_tasks WHERE request_id = ?", (record.request_id,)
            ).fetchone()
            if duplicate:
                existing = _row_to_record(duplicate)
                if existing.request_fingerprint != record.request_fingerprint:
                    raise StateConflict("request_conflict")
                connection.execute("COMMIT")
                return existing, False
            rows = connection.execute(
                "SELECT task_id, purpose, state, resources_released FROM browser_tasks"
            ).fetchall()
            counted = {
                row["task_id"]
                for row in rows
                if row["state"] in ACTIVE_STATES or not bool(row["resources_released"])
            }
            counted.update(observed_tasks)
            if len(counted) >= max_tasks:
                raise StateConflict("capacity_exhausted")
            explore_ids = {
                row["task_id"]
                for row in rows
                if row["purpose"] == "explore"
                and (row["state"] in ACTIVE_STATES or not bool(row["resources_released"]))
            }
            explore_ids.update(
                task_id for task_id, purpose in observed_tasks.items() if purpose != "render"
            )
            if record.purpose == "explore" and len(explore_ids) >= max_explore:
                raise StateConflict("capacity_exhausted")
            connection.execute(
                """
                INSERT INTO browser_tasks (
                    task_id, request_id, request_fingerprint, capability_nonce,
                    owner_job_id, owner_hash, purpose, source_url,
                    expected_runtime_fingerprint, state, stage, version,
                    created_at, creation_deadline, lease_expires_at, hard_expires_at,
                    updated_at, adapter_endpoint, runtime_identity_json, resources_released,
                    cleanup_reason, last_error, last_renew_request_id, last_close_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id,
                    record.request_id,
                    record.request_fingerprint,
                    record.capability_nonce,
                    record.owner_job_id,
                    record.owner_hash,
                    record.purpose,
                    record.source_url,
                    record.expected_runtime_fingerprint,
                    record.state,
                    record.stage,
                    record.version,
                    record.created_at,
                    record.creation_deadline,
                    record.lease_expires_at,
                    record.hard_expires_at,
                    record.updated_at,
                    record.adapter_endpoint,
                    json.dumps(record.runtime_identity, sort_keys=True)
                    if record.runtime_identity
                    else None,
                    int(record.resources_released),
                    record.cleanup_reason,
                    record.last_error,
                    record.last_renew_request_id,
                    record.last_close_request_id,
                ),
            )
            connection.execute("COMMIT")
            return record, True
        except StateConflict:
            connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StateUnavailable("could not reserve browser task") from exc
        finally:
            connection.close()

    def transition(
        self,
        task_id: str,
        *,
        expected_state: str,
        expected_version: int,
        state: str | None = None,
        stage: str | None = None,
        adapter_endpoint: str | None = None,
        runtime_identity: dict | None = None,
        cleanup_reason: str | None = None,
        last_error: str | None = None,
        clear_last_error: bool = False,
        last_close_request_id: str | None = None,
        resources_released: bool | None = None,
        lease_expires_at: float | None = None,
        updated_at: float | None = None,
    ) -> TaskRecord:
        assignments = ["version = version + 1"]
        parameters: list[object] = []
        values = {
            "state": state,
            "stage": stage,
            "adapter_endpoint": adapter_endpoint,
            "runtime_identity_json": json.dumps(runtime_identity, sort_keys=True)
            if runtime_identity is not None
            else None,
            "cleanup_reason": cleanup_reason,
            "last_error": last_error,
            "last_close_request_id": last_close_request_id,
            "resources_released": int(resources_released)
            if resources_released is not None
            else None,
            "lease_expires_at": lease_expires_at,
            "updated_at": updated_at,
        }
        for column, value in values.items():
            if value is not None:
                assignments.append(f"{column} = ?")
                parameters.append(value)
        if clear_last_error:
            assignments.append("last_error = NULL")
        parameters.extend((task_id, expected_state, expected_version))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"UPDATE browser_tasks SET {', '.join(assignments)} "
                "WHERE task_id = ? AND state = ? AND version = ?",
                parameters,
            )
            if cursor.rowcount != 1:
                raise StateConflict("state_claim_lost")
            row = connection.execute(
                "SELECT * FROM browser_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            connection.execute("COMMIT")
            return _row_to_record(row)
        except StateConflict:
            connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StateUnavailable("could not transition browser task") from exc
        finally:
            connection.close()

    def renew(self, task_id: str, *, request_id: str, now: float, lease_seconds: int) -> TaskRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM browser_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                raise StateConflict("task_not_found")
            record = _row_to_record(row)
            if record.last_renew_request_id == request_id:
                connection.execute("COMMIT")
                return record
            if record.state != "running":
                raise StateConflict("task_not_running")
            if now > record.lease_expires_at:
                raise StateConflict("lease_expired")
            lease_expires = min(now + lease_seconds, record.hard_expires_at)
            cursor = connection.execute(
                """
                UPDATE browser_tasks
                SET lease_expires_at = ?, updated_at = ?, last_renew_request_id = ?,
                    version = version + 1
                WHERE task_id = ? AND state = 'running' AND version = ?
                """,
                (lease_expires, now, request_id, task_id, record.version),
            )
            if cursor.rowcount != 1:
                raise StateConflict("state_claim_lost")
            updated = connection.execute(
                "SELECT * FROM browser_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            connection.execute("COMMIT")
            return _row_to_record(updated)
        except StateConflict:
            connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StateUnavailable("could not renew browser task") from exc
        finally:
            connection.close()

    def claim_cleaning(
        self,
        record: TaskRecord,
        *,
        reason: str,
        now: float | None = None,
        request_id: str | None = None,
    ) -> TaskRecord:
        return self.transition(
            record.task_id,
            expected_state=record.state,
            expected_version=record.version,
            state="cleaning",
            stage="cleaning",
            cleanup_reason=reason,
            last_close_request_id=request_id,
            resources_released=False,
            updated_at=now,
        )

    def finish_cleanup(
        self,
        record: TaskRecord,
        *,
        error: str | None = None,
        now: float | None = None,
    ) -> TaskRecord:
        if error is None:
            return self.transition(
                record.task_id,
                expected_state="cleaning",
                expected_version=record.version,
                state="closed",
                stage="closed",
                resources_released=True,
                clear_last_error=True,
                updated_at=now,
            )
        return self.transition(
            record.task_id,
            expected_state="cleaning",
            expected_version=record.version,
            state="cleanup_failed",
            stage="cleanup_failed",
            last_error=error,
            updated_at=now,
        )
