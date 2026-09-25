"""Bounded X relationship normalization. Never persist upstream credentials/raw envelopes."""

from __future__ import annotations

from typing import Any, Literal

import regex
from pydantic import BaseModel


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _id(value: Any) -> str | None:
    value = str(value or "").strip()
    return value if value.isascii() and value.isdigit() else None


def _unwrap(value: Any) -> dict:
    node = _dict(value)
    for _ in range(5):
        nested = node.get("result") or node.get("tweet")
        if not isinstance(nested, dict):
            break
        node = nested
    return node


def _user(node: dict) -> dict:
    return _unwrap(_dict(node.get("core")).get("user_results"))


def _author(node: dict, fallback: dict | None = None) -> dict:
    user = _user(node) or _dict(fallback)
    legacy = _dict(user.get("legacy")) or user
    core = _dict(user.get("core"))
    return {
        "id": _id(user.get("rest_id") or user.get("id_str") or user.get("id")),
        "handle": legacy.get("screen_name") or core.get("screen_name"),
        "name": legacy.get("name") or core.get("name"),
        "avatar_url": legacy.get("profile_image_url_https") or legacy.get("profile_image_url"),
    }


def _text(node: dict) -> str | None:
    note = _dict(_dict(_dict(node.get("note_tweet")).get("note_tweet_results")).get("result"))
    legacy = _dict(node.get("legacy"))
    value = note.get("text") or legacy.get("full_text") or node.get("full_text") or node.get("text")
    return value.strip() if isinstance(value, str) else None


def _target(value: Any, target_id: Any) -> dict | None:
    node = _unwrap(value)
    tweet_id = _id(
        node.get("rest_id")
        or node.get("tweet_id")
        or node.get("id_str")
        or node.get("id")
        or target_id
    )
    if not tweet_id:
        return None
    author = _author(node, _dict(node.get("user")))
    author["handle"] = author["handle"] or node.get("username")
    text = _text(node)
    return {
        "tweet_id": tweet_id,
        "author": author,
        "text": text,
        "external_url": f"https://x.com/i/status/{tweet_id}",
        # A missing object is not evidence of deletion or account protection.
        "availability": "available" if text else "unavailable",
    }


def normalize_x_relations(record: dict) -> dict:
    node = _unwrap(record.get("raw"))
    legacy = _dict(node.get("legacy"))
    user = _dict(record.get("user"))
    author = _author(node, user)
    author["handle"] = author["handle"] or record.get("username")
    author["id"] = author["id"] or _id(legacy.get("user_id_str"))
    reply_id = _id(legacy.get("in_reply_to_status_id_str") or record.get("in_reply_to_status_id"))
    quote = _target(
        node.get("quoted_status_result")
        or legacy.get("quoted_status_result")
        or record.get("quoted_tweet")
        or record.get("quote"),
        legacy.get("quoted_status_id_str") or record.get("quoted_status_id"),
    )
    repost = _target(
        legacy.get("retweeted_status_result")
        or node.get("retweeted_status_result")
        or record.get("referenced_tweet"),
        legacy.get("retweeted_status_id_str"),
    )
    return {
        "version": 1,
        "tweet_id": _id(record.get("tweet_id") or record.get("id") or node.get("rest_id")),
        "author": author,
        "conversation_id": _id(legacy.get("conversation_id_str") or record.get("conversation_id")),
        "reply_to": {
            "tweet_id": reply_id,
            "author_id": _id(
                legacy.get("in_reply_to_user_id_str") or record.get("in_reply_to_user_id")
            ),
            "handle": legacy.get("in_reply_to_screen_name"),
        }
        if reply_id
        else None,
        "quote": quote,
        "repost": repost,
        "is_repost": bool(repost or record.get("is_retweet")),
        "completeness": "parsed" if legacy else "partial",
    }


class XAuthor(BaseModel):
    id: str | None = None
    handle: str | None = None
    name: str | None = None
    avatar_url: str | None = None


class XReferencePreview(BaseModel):
    tweet_id: str
    author: XAuthor
    text: str | None = None
    external_url: str
    availability: Literal["available", "unavailable"]


class XReplyPreview(BaseModel):
    tweet_id: str
    author_id: str | None = None
    handle: str | None = None


class XThreadPreview(BaseModel):
    loaded_count: int
    # The root among collected posts, not necessarily the complete thread's root.
    root_tweet_id: str | None
    root_url: str


class XPreview(BaseModel):
    tweet_id: str | None
    text: str
    author: XAuthor
    external_url: str
    completeness: Literal["parsed", "partial", "legacy"]
    reply_to: XReplyPreview | None = None
    quote: XReferencePreview | None = None
    repost: XReferencePreview | None = None
    is_repost: bool = False
    thread: XThreadPreview | None = None


def short_text(text: str, limit: int) -> str:
    text = " ".join(text.split())
    clusters = list(regex.finditer(r"\X", text))
    return text[: clusters[limit - 1].end()].rstrip() + "…" if len(clusters) > limit else text


def x_preview(entry: Any, content: Any, loaded_count: int = 1) -> XPreview:
    metadata = _dict(getattr(entry, "raw_metadata", None))
    data = _dict(metadata.get("x"))
    references = {}
    for kind in ("quote", "repost"):
        ref = data.get(kind)
        if isinstance(ref, dict):
            references[kind] = {
                **ref,
                "text": short_text(ref["text"], 80) if ref.get("text") else None,
            }
    return XPreview(
        tweet_id=data.get("tweet_id") or _id(entry.native_id),
        text=short_text(content.body_text or content.excerpt or content.title, 180),
        author=data.get("author") or {"handle": entry.author_name},
        external_url=entry.external_url,
        completeness=data.get("completeness", "legacy"),
        reply_to=data.get("reply_to"),
        is_repost=bool(data.get("is_repost") or metadata.get("is_retweet")),
        thread=XThreadPreview(
            loaded_count=loaded_count,
            root_tweet_id=data.get("tweet_id") or _id(entry.native_id),
            root_url=entry.external_url,
        )
        if loaded_count > 1
        else None,
        **references,
    )


def preserve_x_metadata(previous: dict, incoming: dict) -> dict:
    """A provider fallback without raw evidence must not erase known relationships."""
    old = _dict(previous.get("x"))
    new = _dict(incoming.get("x"))
    if old.get("completeness") == "parsed" and new.get("completeness") != "parsed":
        return {
            **incoming,
            "x": old,
            "is_reply": bool(old.get("reply_to") or incoming.get("is_reply")),
            "is_retweet": bool(old.get("is_repost") or incoming.get("is_retweet")),
        }
    return incoming
