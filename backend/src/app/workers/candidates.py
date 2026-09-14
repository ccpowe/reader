"""Independent, lease-based Candidate Queue processor."""

from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from bs4 import BeautifulSoup
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.settings import get_settings
from app.domain.enums import (
    CacheStatus,
    CandidateStatus,
    ContentKind,
    ExtractionStatus,
    SourceStatus,
    TranslationPriority,
    TranslationPurpose,
    TranslationScope,
)
from app.ingestion.html_safety import sanitize_html as _sanitize_html
from app.ingestion.models import DiscoveredContent
from app.services.x_relations import preserve_x_metadata
from app.storage.models import (
    Content,
    ContentMedia,
    FeedSource,
    IngestionCandidate,
    SourceEntry,
)
from app.translation.demand import default_translation_engine
from app.translation.domain import TranslationDemand
from app.translation.store import ensure_translation_work

logger = logging.getLogger(__name__)
_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class CandidateClaim:
    candidate_id: UUID
    lease_token: UUID


async def claim_candidates(session, *, limit: int) -> list[CandidateClaim]:
    if limit < 1:
        return []
    settings = get_settings()
    now = datetime.now(UTC)
    # A crash on the final attempt must still reach a diagnosable terminal
    # state. Bound recovery work and skip rows held by active processors.
    exhausted = list(
        await session.scalars(
            select(IngestionCandidate)
            .where(
                IngestionCandidate.status == CandidateStatus.PROCESSING,
                IngestionCandidate.lease_expires_at < now,
                IngestionCandidate.attempt_count >= _MAX_ATTEMPTS,
            )
            .order_by(IngestionCandidate.lease_expires_at, IngestionCandidate.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for candidate in exhausted:
        candidate.status = CandidateStatus.FAILED
        candidate.lease_token = None
        candidate.lease_expires_at = None
        candidate.last_error_code = "lease_expired"
        candidate.last_error_message = "Final processing attempt expired before completion."
    eligible = (
        select(
            IngestionCandidate.id.label("candidate_id"),
            func.row_number()
            .over(
                partition_by=IngestionCandidate.source_id,
                order_by=(
                    IngestionCandidate.observed_at.desc(),
                    IngestionCandidate.id.desc(),
                ),
            )
            .label("source_rank"),
        )
        .where(
            IngestionCandidate.attempt_count < _MAX_ATTEMPTS,
            or_(
                and_(
                    IngestionCandidate.status == CandidateStatus.PENDING,
                    IngestionCandidate.available_at <= now,
                ),
                and_(
                    IngestionCandidate.status == CandidateStatus.PROCESSING,
                    IngestionCandidate.lease_expires_at < now,
                ),
            ),
        )
        .subquery()
    )
    rows = list(
        await session.scalars(
            select(IngestionCandidate)
            .join(eligible, eligible.c.candidate_id == IngestionCandidate.id)
            .order_by(
                eligible.c.source_rank,
                IngestionCandidate.observed_at.desc(),
                IngestionCandidate.id.desc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True, of=IngestionCandidate)
        )
    )
    claims: list[CandidateClaim] = []
    for candidate in rows:
        token = uuid4()
        candidate.status = CandidateStatus.PROCESSING
        candidate.attempt_count += 1
        candidate.lease_token = token
        candidate.lease_expires_at = now + timedelta(
            seconds=settings.ingestion_candidate_lease_seconds
        )
        claims.append(CandidateClaim(candidate.id, token))
    await session.commit()
    return claims


async def process_candidates(session_factory, *, limit: int | None = None) -> int:
    settings = get_settings()
    batch_size = limit or settings.ingestion_candidate_batch_size
    async with session_factory() as session:
        claims = await claim_candidates(session, limit=batch_size)
    completed = 0
    for claim in claims:
        async with session_factory() as session:
            from app.translation.lifecycle import lock_shared_lifecycle

            await lock_shared_lifecycle(session)
            candidate = await session.scalar(
                select(IngestionCandidate)
                .where(IngestionCandidate.id == claim.candidate_id)
                .with_for_update()
            )
            if (
                candidate is None
                or candidate.status != CandidateStatus.PROCESSING
                or candidate.lease_token != claim.lease_token
            ):
                continue
            try:
                await _process_candidate(session, candidate)
                await session.commit()
            except Exception as exc:
                # Roll back SQL errors and partial projection writes alike.
                # Rollback releases the row lock, so recheck ownership before
                # recording the retry in a fresh transaction.
                await session.rollback()
                candidate = await session.scalar(
                    select(IngestionCandidate)
                    .where(IngestionCandidate.id == claim.candidate_id)
                    .with_for_update()
                )
                if (
                    candidate is None
                    or candidate.status != CandidateStatus.PROCESSING
                    or candidate.lease_token != claim.lease_token
                ):
                    continue
                await _record_failure(candidate, exc)
                logger.warning(
                    "Candidate processing failed: candidate_id=%s error=%s", candidate.id, exc
                )
                await session.commit()
            else:
                completed += 1
    return completed


async def _process_candidate(session, candidate: IngestionCandidate) -> None:
    from app.translation.lifecycle import lock_shared_lifecycle

    await lock_shared_lifecycle(session)
    item = DiscoveredContent.from_payload(candidate.payload)
    item = replace(
        item,
        title=item.title.strip()[:1000] or "Untitled",
        author_name=item.author_name.strip()[:300] if item.author_name else None,
    )
    safe_body_html = _sanitize_html(item.excerpt_html)
    entry = await session.scalar(
        select(SourceEntry).where(
            SourceEntry.source_id == candidate.source_id,
            SourceEntry.native_id == candidate.native_id,
        )
    )
    unavailable = item.raw_metadata.get("availability") == "unavailable"
    if entry is None and unavailable:
        # A private/deleted video that was only part of the initial baseline
        # must not become a new unreadable home-feed card.  Existing entries are
        # retained and marked below.
        _complete_candidate(candidate, content_id=None)
        return
    if entry is None:
        content = await _get_or_create_content(
            session,
            item,
            source_id=candidate.source_id,
            safe_body_html=safe_body_html,
        )
        entry = SourceEntry(
            source_id=candidate.source_id,
            content_id=content.id,
            native_id=item.native_id,
            external_url=item.external_url,
            title=item.title,
            author_name=item.author_name,
            published_at=item.published_at,
            feed_sort_at=candidate.suggested_feed_sort_at,
            raw_metadata=item.raw_metadata,
        )
        session.add(entry)
    else:
        content = await session.scalar(
            select(Content).where(Content.id == entry.content_id).with_for_update()
        )
        if content is None:
            raise RuntimeError("SourceEntry points to missing Content.")
        if unavailable:
            entry.raw_metadata = {**entry.raw_metadata, **item.raw_metadata}
        else:
            entry.external_url = item.external_url
            entry.title = item.title
            entry.author_name = item.author_name
            entry.published_at = item.published_at
            entry.raw_metadata = preserve_x_metadata(entry.raw_metadata, item.raw_metadata)

    discovery_only = bool(item.raw_metadata.get("web_discovery_only"))
    preserve_body = discovery_only and bool(content.body_html or content.body_text)
    safe_body_text = _plain_text(safe_body_html)
    if discovery_only:
        # Missing optional enrichment is not evidence that an existing article
        # lost its body, date, author or image.
        if preserve_body:
            # HTML and text must retain the same source. In particular, a legacy
            # text-only body must not acquire the new listing summary as HTML.
            safe_body_html = content.body_html
            safe_body_text = content.body_text or _plain_text(safe_body_html)
        item = replace(
            item,
            author_name=item.author_name or content.author_name,
            published_at=item.published_at or content.published_at,
        )
        entry.author_name = item.author_name
        entry.published_at = item.published_at

    if content.authority_source_id is None:
        raise RuntimeError("Content has no authoritative Source.")
    is_authoritative = content.authority_source_id == candidate.source_id

    if unavailable and is_authoritative:
        content.content_hash = candidate.payload_hash
    elif not unavailable and is_authoritative:
        content.kind = item.kind
        content.canonical_url = item.external_url
        content.url_hash = hashlib.sha256(item.external_url.encode("utf-8")).hexdigest()
        content.title = item.title
        content.author_name = item.author_name
        content.published_at = item.published_at
        content.excerpt = safe_body_text
        content.body_html = safe_body_html
        content.body_text = safe_body_text
        content.content_hash = candidate.payload_hash
        if not preserve_body:
            content.extraction_status = _resolved_extraction_status(item.kind, safe_body_html)
        await session.flush()
        if not discovery_only or item.media:
            await _synchronise_media(session, content, item)
        await _enqueue_default_title_translation(session, content)

    _complete_candidate(candidate, content_id=content.id)
    source = await session.get(FeedSource, candidate.source_id)
    if source is not None and source.status == SourceStatus.PENDING:
        source.status = SourceStatus.ACTIVE


def _complete_candidate(candidate: IngestionCandidate, *, content_id: UUID | None) -> None:
    candidate.content_id = content_id
    candidate.status = CandidateStatus.COMPLETED
    candidate.completed_at = datetime.now(UTC)
    candidate.lease_token = None
    candidate.lease_expires_at = None
    candidate.last_error_code = None
    candidate.last_error_message = None


async def _get_or_create_content(
    session,
    item: DiscoveredContent,
    *,
    source_id: UUID,
    safe_body_html: str | None = None,
) -> Content:
    if safe_body_html is None:
        safe_body_html = _sanitize_html(item.excerpt_html)
    url_hash = hashlib.sha256(item.external_url.encode("utf-8")).hexdigest()
    await session.execute(
        pg_insert(Content)
        .values(
            kind=item.kind,
            authority_source_id=source_id,
            canonical_url=item.external_url,
            url_hash=url_hash,
            title=item.title,
            author_name=item.author_name,
            published_at=item.published_at,
            excerpt=_plain_text(safe_body_html),
            body_html=safe_body_html,
            body_text=_plain_text(safe_body_html),
            content_hash=item.material_hash(),
            extraction_status=_resolved_extraction_status(item.kind, safe_body_html),
        )
        .on_conflict_do_nothing(
            index_elements=[Content.authority_source_id, Content.url_hash],
            index_where=Content.url_hash.is_not(None),
        )
    )
    content = await session.scalar(
        select(Content)
        .where(
            Content.authority_source_id == source_id,
            Content.url_hash == url_hash,
        )
        .with_for_update()
    )
    if content is None:
        raise RuntimeError("Content upsert did not return the stored row.")
    return content


def _resolved_extraction_status(
    kind: ContentKind,
    body_html: str | None,
) -> ExtractionStatus:
    """Report only work that this pipeline actually completed or attempted."""
    if _plain_text(body_html):
        return ExtractionStatus.SUCCESS
    if kind == ContentKind.ARTICLE:
        return ExtractionStatus.FAILED
    return ExtractionStatus.NOT_NEEDED


async def _synchronise_media(session, content: Content, item: DiscoveredContent) -> None:
    await session.execute(
        update(ContentMedia).where(ContentMedia.content_id == content.id).values(is_active=False)
    )
    for media in item.media:
        await session.execute(
            pg_insert(ContentMedia)
            .values(
                content_id=content.id,
                media_type=media.media_type,
                original_url=media.original_url,
                cache_status=CacheStatus.EXTERNAL,
                mime_type=media.mime_type[:200] if media.mime_type else None,
                sort_order=media.sort_order,
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[ContentMedia.content_id, ContentMedia.original_url],
                set_={
                    "media_type": media.media_type,
                    "mime_type": media.mime_type[:200] if media.mime_type else None,
                    "sort_order": media.sort_order,
                    "is_active": True,
                },
            )
        )


async def _enqueue_default_title_translation(session, content: Content) -> None:
    engine = default_translation_engine()
    if engine is None:
        return
    await ensure_translation_work(
        session,
        [
            TranslationDemand(
                item_id=str(content.id),
                text=content.title,
                purpose=TranslationPurpose.TITLE,
                target_locale=get_settings().translation_default_target_locale,
                scope=TranslationScope.SHARED,
                priority=int(TranslationPriority.INGESTION),
            )
        ],
        engine=engine,
    )


async def _record_failure(candidate: IngestionCandidate, exc: Exception) -> None:
    now = datetime.now(UTC)
    candidate.lease_token = None
    candidate.lease_expires_at = None
    candidate.last_error_code = type(exc).__name__
    candidate.last_error_message = str(exc)[:2000]
    if candidate.attempt_count >= _MAX_ATTEMPTS:
        candidate.status = CandidateStatus.FAILED
        return
    candidate.status = CandidateStatus.PENDING
    delays = (2, 10, 30, 120, 360)
    delay = delays[min(candidate.attempt_count - 1, len(delays) - 1)]
    candidate.available_at = now + timedelta(minutes=delay * random.uniform(0.9, 1.1))


def _plain_text(html: str | None) -> str | None:
    if not html:
        return None
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True) or None
