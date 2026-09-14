from types import SimpleNamespace

import httpx

from app.ingestion.sources.scweet_x import ScweetXSourceAdapter, ScweetXSourceConfig
from app.services.x_relations import normalize_x_relations, x_preview


def tweet(tweet_id="200", author_id="42", **legacy):
    return {
        "rest_id": tweet_id,
        "legacy": {"user_id_str": author_id, "full_text": "collected full text", **legacy},
        "core": {
            "user_results": {
                "result": {
                    "rest_id": author_id,
                    "legacy": {"screen_name": "writer", "name": "Writer"},
                }
            }
        },
    }


def test_nested_graphql_quote_and_reply_are_kept_without_recursing():
    quote = tweet("100", "7", in_reply_to_status_id_str="99", in_reply_to_user_id_str="8")
    quote["quoted_status_result"] = {"result": tweet("90")}
    node = tweet(
        conversation_id_str="190",
        in_reply_to_status_id_str="199",
        in_reply_to_user_id_str="42",
        quoted_status_id_str="100",
    )
    node["quoted_status_result"] = {"result": {"tweet": quote}}
    parsed = normalize_x_relations({"tweet_id": "200", "raw": {"result": node}})
    assert parsed["author"]["id"] == "42"
    assert parsed["reply_to"]["tweet_id"] == "199"
    assert parsed["conversation_id"] == "190"
    assert parsed["quote"]["author"]["id"] == "7"
    assert parsed["quote"]["text"] == "collected full text"
    assert "quote" not in parsed["quote"]
    assert parsed["completeness"] == "parsed"


def test_missing_quote_is_unavailable_not_deleted_and_repost_keeps_actor():
    node = tweet(quoted_status_id_str="100")
    node["legacy"]["retweeted_status_result"] = {"result": tweet("80", "8")}
    parsed = normalize_x_relations({"raw": node})
    assert parsed["quote"]["availability"] == "unavailable"
    assert parsed["quote"]["external_url"] == "https://x.com/i/status/100"
    assert parsed["is_repost"]
    assert parsed["author"]["id"] == "42"
    assert parsed["repost"]["author"]["id"] == "8"


def test_adapter_retains_long_full_text_and_stores_relations():
    adapter = ScweetXSourceAdapter(
        ScweetXSourceConfig("writer", "http://unused", "unused"), httpx.AsyncClient()
    )
    full_text = "Long tweet\n" * 100
    item = adapter._to_content(
        {
            "tweet_id": "200",
            "tweet_url": "https://x.com/writer/status/200",
            "text": full_text,
            "raw": tweet(in_reply_to_status_id_str="199"),
        }
    )
    assert item is not None
    assert item.excerpt_html.count("Long tweet") == 100
    assert len(item.title) == 180
    assert item.raw_metadata["is_reply"]
    assert "raw" not in item.raw_metadata


def test_projection_limits_text_and_distinguishes_collected_count_from_total():
    node = tweet(quoted_status_id_str="100")
    node["quoted_status_result"] = {"result": tweet("100", full_text="引" * 200)}
    entry = SimpleNamespace(
        raw_metadata={"x": normalize_x_relations({"raw": node})},
        native_id="200",
        external_url="https://x.com/writer/status/200",
        author_name="@writer",
    )
    content = SimpleNamespace(body_text="正" * 500, excerpt=None, title="title")
    preview = x_preview(entry, content, 3)
    assert preview.text == "正" * 180 + "…"
    assert preview.quote.text == "引" * 80 + "…"
    assert preview.thread.loaded_count == 3
    assert "total_count" not in preview.thread.model_dump()
    assert content.body_text == "正" * 500


def test_legacy_record_remains_independent_with_short_preview():
    entry = SimpleNamespace(
        raw_metadata={},
        native_id="200",
        external_url="https://x.com/i/status/200",
        author_name="@writer",
    )
    content = SimpleNamespace(body_text=None, excerpt="original", title="title")
    preview = x_preview(entry, content)
    assert preview.completeness == "legacy"
    assert preview.thread is None
    assert preview.text == "original"


def test_preview_does_not_split_combining_characters_or_emoji():
    from app.services.x_relations import short_text

    text = "👩‍👩‍👧‍👦é" * 100
    assert short_text(text, 3) == "👩‍👩‍👧‍👦é👩‍👩‍👧‍👦…"


def test_provider_fallback_does_not_erase_collected_relationships():
    from app.services.x_relations import preserve_x_metadata

    old = {"x": normalize_x_relations({"raw": tweet(in_reply_to_status_id_str="199")})}
    incoming = {"x": normalize_x_relations({"tweet_id": "200"}), "is_reply": False}
    merged = preserve_x_metadata(old, incoming)
    assert merged["x"]["reply_to"]["tweet_id"] == "199"
    assert merged["is_reply"]
