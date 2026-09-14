"""Enrich existing source-owned X entries from an offline Scweet JSON file.

Defaults to dry-run. Does not call X/Scweet, create posts or modify collected text.
Run: python -m app.ingestion.x_relationship_backfill --source-id UUID --input FILE [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.services.x_relations import normalize_x_relations
from app.storage.database import build_engine, build_session_factory
from app.storage.models import FeedSource, SourceEntry


def read_records(path: Path) -> list[dict]:
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Offline batch must be smaller than 20 MiB.")
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        payload = payload.get("items", payload.get("tweets"))
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("Expected a JSON array of TweetRecord objects or an items/tweets array.")
    return payload


async def backfill(session, *, source_id: UUID, records: list[dict], apply: bool) -> dict:
    source = await session.get(FeedSource, source_id)
    if source is None or str(source.kind) != "x":
        raise ValueError("Source must identify an existing X source.")
    counts = {"records": len(records), "matched": 0, "updated": 0, "skipped": 0, "apply": apply}
    seen = set()
    for record in records:
        relations = normalize_x_relations(record)
        native_id = relations.get("tweet_id")
        if not native_id or native_id in seen or relations["completeness"] != "parsed":
            counts["skipped"] += 1
            continue
        seen.add(native_id)
        statement = select(SourceEntry).where(
            SourceEntry.source_id == source_id, SourceEntry.native_id == native_id
        )
        if apply:
            statement = statement.with_for_update()
        entry = await session.scalar(statement)
        if entry is None:
            counts["skipped"] += 1
            continue
        counts["matched"] += 1
        metadata = dict(entry.raw_metadata or {})
        # This is a historical evidence repair, not an instruction to overwrite
        # newer normalized relationships on every invocation.
        if metadata.get("x", {}).get("completeness") == "parsed":
            counts["skipped"] += 1
            continue
        counts["updated"] += 1
        if apply:
            entry.raw_metadata = {
                **metadata,
                "x": relations,
                "is_reply": bool(relations["reply_to"] or metadata.get("is_reply")),
                "is_retweet": bool(relations["is_repost"] or metadata.get("is_retweet")),
            }
    if apply:
        await session.commit()
    else:
        await session.rollback()
    return counts


async def _run(args):
    records = read_records(args.input)
    engine = build_engine()
    try:
        async with build_session_factory(engine)() as session:
            print(
                json.dumps(
                    await backfill(
                        session, source_id=args.source_id, records=records, apply=args.apply
                    )
                )
            )
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", required=True, type=UUID)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
