"""Local operational access to durable Web rule authoring, without model calls."""

from __future__ import annotations

import argparse
from uuid import UUID

from app.services import web_rule_jobs as jobs


def configure_parser(commands) -> None:
    parser = commands.add_parser("web-rules", help="inspect and recover Web rule authoring tasks")
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="list jobs and authoring runtime status")
    listing.add_argument(
        "--status",
        choices=(
            "queued",
            "running",
            "retry_wait",
            "succeeded",
            "failed",
            "blocked",
            "cancelled",
        ),
    )
    listing.add_argument("--source-id", type=UUID)
    listing.add_argument("--limit", type=_limit, default=50)
    show = actions.add_parser("show", help="show one task, its usage and retained diagnostic data")
    show.add_argument("job_id", type=UUID)
    retry = actions.add_parser("retry", help="explicitly grant a failed task a new bounded attempt")
    retry.add_argument("job_id", type=UUID)
    retry.add_argument(
        "--request-id",
        type=UUID,
        required=True,
        help="idempotency UUID; reuse it if repeating this same operational request",
    )
    actions.add_parser("resume-engine", help="clear engine pause without retrying terminal jobs")


def _limit(value: str) -> int:
    limit = int(value)
    if not 1 <= limit <= 100:
        raise argparse.ArgumentTypeError("limit must be 1..100")
    return limit


async def run_web_rule_admin(session_factory, args: argparse.Namespace):
    async with session_factory() as session:
        if args.action == "list":
            result = {
                "runtime": await jobs.web_rule_agent_status(session),
                "jobs": await jobs.list_web_rule_jobs(
                    session, status=args.status, source_id=args.source_id, limit=args.limit
                ),
            }
        elif args.action == "show":
            result = await jobs.get_web_rule_job(session, job_id=args.job_id)
        elif args.action == "retry":
            job = await jobs.retry_web_rule_job(
                session, job_id=args.job_id, request_id=args.request_id
            )
            await session.flush()
            result = await jobs.get_web_rule_job(session, job_id=job.id)
        elif args.action == "resume-engine":
            result = await jobs.resume_web_rule_agent(session)
        else:
            raise ValueError("unknown Web rule operation")
        await session.commit()
        return result
