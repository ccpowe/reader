"""Reader's profile timeline traversal for the pinned Scweet engine."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from Scweet.api_engine import ApiEngine


class ProfileTimelineEngine(ApiEngine):
    """Expand conversation items before Scweet parses and counts page results."""

    @staticmethod
    def _extract_profile_timeline_instructions(
        data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        instructions = ApiEngine._extract_profile_timeline_instructions(data)
        entries: list[dict[str, Any]] = []
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            source_entries = instruction.get("entries") or []
            if isinstance(instruction.get("entry"), dict):
                source_entries = [*source_entries, instruction["entry"]]
            for entry in source_entries:
                if not isinstance(entry, dict):
                    continue
                content = entry.get("content") or {}
                if not isinstance(content, dict) or content.get("promotedMetadata"):
                    continue
                if content.get("entryType") == "TimelineTimelineModule":
                    entries.extend(_module_tweets(content.get("items") or []))
                else:
                    item_content = content.get("itemContent") or {}
                    if isinstance(item_content, dict) and item_content.get("promotedMetadata"):
                        continue
                    entries.append(entry)
            if instruction.get("type") == "TimelineAddToModule":
                entries.extend(_module_tweets(instruction.get("moduleItems") or []))
        return [{"type": "TimelineAddEntries", "entries": entries}]


def _module_tweets(items: list[Any]) -> Iterator[dict[str, Any]]:
    for item in items:
        if not isinstance(item, dict):
            continue
        wrapper = item.get("item") or {}
        if not isinstance(wrapper, dict):
            continue
        content = wrapper.get("itemContent") or {}
        if not isinstance(content, dict) or content.get("promotedMetadata"):
            continue
        result = (content.get("tweet_results") or {}).get("result")
        if not isinstance(result, dict):
            continue
        tweet = result.get("tweet", result)
        if not isinstance(tweet, dict):
            continue
        native_id = tweet.get("rest_id") or (tweet.get("legacy") or {}).get("id_str")
        if not native_id:
            continue
        # Only timeline items become entries; quoted/reposted tweets stay nested
        # in the original result for the existing relation normalizer.
        yield {"entryId": f"tweet-{native_id}", "content": {"itemContent": content}}
