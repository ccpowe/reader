from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.enums import ContentKind
from app.ingestion.candidate_queue import upsert_candidates
from app.ingestion.models import (
    ContentAvailability,
    ContentObservation,
    DiscoveredContent,
    DiscoveredMedia,
    checkpoint_for_items,
)


def _item(native_id: str, title: str) -> DiscoveredContent:
    return DiscoveredContent(
        native_id=native_id,
        kind=ContentKind.ARTICLE,
        title=title,
        external_url=f"https://example.com/{native_id}",
        published_at=None,
    )


def test_dated_material_hash_preserves_installed_digest_and_ignores_date_jitter() -> None:
    item = DiscoveredContent(
        native_id="existing",
        kind=ContentKind.ARTICLE,
        title="既有条目",
        external_url="https://example.com/existing",
        published_at=datetime(2024, 1, 2, tzinfo=UTC),
        author_name="Author",
        excerpt_html="<p>Existing body</p>",
        raw_metadata={"section": "tech", "score": 42},
        media=(
            DiscoveredMedia(original_url="https://example.com/image.png", mime_type="image/png"),
        ),
        source_updated_at=datetime(2024, 1, 3, tzinfo=UTC),
    )
    # Captured from the installed algorithm before the missing-date marker.
    installed_digest = "bf81a64036155282c582724b6c3de0c54c0f512e6c06632e23423b2b4939b47e"
    assert item.material_hash() == installed_digest
    assert (
        replace(item, published_at=item.published_at + timedelta(days=1)).material_hash()
        == installed_digest
    )
    assert replace(item, published_at=None).material_hash() != installed_digest


def test_material_hash_tracks_explicit_policy_not_metadata_control_keys() -> None:
    item = _item("same", "Unchanged title")
    assert (
        replace(item, availability=ContentAvailability.UNAVAILABLE).material_hash()
        != item.material_hash()
    )
    assert (
        replace(item, observation=ContentObservation.LISTING).material_hash()
        != item.material_hash()
    )
    assert (
        replace(
            item, raw_metadata={"availability": "unavailable", "web_discovery_only": True}
        ).material_hash()
        == item.material_hash()
    )


def test_checkpoint_keeps_the_whole_bounded_page_as_baseline() -> None:
    items = tuple(_item(f"item-{index}", f"Item {index}") for index in range(40))

    checkpoint = checkpoint_for_items(items, anchor_limit=30)

    assert len(checkpoint["head_ids"]) == 30
    assert len(checkpoint["item_hashes"]) == 40
    assert checkpoint["item_hashes"]["item-39"] == items[-1].material_hash()


@pytest.mark.asyncio
async def test_baseline_duplicate_does_not_consume_candidate_cap() -> None:
    session = AsyncMock()
    first = _item("known", "Known")
    second = _item("new", "New")
    session.scalar.return_value = uuid4()

    result = await upsert_candidates(
        session,
        source_id=uuid4(),
        items=(first, second),
        observed_at=datetime.now(UTC),
        max_changes=1,
        baseline_hashes={"known": first.material_hash()},
    )

    assert result.changed == 1
    assert result.duplicates == 1
    assert result.processed_items == 2
    assert result.cap_reached is False
    session.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_candidate_cap_stops_before_the_next_material_change() -> None:
    session = AsyncMock()
    session.scalar.return_value = uuid4()

    result = await upsert_candidates(
        session,
        source_id=uuid4(),
        items=(_item("one", "One"), _item("two", "Two")),
        observed_at=datetime.now(UTC),
        max_changes=1,
    )

    assert result.changed == 1
    assert result.processed_items == 1
    assert result.cap_reached is True


@pytest.mark.asyncio
async def test_duplicate_native_id_inside_one_provider_page_counts_once() -> None:
    session = AsyncMock()
    session.scalar.return_value = uuid4()

    result = await upsert_candidates(
        session,
        source_id=uuid4(),
        items=(_item("same", "Newest representation"), _item("same", "Older duplicate")),
        observed_at=datetime.now(UTC),
        max_changes=2,
    )

    assert result.changed == 1
    assert result.duplicates == 1
    assert result.processed_items == 2
    session.scalar.assert_awaited_once()
