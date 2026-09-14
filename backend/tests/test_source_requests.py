import pytest
from pydantic import ValidationError

from app.api.sources import (
    AddRedditSourceRequest,
    AddRssSourceRequest,
    UpdateSourceSubscriptionRequest,
)


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
