"""Task-owned Lightpanda process behind Reader's controlled transport.

This module never accepts model commands or a remote CDP endpoint. Linux
namespaces protect the filesystem; the shared host network is separately
restricted by native engine blocks and Reader's request fulfillment policy.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import signal
import socket
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from .models import SourceScanError

SUPPORTED_LIGHTPANDA_VERSION = "0.4.0"
BROWSER_POLICY_VERSION = "reader-controlled-browser-v3"
_START_TIMEOUT = 10.0
_STOP_TIMEOUT = 3.0
_READ_ONLY_PATHS = (
    "/usr",
    "/bin",
    "/lib",
    "/lib64",
    "/etc/ssl",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/etc/nsswitch.conf",
    "/etc/ld.so.cache",
)
_ENGINE_FLAGS = (
    "--http-proxy",
    "http://127.0.0.1:9",
    "--block-private-networks",
    "--block-cidrs",
    "0.0.0.0/0",
    "--block-cidrs",
    "::/0",
    "--v8-max-heap-mb",
    "64",
    "--watchdog-ms",
    "5000",
    "--http-timeout",
    "1000",
    "--http-connect-timeout",
    "1000",
    "--http-max-concurrent",
    "2",
    "--ws-max-concurrent",
    "0",
    # Fetch.fulfillRequest base64-encodes the response. Cover Reader's existing
    # 20 MiB HTTP maximum plus JSON framing; retain a finite CDP message bound.
    "--cdp-max-message-size",
    str(32 * 1024 * 1024),
    "--disable-metrics",
)


@lru_cache(maxsize=8)
def _binary_digest(path: str, _identity: tuple) -> str:
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def browser_execution_identity(engine: str, executable_path: str | None) -> dict:
    """Worker-only runtime evidence; API configuration projection must not call it."""
    result = {"engine": engine, "policy_version": BROWSER_POLICY_VERSION}
    if engine != "lightpanda":
        return result
    result["lightpanda_supported_version"] = SUPPORTED_LIGHTPANDA_VERSION
    result["launch_policy_sha256"] = hashlib.sha256(
        repr((_READ_ONLY_PATHS, _ENGINE_FLAGS, BROWSER_POLICY_VERSION)).encode()
    ).hexdigest()
    try:
        if not executable_path or not Path(executable_path).is_absolute():
            raise ValueError("An absolute executable path is required")
        path = Path(executable_path).resolve(strict=True)
        stat = path.stat()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("The configured binary is not executable")
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        result.update(binary_status="available", binary_sha256=_binary_digest(str(path), identity))
    except (OSError, ValueError):
        result["binary_status"] = "unavailable"
    return result


def _sandbox_command(executable_path: str) -> list[str]:
    if sys.platform != "linux" or not (bwrap := shutil.which("bwrap")):
        raise SourceScanError(
            "browser_unavailable", "Lightpanda requires Linux and working bubblewrap namespaces."
        )
    args = [
        bwrap,
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--new-session",
    ]
    for path in _READ_ONLY_PATHS:
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
            "--ro-bind",
            executable_path,
            "/reader/lightpanda",
            "--chdir",
            "/tmp",
            "/reader/lightpanda",
        )
    )
    return args


async def _settle(task: asyncio.Task, *, propagate_cancel: bool = True):
    # Cleanup must finish even if a lease deadline cancels an already cancelled
    # operation. The caller still propagates its original cancellation.
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            continue
    result = task.result()
    if cancelled and propagate_cancel:
        raise asyncio.CancelledError
    return result


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), _STOP_TIMEOUT)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # A process namespace dies with its init; bubblewrap also sets
        # parent-death handling. Never leave the owned wrapper running.
        try:
            await asyncio.wait_for(process.wait(), _STOP_TIMEOUT)
        except TimeoutError as exc:
            raise SourceScanError(
                "browser_cleanup_failed", "The owned browser did not exit after termination."
            ) from exc


@dataclass
class OwnedBrowserEndpoint:
    url: str
    process: asyncio.subprocess.Process
    _stop_task: asyncio.Task | None = field(default=None, init=False, repr=False)

    async def stop(self) -> None:
        """Idempotent host-owned termination, independent of CDP responsiveness."""
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(_stop_process(self.process))
        await _settle(self._stop_task)


async def _spawn(args: list[str], directory: str, *, capture: bool = False):
    task = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *args,
            cwd=directory,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/home/reader",
                "LIGHTPANDA_DISABLE_TELEMETRY": "true",
            },
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        process = await _settle(task, propagate_cancel=False)
        await _settle(asyncio.create_task(_stop_process(process)), propagate_cancel=False)
        raise


async def _check_version(args: list[str], directory: str) -> None:
    process = await _spawn([*args, "version"], directory, capture=True)
    try:
        async with asyncio.timeout(3):
            output = await process.stdout.read(128)
            await process.wait()
        if process.returncode != 0:
            raise SourceScanError(
                "browser_unavailable", "Lightpanda could not start inside its required sandbox."
            )
        if output.strip() != SUPPORTED_LIGHTPANDA_VERSION.encode():
            raise SourceScanError(
                "browser_unavailable", "Reader supports the pinned Lightpanda 0.4.0 binary."
            )
    finally:
        await _settle(asyncio.create_task(_stop_process(process)))


async def _wait_ready(process: asyncio.subprocess.Process, port: int) -> None:
    # This host-controlled loopback address is exclusively a process control
    # channel. It is never derived from page content or model arguments.
    async with httpx.AsyncClient(trust_env=False) as client:
        while process.returncode is None:
            try:
                async with client.stream(
                    "GET", f"http://127.0.0.1:{port}/json/version", timeout=0.3
                ) as response:
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 8192:
                            raise SourceScanError(
                                "browser_unavailable", "Invalid browser endpoint."
                            )
                    if response.status_code == 200:
                        return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise SourceScanError("browser_unavailable", "The isolated browser exited during startup.")


@asynccontextmanager
async def browser_endpoint(
    engine: str,
    executable_path: str | None = None,
    *,
    expected_identity: dict | None = None,
):
    """Yield an owned Lightpanda endpoint; never start a fallback browser."""
    if engine != "lightpanda":
        raise SourceScanError("browser_unavailable", "Dynamic Web execution requires Lightpanda.")
    identity = browser_execution_identity(engine, executable_path)
    if identity.get("binary_status") != "available":
        raise SourceScanError(
            "browser_unavailable", "Configure an executable Lightpanda 0.4.0 binary."
        )
    if expected_identity is not None and expected_identity != identity:
        raise SourceScanError("browser_configuration_changed", "Browser runtime identity changed.")
    args = _sandbox_command(str(Path(executable_path).resolve()))
    process = None
    with TemporaryDirectory(prefix="reader-browser-") as directory:
        try:
            async with asyncio.timeout(_START_TIMEOUT):
                await _check_version(args, directory)
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                process = await _spawn(
                    [*args, "serve", "--host", "127.0.0.1", "--port", str(port), *_ENGINE_FLAGS],
                    directory,
                )
                await _wait_ready(process, port)
        except (OSError, TimeoutError) as exc:
            if process is not None:
                await _settle(asyncio.create_task(_stop_process(process)))
            raise SourceScanError(
                "browser_unavailable", "The configured isolated browser could not start."
            ) from exc
        except BaseException:
            if process is not None:
                await _settle(asyncio.create_task(_stop_process(process)))
            raise
        owned = OwnedBrowserEndpoint(f"ws://127.0.0.1:{port}", process)
        try:
            yield owned
        finally:
            await owned.stop()
