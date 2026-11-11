"""Explicit candidate observation and availability on guarded PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from test_candidate_recovery_postgres import _database

from app.domain.enums import CandidateStatus, ContentKind
from app.ingestion.candidate_queue import upsert_candidates
from app.ingestion.models import (
    ContentAvailability,
    DiscoveredContent,
    DiscoveredMedia,
)
from app.storage.models import Content, ContentMedia, IngestionCandidate, SourceEntry
from app.workers import candidates


async def _upsert(factory, source_id, item, *, counts_as_update=True, baseline_hashes=None):
    async with factory() as session:
        result = await upsert_candidates(
            session,
            source_id=source_id,
            items=(item,),
            observed_at=datetime.now(UTC),
            max_changes=10,
            baseline_hashes=baseline_hashes,
            counts_as_update=counts_as_update,
        )
        await session.commit()
    return result


async def _project(factory, source_id, item, *, counts_as_update=True):
    result = await _upsert(factory, source_id, item, counts_as_update=counts_as_update)
    assert result.changed == 1
    assert await candidates.process_candidates(factory, limit=1) == 1


def _article(*, native_id="article", **changes):
    original = DiscoveredContent(
        native_id=native_id,
        kind=ContentKind.ARTICLE,
        title="Original article",
        external_url=f"https://example.com/{native_id}",
        published_at=datetime(2024, 1, 2, tzinfo=UTC),
        author_name="Original author",
        excerpt_html="<p>Original article body.</p>",
        media=(DiscoveredMedia(original_url="https://example.com/old.jpg"),),
    )
    return replace(original, **changes)




@pytest.mark.postgres
@pytest.mark.asyncio
async def test_unavailable_first_discovery_and_existing_entry_recovery(monkeypatch):
    monkeypatch.setattr(candidates, "_enqueue_default_title_translation", AsyncMock())
    async with _database() as (factory, source_id):
        unavailable = replace(
            _article(native_id="video"),
            kind=ContentKind.VIDEO,
            availability=ContentAvailability.UNAVAILABLE,
            title="Private video",
            author_name=None,
            published_at=None,
            excerpt_html=None,
            media=(),
            raw_metadata={},
        )
        baseline = {unavailable.native_id: unavailable.material_hash()}
        duplicate = await _upsert(
            factory,
            source_id,
            unavailable,
            baseline_hashes=baseline,
            counts_as_update=False,
        )
        assert (duplicate.changed, duplicate.duplicates) == (0, 1)
        await _project(factory, source_id, unavailable, counts_as_update=False)
        async with factory() as session:
            queued = await session.scalar(
                select(IngestionCandidate).where(IngestionCandidate.source_id == source_id)
            )
            assert queued.payload["availability"] == "unavailable"
            assert DiscoveredContent.from_payload(queued.payload) == unavailable
            assert queued.status == CandidateStatus.COMPLETED and queued.content_id is None
            assert (
                await session.scalar(select(SourceEntry).where(SourceEntry.source_id == source_id))
                is None
            )
            assert (
                await session.scalar(
                    select(Content).where(Content.authority_source_id == source_id)
                )
                is None
            )
            candidate_id = queued.id

        available = replace(
            unavailable,
            availability=ContentAvailability.AVAILABLE,
            title="Published video",
            author_name="Creator",
            published_at=datetime(2024, 3, 4, tzinfo=UTC),
            excerpt_html="<p>Available description.</p>",
            media=(DiscoveredMedia(original_url="https://example.com/thumbnail.jpg"),),
        )
        assert available.material_hash() != unavailable.material_hash()
        assert (await _upsert(factory, source_id, available, counts_as_update=True)).changed == 1
        async with factory() as session:
            queued = await session.get(IngestionCandidate, candidate_id)
            assert queued.status == CandidateStatus.PENDING
            assert queued.counts_as_update is False
        assert await candidates.process_candidates(factory, limit=1) == 1
        async with factory() as session:
            entry = await session.scalar(
                select(SourceEntry).where(SourceEntry.source_id == source_id)
            )
            content_id, entry_id, sort_at = entry.content_id, entry.id, entry.feed_sort_at
            assert entry.update_sequence is None

        private_again = replace(
            available,
            availability=ContentAvailability.UNAVAILABLE,
            title="Removed from provider",
            author_name=None,
            published_at=None,
            excerpt_html=None,
            media=(),
            raw_metadata={},
        )
        await _project(factory, source_id, private_again)
        async with factory() as session:
            entry = await session.get(SourceEntry, entry_id)
            content = await session.get(Content, content_id)
            media = list(
                await session.scalars(
                    select(ContentMedia).where(ContentMedia.content_id == content_id)
                )
            )
            assert entry.raw_metadata["availability"] == "unavailable"
            assert (entry.title, entry.author_name, entry.published_at, entry.feed_sort_at) == (
                available.title,
                available.author_name,
                available.published_at,
                sort_at,
            )
            assert (content.title, content.body_html, content.body_text, content.author_name) == (
                available.title,
                available.excerpt_html,
                "Available description.",
                available.author_name,
            )
            assert content.published_at == available.published_at
            assert {item.original_url for item in media if item.is_active} == {
                "https://example.com/thumbnail.jpg"
            }

        recovered = replace(
            available,
            title="Restored video",
            published_at=available.published_at + timedelta(days=1),
            excerpt_html="<p>Restored description.</p>",
            media=(DiscoveredMedia(original_url="https://example.com/restored.jpg"),),
        )
        await _project(factory, source_id, recovered)
        async with factory() as session:
            queued = await session.get(IngestionCandidate, candidate_id)
            entry = await session.get(SourceEntry, entry_id)
            content = await session.get(Content, content_id)
            media = list(
                await session.scalars(
                    select(ContentMedia).where(ContentMedia.content_id == content_id)
                )
            )
            assert queued.status == CandidateStatus.COMPLETED
            assert queued.content_id == content_id and queued.counts_as_update is False
            assert entry.raw_metadata.get("availability") != "unavailable"
            assert (entry.title, entry.published_at, entry.feed_sort_at) == (
                recovered.title,
                recovered.published_at,
                sort_at,
            )
            assert (content.title, content.body_html, content.body_text, content.published_at) == (
                recovered.title,
                recovered.excerpt_html,
                "Restored description.",
                recovered.published_at,
            )
            assert {item.original_url for item in media if item.is_active} == {
                "https://example.com/restored.jpg"
            }
            assert content.content_hash == recovered.material_hash()
        unchanged = await _upsert(factory, source_id, recovered)
        assert (unchanged.changed, unchanged.duplicates) == (0, 1)
