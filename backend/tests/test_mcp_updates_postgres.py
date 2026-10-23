"""Account-scoped MCP updates against a disposable, migrated PostgreSQL database."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, text
from test_candidate_recovery_postgres import _database

from app.api import mcp_updates
from app.core.auth import AuthenticatedUser
from app.core.settings import Settings
from app.storage.database import get_session
from app.storage.models import Content, FeedSource, SourceEntry, SourceSubscription


@pytest.mark.asyncio
async def test_updates_routes_require_reader_authentication():
    app = FastAPI()
    app.include_router(mcp_updates.router, prefix="/v1")
    app.dependency_overrides[get_session] = lambda: object()
    query = {
        "start_time": "2025-01-01T00:00:00Z",
        "end_time": "2025-01-02T00:00:00Z",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for path in (
            "/v1/mcp/updates/summary",
            f"/v1/mcp/subscriptions/{uuid4()}/updates",
        ):
            response = await client.get(path, params=query)
            assert response.status_code == 401


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_update_summary_and_pages_share_scope_window_dedup_and_snapshot(monkeypatch):
    settings = Settings(
        _env_file=None,
        server_id="mcp-updates-test",
        server_access_token="test-server-token",
        auth_jwt_secret="test-mcp-updates-secret-with-more-than-32-chars",
    )
    monkeypatch.setattr(mcp_updates, "get_settings", lambda: settings)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=2)
    user_id, other_id = uuid4(), uuid4()
    second_source_id = uuid4()
    async with _database() as (factory, source_id):
        async with factory() as session:
            await session.execute(
                text("INSERT INTO profiles (id) VALUES (:first), (:second)"),
                {"first": user_id, "second": other_id},
            )
            session.add(
                FeedSource(
                    id=second_source_id,
                    kind="rss",
                    canonical_key=f"mcp-updates:{second_source_id}",
                    canonical_url="https://example.org/updates",
                    display_name="Empty",
                )
            )
            subscription_id, empty_id, other_subscription_id = uuid4(), uuid4(), uuid4()
            session.add_all(
                [
                    SourceSubscription(
                        id=subscription_id,
                        user_id=user_id,
                        source_id=source_id,
                        folder_name="News",
                        custom_name="My Updates",
                        include_in_home=False,
                    ),
                    SourceSubscription(
                        id=empty_id,
                        user_id=user_id,
                        source_id=second_source_id,
                        folder_name=None,
                    ),
                    SourceSubscription(
                        id=other_subscription_id,
                        user_id=other_id,
                        source_id=second_source_id,
                        folder_name="Other",
                    ),
                ]
            )
            content_ids = []
            for index in range(32):
                content_id = uuid4()
                content_ids.append(content_id)
                published_at = start + timedelta(minutes=index)
                session.add(
                    Content(
                        id=content_id,
                        authority_source_id=source_id,
                        kind="article",
                        title=f"Raw {index}",
                        published_at=published_at,
                    )
                )
                session.add(
                    SourceEntry(
                        source_id=source_id,
                        content_id=content_id,
                        native_id=f"native-{index}",
                        title=f"Raw {index}",
                        external_url=f"https://example.com/{index}",
                        published_at=published_at,
                        feed_sort_at=published_at,
                        fetched_at=published_at + timedelta(seconds=1),
                    )
                )
            # A native-ID alias must not count twice or supersede the first entry.
            session.add(
                SourceEntry(
                    source_id=source_id,
                    content_id=content_ids[0],
                    native_id="native-alias",
                    title="Alias",
                    external_url="https://example.com/alias",
                    feed_sort_at=start,
                    fetched_at=start + timedelta(hours=1),
                )
            )
            boundary_ids = {}
            for native_id, published_at, fetched_at in (
                ("before", start - timedelta(microseconds=1), start),
                ("end", end, start),
                ("undated", None, start + timedelta(minutes=40)),
            ):
                content_id = uuid4()
                boundary_ids[native_id] = content_id
                session.add(
                    Content(
                        id=content_id,
                        authority_source_id=source_id,
                        kind="post",
                        title=native_id,
                        published_at=published_at,
                    )
                )
                session.add(
                    SourceEntry(
                        source_id=source_id,
                        content_id=content_id,
                        native_id=native_id,
                        title=native_id,
                        external_url=f"https://example.com/{native_id}",
                        published_at=published_at,
                        feed_sort_at=fetched_at,
                        fetched_at=fetched_at,
                    )
                )
            await session.commit()

        user = AuthenticatedUser(id=user_id, email=None, claims={})
        other = AuthenticatedUser(id=other_id, email=None, claims={})
        try:
            async with factory() as session:
                summary = await mcp_updates.get_updates_summary(
                    start, end, group=None, current_user=user, session=session
                )
                assert summary.total == 33
                groups = {group.name: group for group in summary.groups}
                assert {name: group.total for name, group in groups.items()} == {
                    None: 0,
                    "News": 33,
                }
                assert groups[None].subscriptions[0].subscription_id == str(empty_id)
                assert groups["News"].subscriptions[0].source_name == "My Updates"
                assert groups["News"].subscriptions[0].source_kind == "rss"
                assert groups["News"].subscriptions[0].count == 33
                assert (
                    await mcp_updates.get_updates_summary(
                        start, end, group="", current_user=user, session=session
                    )
                ).total == 0
                assert (
                    await mcp_updates.get_updates_summary(
                        start, end, group=" News ", current_user=user, session=session
                    )
                ).total == 33
                assert (
                    await mcp_updates.get_updates_summary(
                        start, end, group=None, current_user=other, session=session
                    )
                ).total == 0

                empty = await mcp_updates.list_subscription_updates(
                    empty_id, start, end, cursor=None, current_user=user, session=session
                )
                assert empty.total == 0
                assert empty.items == []
                assert empty.next_cursor is None

                first = await mcp_updates.list_subscription_updates(
                    subscription_id, start, end, cursor=None, current_user=user, session=session
                )
                assert first.total == 33
                assert len(first.items) == 30
                assert first.next_cursor is not None
                assert all(item.content_type in {"article", "post"} for item in first.items)
                assert all(start <= item.effective_at < end for item in first.items)
                assert first.items[0].title == "undated"
                assert first.items[0].published_at is None
                assert first.items[0].effective_at == first.items[0].fetched_at

                with pytest.raises(HTTPException) as denied:
                    await mcp_updates.list_subscription_updates(
                        empty_id, start, end, cursor=None, current_user=other, session=session
                    )
                assert denied.value.status_code == 404
                with pytest.raises(HTTPException) as denied:
                    await mcp_updates.list_subscription_updates(
                        other_subscription_id,
                        start,
                        end,
                        cursor=None,
                        current_user=user,
                        session=session,
                    )
                assert denied.value.status_code == 404

                # A newly fetched, backdated item must not shift or extend this traversal.
                late_id = uuid4()
                session.add(
                    Content(
                        id=late_id,
                        authority_source_id=source_id,
                        kind="article",
                        title="Late",
                        published_at=start + timedelta(minutes=29, seconds=30),
                    )
                )
                session.add(
                    SourceEntry(
                        source_id=source_id,
                        content_id=late_id,
                        native_id="late",
                        title="Late",
                        external_url="https://example.com/late",
                        feed_sort_at=start,
                        fetched_at=datetime.now(UTC) + timedelta(minutes=1),
                    )
                )
                await session.commit()

                second = await mcp_updates.list_subscription_updates(
                    subscription_id,
                    start,
                    end,
                    cursor=first.next_cursor,
                    current_user=user,
                    session=session,
                )
                assert second.total == 33
                assert len(second.items) == 3
                assert second.next_cursor is None
                ids = [item.content_id for item in first.items + second.items]
                assert len(ids) == len(set(ids)) == 33
                assert str(content_ids[0]) in ids
                assert str(boundary_ids["undated"]) in ids
                assert str(boundary_ids["before"]) not in ids
                assert str(boundary_ids["end"]) not in ids
                assert str(late_id) not in ids
                assert first.items[-1].effective_at >= second.items[0].effective_at
                oldest = next(
                    item for item in second.items if item.content_id == str(content_ids[0])
                )
                assert oldest.title == "Raw 0"

                for invalid_cursor, account, bound in (
                    ("invalid", user, end),
                    (first.next_cursor, other, end),
                    (first.next_cursor, user, end + timedelta(seconds=1)),
                ):
                    with pytest.raises(HTTPException) as invalid:
                        await mcp_updates.list_subscription_updates(
                            subscription_id,
                            start,
                            bound,
                            cursor=invalid_cursor,
                            current_user=account,
                            session=session,
                        )
                    assert invalid.value.status_code == 422
                with pytest.raises(HTTPException) as invalid:
                    await mcp_updates.list_subscription_updates(
                        empty_id,
                        start,
                        end,
                        cursor=first.next_cursor,
                        current_user=user,
                        session=session,
                    )
                assert invalid.value.status_code == 422
                for naive_start, invalid_end in ((start.replace(tzinfo=None), end), (end, end)):
                    with pytest.raises(HTTPException) as invalid:
                        await mcp_updates.get_updates_summary(
                            naive_start, invalid_end, group=None, current_user=user, session=session
                        )
                    assert invalid.value.status_code == 422
                disabled = await session.get(SourceSubscription, empty_id)
                disabled.is_enabled = False
                await session.commit()
                summary = await mcp_updates.get_updates_summary(
                    start, end, group=None, current_user=user, session=session
                )
                assert [group.name for group in summary.groups] == ["News"]
                with pytest.raises(HTTPException) as denied:
                    await mcp_updates.list_subscription_updates(
                        empty_id, start, end, cursor=None, current_user=user, session=session
                    )
                assert denied.value.status_code == 404
        finally:
            async with factory() as session:
                await session.execute(
                    delete(SourceSubscription).where(
                        SourceSubscription.user_id.in_((user_id, other_id))
                    )
                )
                await session.execute(delete(FeedSource).where(FeedSource.id == second_source_id))
                await session.execute(
                    text("DELETE FROM profiles WHERE id IN (:first, :second)"),
                    {"first": user_id, "second": other_id},
                )
                await session.commit()
