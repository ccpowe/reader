from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.enums import ContentKind
from app.ingestion.candidate_queue import upsert_candidates
from app.ingestion.models import DiscoveredContent, checkpoint_for_items


def _item(native_id: str, title: str) -> DiscoveredContent:
    return DiscoveredContent(
        native_id=native_id,
        kind=ContentKind.ARTICLE,
        title=title,
        external_url=f"https://example.com/{native_id}",
        published_at=None,
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
