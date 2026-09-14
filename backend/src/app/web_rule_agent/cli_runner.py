"""Small stdlib-only argv runner inside the task's owned PID namespace.

The Reader parent supplies commands over stdin. Keeping this process alive keeps
agent-browser's detached daemon in the same namespace between commands. It never
loads Reader configuration, imports application code, or invokes a shell.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time

CAPTURE_BYTES = 256 * 1024
MAX_REQUEST_BYTES = 96 * 1024


def execute(argv: list[str], timeout: float) -> dict:
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        with selectors.DefaultSelector() as selector:
            for name in buffers:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    name = key.data
                    totals[name] += len(chunk)
                    buffers[name].extend(chunk[: max(0, CAPTURE_BYTES - len(buffers[name]))])
            if timed_out:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()
    return {
        "exit_code": process.returncode,
        **{name: value.decode("utf-8", errors="replace") for name, value in buffers.items()},
        "capture_truncated": [name for name in buffers if totals[name] > CAPTURE_BYTES],
        "timed_out": timed_out,
    }


def main() -> None:
    for line in iter(lambda: sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1), b""):
        if len(line) > MAX_REQUEST_BYTES or not line.endswith(b"\n"):
            raise ValueError("Owned CLI request exceeded its transport limit")
        request = json.loads(line)
        try:
            result = execute(request["argv"], request["timeout"])
        except Exception as exc:
            result = {"runner_error": type(exc).__name__ + ": " + str(exc)}
        print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
