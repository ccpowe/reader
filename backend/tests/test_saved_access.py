from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.api.saved import _saved_item_response


def test_saved_content_remains_visible_after_its_subscription_is_removed() -> None:
    content_id = uuid4()
    source_id = uuid4()
    content = SimpleNamespace(id=content_id, excerpt="saved excerpt", media=[])
    entry = SimpleNamespace(
        title="Saved article",
        external_url="https://example.com/article",
        author_name="Author",
        published_at=datetime.now(UTC),
        fetched_at=datetime.now(UTC),
    )
    source = SimpleNamespace(
        id=source_id, display_name="Original source", avatar_url=None, kind="rss"
    )
    response = _saved_item_response(SimpleNamespace(), entry, content, source, None)

    assert response.content_id == str(content_id)
    assert response.source_name == "Original source"
