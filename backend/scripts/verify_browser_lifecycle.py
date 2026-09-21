#!/usr/bin/env python3
"""Opt-in real-Docker smoke for the private browser lifecycle controller.

The script reuses existing images and networks, generates a unique deployment
label, and removes only task resources carrying that label.  It never creates
or removes shared networks and never pulls or builds images.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx

from app.browser_tasks.auth import generate_capability_nonce
from app.browser_tasks.controller import (
    LABEL_PREFIX,
    POLICY_SCHEMA,
    BrowserTaskConfig,
    cleanup_task_resources,
)
from app.browser_tasks.docker_runtime import DockerEngine
from app.browser_tasks.protocol import RuntimeIdentity
from app.browser_tasks.reaper import BrowserTaskReaper, ReaperConfig
from app.browser_tasks.state import StateUnavailable, TaskRecord, TaskStateStore

CONFIRMATION = "run-isolated-browser-lifecycle-smoke"


class UnavailableState:
    def list_records(self):
        raise StateUnavailable("injected database outage")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", required=True, choices=(CONFIRMATION,))
    parser.add_argument("--browser-image", required=True)
    parser.add_argument("--adapter-image", required=True)
    parser.add_argument("--data-network", required=True)
    parser.add_argument("--egress-network", required=True)
    parser.add_argument("--docker-socket", default="/var/run/docker.sock")
    return parser.parse_args()


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def image_policy(args: argparse.Namespace) -> tuple[str, str, str]:
    engine = DockerEngine(args.docker_socket)
    try:
        await engine.initialize()
        browser, adapter = await asyncio.gather(
            engine.inspect_image(args.browser_image),
            engine.inspect_image(args.adapter_image),
        )
    finally:
        await engine.aclose()
    prefix = "io.reader.browser-runtime"
    try:
        return (
            browser.labels[f"{prefix}.lightpanda.sha256"],
            browser.labels[f"{prefix}.agent-browser.sha256"],
            adapter.labels[f"{prefix}.adapter-policy-version"],
        )
    except KeyError as exc:
        raise RuntimeError(f"candidate image is missing identity label: {exc}") from exc


def manager_environment(
    args: argparse.Namespace,
    *,
    state_dir: Path,
    token_file: Path,
    deployment_id: str,
    port: int,
    lightpanda_sha: str,
    agent_browser_sha: str,
    policy_version: str,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "APP_BROWSER_TASK_STATE_DIR": str(state_dir),
            "APP_BROWSER_TASK_DEPLOYMENT_ID": deployment_id,
            "APP_BROWSER_TASK_BROWSER_IMAGE": args.browser_image,
            "APP_BROWSER_TASK_ADAPTER_IMAGE": args.adapter_image,
            "APP_BROWSER_TASK_DATA_NETWORK": args.data_network,
            "APP_BROWSER_TASK_EGRESS_NETWORK": args.egress_network,
            "APP_BROWSER_TASK_POLICY_VERSION": policy_version,
            "APP_BROWSER_TASK_LIGHTPANDA_SHA256": lightpanda_sha,
            "APP_BROWSER_TASK_AGENT_BROWSER_SHA256": agent_browser_sha,
            "APP_BROWSER_TASK_DOCKER_SOCKET": args.docker_socket,
            "APP_BROWSER_MANAGER_TOKEN_FILE": str(token_file),
            "APP_BROWSER_MANAGER_HOST": "127.0.0.1",
            "APP_BROWSER_MANAGER_PORT": str(port),
            "APP_BROWSER_TASK_RENDER_HARD_TTL_SECONDS": "60",
        }
    )
    return env


@contextmanager
def manager_process(env: dict[str, str]):
    process = subprocess.Popen(
        [sys.executable, "-c", "from app.browser_tasks.manager_app import main; main()"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=10)


def wait_ready(client: httpx.Client, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"manager exited during startup: {process.returncode}")
        try:
            if client.get("/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("manager did not become ready")


async def run_reaper(
    config: BrowserTaskConfig,
    state,
    docker_socket: str,
    *,
    now: float,
) -> None:
    engine = DockerEngine(docker_socket)
    await engine.initialize()
    try:
        reaper = BrowserTaskReaper(
            task_config=config,
            reaper_config=ReaperConfig(orphan_grace_seconds=0),
            state=state,
            engine=engine,
        )
        await reaper.run_once(now=now)
    finally:
        await engine.aclose()


async def deployment_resources(config: BrowserTaskConfig, docker_socket: str):
    engine = DockerEngine(docker_socket)
    await engine.initialize()
    try:
        return await engine.list_resources(
            labels={
                f"{LABEL_PREFIX}.managed": "true",
                f"{LABEL_PREFIX}.schema": POLICY_SCHEMA,
                f"{LABEL_PREFIX}.deployment": config.deployment_id,
            }
        )
    finally:
        await engine.aclose()


async def inject_partial_group(
    config: BrowserTaskConfig,
    state: TaskStateStore,
    docker_socket: str,
    runtime_identity: dict,
) -> str:
    now = time.time()
    task_id = str(uuid4())
    request_id = str(uuid4())
    owner = "smoke-partial"
    owner_hash = hashlib.sha256(f"{config.deployment_id}\0{owner}".encode()).hexdigest()
    runtime_fingerprint = RuntimeIdentity.model_validate(runtime_identity).fingerprint()
    record = TaskRecord(
        task_id=task_id,
        request_id=request_id,
        request_fingerprint=hashlib.sha256(request_id.encode()).hexdigest(),
        capability_nonce=generate_capability_nonce(),
        owner_job_id=owner,
        owner_hash=owner_hash,
        purpose="render",
        source_url="https://example.com/",
        expected_runtime_fingerprint=runtime_fingerprint,
        state="creating",
        stage="volume_ready",
        version=0,
        created_at=now - 10,
        creation_deadline=now - 1,
        lease_expires_at=now + 30,
        hard_expires_at=now + 60,
        updated_at=now - 10,
        adapter_endpoint=None,
        runtime_identity=runtime_identity,
        resources_released=False,
        cleanup_reason=None,
        last_error=None,
    )
    state.reserve(record, observed_tasks={}, max_tasks=2, max_explore=1)
    labels = {
        f"{LABEL_PREFIX}.managed": "true",
        f"{LABEL_PREFIX}.schema": POLICY_SCHEMA,
        f"{LABEL_PREFIX}.deployment": config.deployment_id,
        f"{LABEL_PREFIX}.task": task_id,
        f"{LABEL_PREFIX}.owner": owner_hash,
        f"{LABEL_PREFIX}.purpose": "render",
        f"{LABEL_PREFIX}.resource-kind": "control-volume",
        f"{LABEL_PREFIX}.created-at": str(int(record.created_at)),
        f"{LABEL_PREFIX}.creation-deadline": str(int(record.creation_deadline)),
        f"{LABEL_PREFIX}.hard-deadline": str(int(record.hard_expires_at)),
        f"{LABEL_PREFIX}.policy": config.policy_version,
        f"{LABEL_PREFIX}.runtime": runtime_fingerprint,
        f"{LABEL_PREFIX}.image": "none",
    }
    engine = DockerEngine(docker_socket)
    await engine.initialize()
    try:
        await engine.create_volume(name=f"reader-browser-task-{task_id}", labels=labels)
    finally:
        await engine.aclose()
    return task_id


async def cleanup_deployment(config: BrowserTaskConfig, state: TaskStateStore, docker_socket: str):
    engine = DockerEngine(docker_socket)
    await engine.initialize()
    try:
        try:
            task_ids = {record.task_id for record in state.list_records()}
        except StateUnavailable:
            task_ids = set()
        task_ids.update(
            resource.labels[f"{LABEL_PREFIX}.task"]
            for resource in await deployment_resources(config, docker_socket)
            if resource.labels.get(f"{LABEL_PREFIX}.task")
        )
        for task_id in task_ids:
            error = await cleanup_task_resources(engine, config, task_id)
            if error:
                raise RuntimeError(f"cleanup failed for smoke task {task_id}: {error}")
        remaining = await engine.list_resources(
            labels={
                f"{LABEL_PREFIX}.managed": "true",
                f"{LABEL_PREFIX}.schema": POLICY_SCHEMA,
                f"{LABEL_PREFIX}.deployment": config.deployment_id,
            }
        )
        if remaining:
            raise RuntimeError("smoke cleanup left deployment resources")
    finally:
        await engine.aclose()


def main() -> None:
    args = parse_args()
    lightpanda_sha, agent_browser_sha, policy = asyncio.run(image_policy(args))
    deployment_id = f"reader-browser-smoke-{uuid4()}"
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="reader-browser-lifecycle-") as temporary:
        state_dir = Path(temporary)
        token_file = state_dir / "manager.token"
        token = secrets.token_urlsafe(32)
        token_file.write_text(token)
        token_file.chmod(0o600)
        env = manager_environment(
            args,
            state_dir=state_dir,
            token_file=token_file,
            deployment_id=deployment_id,
            port=port,
            lightpanda_sha=lightpanda_sha,
            agent_browser_sha=agent_browser_sha,
            policy_version=policy,
        )
        headers = {"Authorization": f"Bearer {token}"}
        base_url = f"http://127.0.0.1:{port}"
        state = TaskStateStore(state_dir / "tasks.sqlite3")
        from app.browser_tasks.manager_app import load_task_config

        old_environ = os.environ.copy()
        os.environ.update(env)
        try:
            config, _, _ = load_task_config()
        finally:
            os.environ.clear()
            os.environ.update(old_environ)
        try:
            with (
                manager_process(env) as first_manager,
                httpx.Client(base_url=base_url, headers=headers, timeout=45) as client,
            ):
                wait_ready(client, first_manager)
                descriptor = client.get("/v1/runtime-descriptor").raise_for_status().json()
                runtime_fingerprint = RuntimeIdentity.model_validate(
                    descriptor["runtime_identity"]
                ).fingerprint()
                request_id = str(uuid4())
                create_body = {
                    "request_id": request_id,
                    "owner_job_id": "smoke-explore",
                    "purpose": "explore",
                    "source_url": "https://example.com/",
                    "expected_runtime_fingerprint": runtime_fingerprint,
                }
                first = client.post("/v1/tasks", json=create_body).raise_for_status().json()
                first_manager.send_signal(signal.SIGKILL)
                first_manager.wait(timeout=10)

            with (
                manager_process(env) as restarted,
                httpx.Client(base_url=base_url, headers=headers, timeout=45) as client,
            ):
                wait_ready(client, restarted)
                duplicate = client.post("/v1/tasks", json=create_body).raise_for_status().json()
                assert duplicate["task_id"] == first["task_id"]
                assert duplicate["capability"] == first["capability"]
                task_headers = {"X-Reader-Task-Capability": first["capability"]}
                renewed = (
                    client.post(
                        f"/v1/tasks/{first['task_id']}/renew",
                        json={"request_id": str(uuid4())},
                        headers=task_headers,
                    )
                    .raise_for_status()
                    .json()
                )
                assert renewed["state"] == "running"
                asyncio.run(run_reaper(config, state, args.docker_socket, now=time.time()))
                after_reap = (
                    client.get(f"/v1/tasks/{first['task_id']}", headers=task_headers)
                    .raise_for_status()
                    .json()
                )
                assert after_reap["state"] == "running"

                render = (
                    client.post(
                        "/v1/tasks",
                        json={
                            "request_id": str(uuid4()),
                            "owner_job_id": "smoke-render",
                            "purpose": "render",
                            "source_url": "https://example.com/",
                            "expected_runtime_fingerprint": runtime_fingerprint,
                        },
                    )
                    .raise_for_status()
                    .json()
                )
                client.post(
                    f"/v1/tasks/{render['task_id']}/close",
                    json={"request_id": str(uuid4()), "reason": "normal"},
                    headers={"X-Reader-Task-Capability": render["capability"]},
                ).raise_for_status()
                live_task_ids = {
                    resource.labels.get(f"{LABEL_PREFIX}.task")
                    for resource in asyncio.run(deployment_resources(config, args.docker_socket))
                }
                assert first["task_id"] in live_task_ids
                assert render["task_id"] not in live_task_ids

            record = state.get(first["task_id"])
            claimed = state.claim_cleaning(
                record, reason="injected_cleanup_failure", now=time.time()
            )
            state.finish_cleanup(claimed, error="injected_remove_failure", now=time.time())
            asyncio.run(run_reaper(config, state, args.docker_socket, now=time.time()))
            assert state.get(first["task_id"]).state == "closed"

            crash_request_id = str(uuid4())
            crash_body = {
                "request_id": crash_request_id,
                "owner_job_id": "smoke-create-crash",
                "purpose": "explore",
                "source_url": "https://example.com/",
                "expected_runtime_fingerprint": runtime_fingerprint,
            }
            with manager_process(env) as creating_manager:
                with httpx.Client(base_url=base_url, headers=headers, timeout=45) as client:
                    wait_ready(client, creating_manager)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(
                        lambda: httpx.post(
                            f"{base_url}/v1/tasks",
                            json=crash_body,
                            headers=headers,
                            timeout=45,
                        )
                    )
                    deadline = time.monotonic() + 30
                    observed_resource = False
                    while time.monotonic() < deadline and not pending.done():
                        if asyncio.run(deployment_resources(config, args.docker_socket)):
                            observed_resource = True
                            creating_manager.send_signal(signal.SIGKILL)
                            creating_manager.wait(timeout=10)
                            break
                        time.sleep(0.005)
                    if not observed_resource:
                        raise RuntimeError(
                            "could not intercept a real manager create before it completed"
                        )
                    try:
                        pending.result(timeout=5).raise_for_status()
                    except (httpx.HTTPError, subprocess.SubprocessError):
                        pass
            crashed = state.get_by_request(crash_request_id)
            if crashed is None or crashed.state != "creating":
                raise RuntimeError("manager was not killed during its creating state")
            asyncio.run(
                run_reaper(
                    config,
                    state,
                    args.docker_socket,
                    now=crashed.creation_deadline + 1,
                )
            )
            assert state.get(crashed.task_id).state == "closed"

            partial_id = asyncio.run(
                inject_partial_group(
                    config, state, args.docker_socket, descriptor["runtime_identity"]
                )
            )
            asyncio.run(run_reaper(config, state, args.docker_socket, now=time.time()))
            assert state.get(partial_id).state == "closed"

            with (
                manager_process(env) as manager,
                httpx.Client(base_url=base_url, headers=headers, timeout=45) as client,
            ):
                wait_ready(client, manager)
                fallback = (
                    client.post(
                        "/v1/tasks",
                        json={
                            "request_id": str(uuid4()),
                            "owner_job_id": "smoke-db-fallback",
                            "purpose": "render",
                            "source_url": "https://example.com/",
                            "expected_runtime_fingerprint": runtime_fingerprint,
                        },
                    )
                    .raise_for_status()
                    .json()
                )
                manager.send_signal(signal.SIGKILL)
                manager.wait(timeout=10)
            fallback_record = state.get(fallback["task_id"])
            asyncio.run(
                run_reaper(
                    config,
                    UnavailableState(),
                    args.docker_socket,
                    now=fallback_record.hard_expires_at + 1,
                )
            )
            remaining = asyncio.run(deployment_resources(config, args.docker_socket))
            assert not remaining
            print(f"browser lifecycle smoke passed: deployment={deployment_id}")
        finally:
            asyncio.run(cleanup_deployment(config, state, args.docker_socket))


if __name__ == "__main__":
    main()
