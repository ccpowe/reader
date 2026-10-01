from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api import sources
from app.api.sources import (
    AddRedditSourceRequest,
    AddRssSourceRequest,
    UpdateSourceSubscriptionRequest,
)
from app.core.auth import AuthenticatedUser, require_current_user
from app.domain.enums import SourceStatus
from app.ingestion import url_safety
from app.storage.database import get_session
from app.storage.models import FeedSource, SourceSubscription, SourceSyncState


def test_subscription_requests_reject_removed_max_items_field() -> None:
    with pytest.raises(ValidationError):
        AddRssSourceRequest(url="https://example.com/feed.xml", max_items=10)
    with pytest.raises(ValidationError):
        UpdateSourceSubscriptionRequest(max_items=10)


def test_reddit_subscription_is_a_community_not_a_ranking_view() -> None:
    request = AddRedditSourceRequest(subreddit="LocalLLaMA")
    assert request.subreddit == "LocalLLaMA"

    with pytest.raises(ValidationError):
        AddRedditSourceRequest(subreddit="LocalLLaMA", ranking="top")


def test_reddit_subscription_accepts_prefixed_max_length_community() -> None:
    subreddit = f"r/{'a' * 21}"

    request = AddRedditSourceRequest(subreddit=subreddit)

    assert request.subreddit == subreddit


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["rss", "web"])
async def test_ipv6_subscription_routes_accept_public_literal(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def unexpected_dns(*_args, **_kwargs):
        raise AssertionError("Literal IPv6 source URL must not initiate DNS resolution")

    monkeypatch.setattr(url_safety._DNS_RESOLVER, "getaddrinfo", unexpected_dns)
    user_id = uuid4()

    async def store_subscription(_session, *, user_id, identity, **_kwargs):
        source = FeedSource(
            id=uuid4(),
            kind=identity.kind,
            canonical_key=identity.canonical_key,
            canonical_url=identity.canonical_url,
            status=SourceStatus.PENDING,
        )
        subscription = SourceSubscription(id=uuid4(), user_id=user_id, source_id=source.id)
        return source, subscription, SourceSyncState(source_id=source.id, next_scan_at=None), True

    monkeypatch.setattr(sources, "subscribe_to_source", store_subscription)
    app = FastAPI()
    app.include_router(sources.router)
    app.dependency_overrides[require_current_user] = lambda: AuthenticatedUser(
        id=user_id, email=None, claims={}
    )
    app.dependency_overrides[get_session] = lambda: object()
    canonical = "https://[2606:4700:4700::1111]/feed"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(f"/sources/{kind}", json={"url": canonical})

    assert response.status_code == 201
    assert response.json()["canonical_url"] == canonical
