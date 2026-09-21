"""Trusted local administration for the current browser-task deployment."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from app.browser_tasks.controller import LABEL_PREFIX, POLICY_SCHEMA, cleanup_task_resources
from app.browser_tasks.manager_app import build_runtime_from_environment
from app.browser_tasks.state import StateConflict, StateUnavailable


async def drain() -> bool:
    """Stop this deployment's known tasks without accepting Docker object selectors."""
    runtime = await build_runtime_from_environment(acquire_controller_lock=False)
    success = True
    try:
        records = await asyncio.to_thread(runtime.state.list_records)
        for initial in records:
            record = initial
            if record.state != "cleaning":
                try:
                    record = await asyncio.to_thread(
                        runtime.state.claim_cleaning,
                        record,
                        reason="admin_drain",
                        now=time.time(),
                    )
                except StateConflict:
                    latest = await asyncio.to_thread(runtime.state.get, record.task_id)
                    if latest is None:
                        continue
                    record = latest
                    if record.state != "cleaning":
                        success = False
                        continue
            error = await cleanup_task_resources(runtime.engine, runtime.config, record.task_id)
            try:
                await asyncio.to_thread(
                    runtime.state.finish_cleanup, record, error=error, now=time.time()
                )
            except (StateConflict, StateUnavailable):
                success = False
            if error:
                success = False

        resources = await runtime.engine.list_resources(
            labels={
                f"{LABEL_PREFIX}.managed": "true",
                f"{LABEL_PREFIX}.schema": POLICY_SCHEMA,
                f"{LABEL_PREFIX}.deployment": runtime.config.deployment_id,
            }
        )
        orphan_ids = {
            resource.labels.get(f"{LABEL_PREFIX}.task")
            for resource in resources
            if resource.labels.get(f"{LABEL_PREFIX}.task")
        }
        for task_id in orphan_ids:
            if await cleanup_task_resources(runtime.engine, runtime.config, str(task_id)):
                success = False
        remaining = await runtime.engine.list_resources(
            labels={
                f"{LABEL_PREFIX}.managed": "true",
                f"{LABEL_PREFIX}.schema": POLICY_SCHEMA,
                f"{LABEL_PREFIX}.deployment": runtime.config.deployment_id,
            }
        )
        return success and not remaining
    finally:
        await runtime.engine.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reader browser task local administration")
    parser.add_argument("command", choices=("drain",))
    args = parser.parse_args()
    if args.command == "drain" and not asyncio.run(drain()):
        sys.exit(1)


if __name__ == "__main__":
    main()
