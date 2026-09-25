from __future__ import annotations

from profile_timeline import ProfileTimelineEngine
from Scweet.api_engine import ApiEngine


def _tweet(native_id: str, text: str) -> dict:
    return {
        "rest_id": native_id,
        "legacy": {"full_text": text, "created_at": "Fri Sep 25 03:53:26 +0000 2026"},
        "core": {"user_results": {"result": {"core": {"screen_name": "arena", "name": "Arena"}}}},
    }


def _item(tweet: dict) -> dict:
    return {"itemContent": {"tweet_results": {"result": tweet}}}


def _page(instructions: list[dict]) -> dict:
    return {
        "data": {"user": {"result": {"timeline": {"timeline": {"instructions": instructions}}}}}
    }


def test_profile_conversations_keep_posts_replies_media_and_quote_relationships() -> None:
    original = _tweet("101", "Agent Arena release")
    original["quoted_status_result"] = {"result": _tweet("90", "Quoted release")}
    original["legacy"]["extended_entities"] = {
        "media": [{"media_url_https": "https://pbs.twimg.com/release.jpg"}]
    }
    reply = _tweet("102", "Leaderboard details")
    reply["legacy"]["in_reply_to_status_id_str"] = "101"
    page = _page(
        [
            {
                "entries": [
                    {
                        "entryId": "tweet-100",
                        "content": _item(_tweet("100", "Standalone")),
                    },
                    {
                        "entryId": "profile-conversation-103",
                        "content": {
                            "entryType": "TimelineTimelineModule",
                            "items": [
                                {"item": _item(original)},
                                {
                                    "item": _item(
                                        {
                                            "__typename": "TweetWithVisibilityResults",
                                            "tweet": reply,
                                        }
                                    )
                                },
                            ],
                        },
                    },
                    {
                        "entryId": "cursor-bottom-page",
                        "content": {"cursorType": "Bottom", "value": "older"},
                    },
                ]
            }
        ]
    )
    engine = ProfileTimelineEngine.__new__(ProfileTimelineEngine)
    tweets, cursor = engine._extract_profile_tweets_and_cursor(page)
    assert [(tweet.tweet_id, tweet.text) for tweet in tweets] == [
        ("100", "Standalone"),
        ("101", "Agent Arena release"),
        ("102", "Leaderboard details"),
    ]
    assert cursor == "older"
    assert tweets[1].tweet_url == "https://x.com/arena/status/101"
    assert tweets[1].media.image_links == ["https://pbs.twimg.com/release.jpg"]
    assert tweets[1].embedded_text == "Quoted release"
    assert tweets[2].raw["tweet"]["legacy"]["in_reply_to_status_id_str"] == "101"
    # Expanding a conversation must not alter the response or leak into the
    # engine used by search, profile lookup, and follows.
    baseline, _ = ApiEngine.__new__(ApiEngine)._extract_profile_tweets_and_cursor(page)
    assert [tweet.tweet_id for tweet in baseline] == ["100"]


def test_module_only_page_and_appended_items_are_not_empty() -> None:
    page = _page(
        [
            {
                "type": "TimelinePinEntry",
                "entry": {
                    "entryId": "tweet-1",
                    "content": _item(_tweet("1", "Pinned")),
                },
            },
            {
                "type": "TimelineAddEntries",
                "entries": [
                    {
                        "entryId": "profile-conversation-201",
                        "content": {
                            "entryType": "TimelineTimelineModule",
                            "items": [{"item": _item(_tweet("201", "Thread root"))}],
                        },
                    }
                ],
            },
            {
                "type": "TimelineAddToModule",
                "moduleEntryId": "profile-conversation-201",
                "moduleItems": [{"item": _item(_tweet("202", "Thread continuation"))}],
            },
            {
                "type": "TimelineReplaceEntry",
                "entry": {
                    "entryId": "cursor-bottom-page",
                    "content": {"cursorType": "Bottom", "value": "next"},
                },
            },
        ]
    )
    tweets, cursor = ProfileTimelineEngine.__new__(
        ProfileTimelineEngine
    )._extract_profile_tweets_and_cursor(page)
    assert [tweet.tweet_id for tweet in tweets] == ["1", "201", "202"]
    assert cursor == "next"


def test_modules_ignore_promotions_recommendations_and_unavailable_items() -> None:
    promoted = _item(_tweet("301", "Ad"))
    promoted["itemContent"]["promotedMetadata"] = {"advertiser": "example"}
    page = _page(
        [
            {
                "entries": [
                    {
                        "entryId": "profile-conversation-300",
                        "content": {
                            "entryType": "TimelineTimelineModule",
                            "items": [
                                {"item": promoted},
                                {
                                    "item": {
                                        "itemContent": {
                                            "user_results": {"result": {"rest_id": "302"}}
                                        }
                                    }
                                },
                                {"item": _item({"__typename": "TweetTombstone"})},
                                {"item": _item(_tweet("303", "Visible reply"))},
                            ],
                        },
                    },
                    {"entryId": "tweet-301", "content": promoted},
                ]
            }
        ]
    )
    tweets, _ = ProfileTimelineEngine.__new__(
        ProfileTimelineEngine
    )._extract_profile_tweets_and_cursor(page)
    assert [(tweet.tweet_id, tweet.text) for tweet in tweets] == [("303", "Visible reply")]
