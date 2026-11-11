"""Transactional candidate queue upserts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import CandidateStatus
from app.ingestion.models import DiscoveredContent
from app.storage.models import IngestionCandidate


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
    counts_as_update: bool = True,
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
                counts_as_update=counts_as_update,
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
                    # The first discovery decides whether this was baseline data.
                    # A later head scan must not turn a pending initial candidate
                    # into a user-visible update.
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
