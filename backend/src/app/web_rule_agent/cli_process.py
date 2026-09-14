"""Own every CLI client and detached daemon in one task-local PID namespace."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from app.ingestion.browser_runtime import _binary_digest, _settle, _stop_process
from app.ingestion.models import SourceScanError

SUPPORTED_CLI_VERSION = "0.37.1"
CLI_POLICY_VERSION = "owned-cli-v1"
_SYSTEM_PATHS = ("/usr", "/bin", "/lib", "/lib64", "/etc/ld.so.cache")
_RESPONSE_LIMIT = 4 * 1024 * 1024  # Two bounded captures, including JSON escaping.


def cli_execution_identity(executable_path: str | None) -> dict:
    """Worker-only identity; API settings must not read the binary or runner."""
    result = {
        "cli_supported_version": SUPPORTED_CLI_VERSION,
        "policy_version": CLI_POLICY_VERSION,
        "launch_policy_sha256": hashlib.sha256(
            repr((_SYSTEM_PATHS, CLI_POLICY_VERSION)).encode()
        ).hexdigest(),
    }
    try:
        if not executable_path or not Path(executable_path).is_absolute():
            raise ValueError("Configure an absolute native CLI executable path")
        path = Path(executable_path).resolve(strict=True)
        stat = path.stat()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("The CLI binary is not executable")
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        result.update(binary_status="available", binary_sha256=_binary_digest(str(path), key))
    except (OSError, ValueError):
        result["binary_status"] = "unavailable"
    try:
        runner = Path(__file__).with_name("cli_runner.py").resolve(strict=True)
        stat = runner.stat()
        if not runner.is_file():
            raise ValueError("The CLI runner is not a regular file")
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        result.update(runner_status="available", runner_sha256=_binary_digest(str(runner), key))
    except (OSError, ValueError):
        result["runner_status"] = "unavailable"
    return result


def sandbox_command(executable: str, directory: str) -> list[str]:
    if sys.platform != "linux" or not (bwrap := shutil.which("bwrap")):
        raise SourceScanError("browser_unavailable", "The CLI requires Linux and bubblewrap.")
    if not Path("/usr/bin/python3").is_file():
        raise SourceScanError("browser_unavailable", "The CLI runner requires /usr/bin/python3.")
    args = [
        bwrap,
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--new-session",
    ]
    for path in _SYSTEM_PATHS:
        if Path(path).exists():
            args.extend(("--ro-bind", path, path))
    args.extend(
        (
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/home",
            "--dir",
            "/home/reader",
            "--bind",
            directory,
            "/run/reader-cli",
            "--ro-bind",
            str(Path(executable).resolve()),
            "/reader/agent-browser",
            "--ro-bind",
            str(Path(__file__).with_name("cli_runner.py")),
            "/reader/cli_runner.py",
            "--chdir",
            "/run/reader-cli",
            "/usr/bin/python3",
            "-u",
            "/reader/cli_runner.py",
        )
    )
    return args


class OwnedCLIProcess:
    def __init__(self, executable: str, *, expected_identity: dict | None = None):
        self.executable = executable
        self.expected_identity = expected_identity
        self.process = None
        self.directory = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def start(self) -> None:
        if self.process is not None:
            return
        identity = cli_execution_identity(self.executable)
        if identity["binary_status"] != "available":
            raise SourceScanError(
                "browser_unavailable", "Configure agent-browser 0.37.1 native binary."
            )
        if identity["runner_status"] != "available":
            raise SourceScanError("browser_unavailable", "The CLI runner file is unavailable.")
        if self.expected_identity is not None and identity != self.expected_identity:
            raise SourceScanError(
                "browser_configuration_changed", "CLI execution identity changed."
            )
        if self._closed:
            raise SourceScanError("browser_session_lost", "The owned CLI session already ended.")
        self.directory = TemporaryDirectory(prefix="reader-cli-")
        Path(self.directory.name, "config.json").write_text("{}\n")
        try:
            args = sandbox_command(self.executable, self.directory.name)
        except BaseException:
            self.directory.cleanup()
            self.directory = None
            raise
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                limit=_RESPONSE_LIMIT,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/home/reader",
                    "AGENT_BROWSER_SOCKET_DIR": "/run/reader-cli/sockets",
                    "AGENT_BROWSER_IDLE_TIMEOUT_MS": "0",
                    "AGENT_BROWSER_DEFAULT_TIMEOUT": "20000",
                },
            )
        )
        try:
            self.process = await asyncio.shield(spawning)
            version = await self.command(["/reader/agent-browser", "--version"], timeout=5)
            if version.get("exit_code") != 0 or version.get("stdout", "").strip() != (
                "agent-browser " + SUPPORTED_CLI_VERSION
            ):
                raise SourceScanError("browser_unavailable", "CLI version must be exactly 0.37.1.")
        except BaseException:
            try:
                if self.process is None:
                    self.process = await _settle(spawning, propagate_cancel=False)
            finally:
                await _settle(asyncio.create_task(self.aclose()), propagate_cancel=False)
            raise

    async def command(self, argv: list[str], *, timeout: float = 28) -> dict:
        async with self._lock:
            if self._closed or self.process is None or self.process.returncode is not None:
                raise SourceScanError(
                    "browser_session_lost", "The owned CLI session is unavailable."
                )
            try:
                async with asyncio.timeout(timeout + 3):
                    self.process.stdin.write(
                        (
                            json.dumps({"argv": argv, "timeout": timeout}, ensure_ascii=False)
                            + "\n"
                        ).encode()
                    )
                    await self.process.stdin.drain()
                    line = await self.process.stdout.readline()
                    if not line:
                        raise ValueError("Owned CLI runner exited without a response")
                    result = json.loads(line)
                    if result.get("runner_error"):
                        raise ValueError(result["runner_error"])
                    return result
            except (OSError, ValueError, TimeoutError) as exc:
                raise SourceScanError("browser_session_lost", str(exc)) from exc

    async def aclose(self) -> None:
        self._closed = True
        try:
            if self.process is not None:
                await _settle(asyncio.create_task(_stop_process(self.process)))
        finally:
            if self.directory is not None:
                self.directory.cleanup()
                self.directory = None
