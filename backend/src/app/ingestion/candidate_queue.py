"""Transactional Candidate Queue and Web Frontier upserts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import CandidateStatus, WebFrontierStatus
from app.ingestion.models import DiscoveredContent, DiscoveredWebLink
from app.storage.models import IngestionCandidate, WebFrontier


@dataclass(frozen=True)
class CandidateUpsertResult:
    changed: int
    duplicates: int
    processed_items: int
    cap_reached: bool


async def upsert_candidates(
    session: AsyncSession,
    *,
    source_id: UUID,
    items: tuple[DiscoveredContent, ...],
    observed_at: datetime,
    max_changes: int | None,
    baseline_hashes: dict[str, str] | None = None,
    force_feed_sort_at: datetime | None = None,
) -> CandidateUpsertResult:
    """Persist only new/materially changed items; exact duplicates are free."""
    changed = 0
    duplicates = 0
    processed = 0
    baseline_hashes = baseline_hashes or {}
    seen_native_ids: set[str] = set()
    for item in items:
        # Provider pages occasionally repeat an ID (RSS duplicate GUIDs and X
        # timeline modules are common examples).  Keep the first/newest
        # occurrence so one upstream page cannot consume the change budget more
        # than once for the same durable identity.
        if item.native_id in seen_native_ids:
            duplicates += 1
            processed += 1
            continue
        seen_native_ids.add(item.native_id)
        if max_changes is not None and changed >= max_changes:
            return CandidateUpsertResult(changed, duplicates, processed, True)
        payload_hash = item.material_hash()
        if baseline_hashes.get(item.native_id) == payload_hash:
            duplicates += 1
            processed += 1
            continue
        suggested_sort = force_feed_sort_at or item.published_at or observed_at
        candidate_id = uuid4()
        statement = (
            pg_insert(IngestionCandidate)
            .values(
                id=candidate_id,
                source_id=source_id,
                native_id=item.native_id,
                payload=item.to_payload(),
                payload_hash=payload_hash,
                source_updated_at=item.source_updated_at,
                observed_at=observed_at,
                suggested_feed_sort_at=suggested_sort,
                status=CandidateStatus.PENDING,
                attempt_count=0,
                available_at=observed_at,
                lease_token=None,
                lease_expires_at=None,
                completed_at=None,
                last_error_code=None,
                last_error_message=None,
            )
            .on_conflict_do_update(
                index_elements=[IngestionCandidate.source_id, IngestionCandidate.native_id],
                set_={
                    "payload": item.to_payload(),
                    "payload_hash": payload_hash,
                    "source_updated_at": item.source_updated_at,
                    "observed_at": observed_at,
                    # Existing SourceEntry.feed_sort_at remains immutable; this is
                    # used only if the Candidate has never been admitted before.
                    "suggested_feed_sort_at": suggested_sort,
                    "status": CandidateStatus.PENDING,
                    "attempt_count": 0,
                    "available_at": observed_at,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "completed_at": None,
                    "last_error_code": None,
                    "last_error_message": None,
                    "updated_at": observed_at,
                },
                where=IngestionCandidate.payload_hash != payload_hash,
            )
            .returning(IngestionCandidate.id)
        )
        stored_id = await session.scalar(statement)
        processed += 1
        if stored_id is None:
            duplicates += 1
        else:
            changed += 1
    return CandidateUpsertResult(changed, duplicates, processed, False)


async def upsert_web_frontier(
    session: AsyncSession,
    *,
    source_id: UUID,
    links: tuple[DiscoveredWebLink, ...],
    observed_at: datetime | None = None,
) -> None:
    now = observed_at or datetime.now(UTC)
    for link in links:
        url_hash = hashlib.sha256(link.normalized_url.encode("utf-8")).hexdigest()
        status = (
            WebFrontierStatus.FETCHED
            if link.fetched
            else WebFrontierStatus.PENDING
            if link.accepted
            else WebFrontierStatus.UNCERTAIN
            if link.confidence > 0
            else WebFrontierStatus.REJECTED
        )
        statement = (
            pg_insert(WebFrontier)
            .values(
                id=uuid4(),
                source_id=source_id,
                original_url=link.original_url or link.normalized_url,
                normalized_url=link.normalized_url,
                url_hash=url_hash,
                canonical_url=link.canonical_url,
                discovered_from=link.discovered_from,
                confidence=link.confidence,
                status=status,
                evidence=link.evidence,
                available_at=now,
                last_fetched_at=now if link.fetched else None,
                content_hash=link.content_hash,
                attempt_count=0,
            )
            .on_conflict_do_update(
                index_elements=[WebFrontier.source_id, WebFrontier.url_hash],
                set_={
                    "original_url": link.original_url or link.normalized_url,
                    "canonical_url": link.canonical_url or WebFrontier.canonical_url,
                    "discovered_from": link.discovered_from,
                    "confidence": link.confidence,
                    "status": case(
                        (
                            WebFrontier.status == WebFrontierStatus.FETCHED,
                            WebFrontierStatus.FETCHED,
                        ),
                        else_=status,
                    ),
                    "evidence": link.evidence,
                    "last_fetched_at": now if link.fetched else WebFrontier.last_fetched_at,
                    "content_hash": link.content_hash or WebFrontier.content_hash,
                    "updated_at": now,
                },
            )
        )
        await session.execute(statement)
