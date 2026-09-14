"""Private HTTP boundary around the locally maintained Scweet fork.

This service is intentionally tiny: it is for the reader backend only and
must be bound to a private Docker network or localhost, never exposed as a
public scraping API.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field
from Scweet import Scweet, ScweetConfig

logger = logging.getLogger(__name__)

SEARCH_FALLBACK_DAYS = 45


class UnverifiedEmptyTimelineError(RuntimeError):
    """Neither timeline data nor a valid public profile could be confirmed."""


class ProfileTweetsRequest(BaseModel):
    usernames: list[str] = Field(min_length=1, max_length=10)
    limit: int = Field(default=10, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=2048)
    boundary_ids: list[str] = Field(default_factory=list, max_length=50)


class ProfileTweetsResponse(BaseModel):
    items: list[dict[str, Any]]
    continuation: str | None
    completed: bool
    limit_reached: str | None
    page_count: int
    newest_id: str | None
    oldest_id: str | None
    boundary_ids: list[str]


class ProfileInfoRequest(BaseModel):
    usernames: list[str] = Field(min_length=1, max_length=10)


class ProfileInfoResponse(BaseModel):
    items: list[dict[str, Any]]


class ScweetRuntime:
    """One serial client: a single account must not run concurrent jobs."""

    def __init__(self) -> None:
        cookie_file = os.environ.get("SCWEET_COOKIES_FILE", "/run/secrets/scweet-cookies.json")
        state_db = os.environ.get("SCWEET_STATE_DB", "/var/lib/scweet/scweet_state.db")
        manifest_ttl = int(os.environ.get("SCWEET_MANIFEST_TTL_SECONDS", "86400"))
        self._client = Scweet(
            cookies_file=cookie_file,
            db_path=state_db,
            manifest_scrape_on_init=True,
            config=ScweetConfig(manifest_ttl_s=manifest_ttl),
        )
        self._lock = asyncio.Lock()

    async def get_profile_tweets(self, usernames: list[str], limit: int) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._get_profile_tweets, usernames, limit)

    def _get_profile_tweets(self, usernames: list[str], limit: int) -> list[dict[str, Any]]:
        fetched = self._client.get_profile_tweets(usernames, limit=limit, save=False)
        if fetched:
            return fetched

        # Some X profile timelines currently return cursor-only module pages
        # that Scweet cannot parse. SearchTimeline uses a different GraphQL
        # shape and is a bounded recovery path for recent source ingestion.
        today = datetime.now(UTC).date()
        fallback = self._client.search(
            "",
            from_users=usernames,
            since=(today - timedelta(days=SEARCH_FALLBACK_DAYS)).isoformat(),
            until=(today + timedelta(days=1)).isoformat(),
            display_type="Latest",
            limit=limit,
            max_empty_pages=3,
            save=False,
        )
        requested = {username.removeprefix("@").casefold() for username in usernames}
        matching = [item for item in fallback if _tweet_author(item).casefold() in requested]
        normalized = _dedupe_and_sort_tweets(matching)[:limit]
        if normalized:
            logger.warning(
                "Scweet profile timeline was empty; SearchTimeline fallback recovered %s items",
                len(normalized),
            )
            return normalized
        profiles = self._client.get_user_info(usernames, save=False)
        if profiles:
            logger.info("Scweet confirmed an empty recent timeline through profile lookup")
            return []
        raise UnverifiedEmptyTimelineError(
            "Scweet returned an empty timeline and could not confirm the requested profile."
        )

    async def get_user_info(self, usernames: list[str]) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._client.get_user_info, usernames, save=False)


def _require_internal_token(request: Request) -> None:
    expected = os.environ.get("SCWEET_SERVICE_TOKEN", "")
    provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token."
        )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Initialization validates cookies and refreshes the cached manifest once.
    # A startup error is deliberate: an unhealthy collector must not pretend to
    # be ready and cause opaque sync failures in the main backend.
    app.state.runtime = ScweetRuntime()
    logger.info("Scweet service initialized")
    yield


app = FastAPI(
    title="Reader internal Scweet service", docs_url=None, redoc_url=None, lifespan=_lifespan
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/v1/profiles/tweets",
    response_model=ProfileTweetsResponse,
    dependencies=[Depends(_require_internal_token)],
)
async def profile_tweets(payload: ProfileTweetsRequest, request: Request) -> ProfileTweetsResponse:
    runtime: ScweetRuntime = request.app.state.runtime
    offset = _decode_cursor(payload.cursor)
    requested_total = min(offset + payload.limit, 120)
    try:
        fetched = await runtime.get_profile_tweets(payload.usernames, requested_total)
    except Exception as exc:
        logger.warning("Scweet profile fetch failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": _collector_error_code(exc),
                "message": (
                    "Scweet profile fetch failed; check collector logs and account credentials."
                ),
            },
        ) from exc
    organic = [item for item in fetched if _is_organic_timeline_item(item)]
    resume_offset = offset
    if offset:
        matched_offset = _resume_after_boundary(organic, payload.boundary_ids)
        if matched_offset is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "cursor_invalid",
                    "message": "Timeline shifted before the previous boundary could be verified.",
                },
            )
        # Resume by stable identity, not by the stale numeric offset.  Head
        # insertions can move this forward; deletions can move it backward.
        resume_offset = matched_offset
    items = organic[resume_offset : resume_offset + payload.limit]
    next_offset = resume_offset + len(items)
    # Filtering an ad/recommendation must not be mistaken for timeline
    # exhaustion; the collector's unfiltered result count is the evidence.
    upstream_exhausted = len(fetched) < requested_total
    collector_window_reached = next_offset >= 120 and requested_total == 120
    completed = upstream_exhausted or collector_window_reached
    if not items and not completed:
        # A shifted boundary can land exactly at the end of the fetched
        # prefix. Advance the cumulative request window instead of emitting a
        # cursor that would replay the same empty slice forever.
        next_offset = requested_total
        collector_window_reached = next_offset >= 120
        completed = collector_window_reached
    ids = [_tweet_id(item) for item in items if _tweet_id(item)]
    return ProfileTweetsResponse(
        items=items,
        continuation=None if completed else _encode_cursor(next_offset),
        completed=completed,
        limit_reached=(
            "collector_window" if collector_window_reached else None if completed else "page_size"
        ),
        page_count=1,
        newest_id=ids[0] if ids else None,
        oldest_id=ids[-1] if ids else None,
        boundary_ids=ids or payload.boundary_ids,
    )


@app.post(
    "/v1/profiles/info",
    response_model=ProfileInfoResponse,
    dependencies=[Depends(_require_internal_token)],
)
async def profile_info(payload: ProfileInfoRequest, request: Request) -> ProfileInfoResponse:
    """Read public account metadata when a timeline has no usable tweet author."""
    runtime: ScweetRuntime = request.app.state.runtime
    try:
        items = await runtime.get_user_info(payload.usernames)
    except Exception as exc:
        logger.warning("Scweet profile lookup failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Scweet profile lookup failed; check collector logs and account credentials.",
        ) from exc
    return ProfileInfoResponse(items=items)


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        offset = int(payload["offset"])
    except (binascii.Error, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "cursor_invalid", "message": "Invalid Scweet continuation."},
        ) from exc
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "cursor_invalid", "message": "Invalid Scweet continuation."},
        )
    return offset


def _resume_after_boundary(fetched: list[dict[str, Any]], expected_ids: list[str]) -> int | None:
    """Return the position after the oldest surviving prior-page tweet."""
    positions = {
        native_id: index for index, item in enumerate(fetched) if (native_id := _tweet_id(item))
    }
    for native_id in reversed(expected_ids):
        if native_id in positions:
            return positions[native_id] + 1
    return None


def _tweet_id(item: dict[str, Any]) -> str:
    return str(item.get("tweet_id") or item.get("id") or "").strip()


def _tweet_author(item: dict[str, Any]) -> str:
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    return str(user.get("screen_name") or item.get("username") or "").removeprefix("@").strip()


def _dedupe_and_sort_tweets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        native_id = _tweet_id(item)
        if native_id and native_id not in unique:
            unique[native_id] = item
    return sorted(
        unique.values(),
        key=lambda item: int(_tweet_id(item)) if _tweet_id(item).isdigit() else -1,
        reverse=True,
    )


def _is_organic_timeline_item(item: Any) -> bool:
    if not isinstance(item, dict) or not _tweet_id(item):
        return False
    event = str(item.get("timeline_event") or item.get("type") or "tweet").lower()
    if event in {"ad", "advertisement", "promoted", "recommendation", "who_to_follow"}:
        return False
    return not bool(item.get("promoted") or item.get("is_ad") or item.get("is_promoted"))


def _collector_error_code(exc: Exception) -> str:
    if isinstance(exc, UnverifiedEmptyTimelineError):
        return "timeline_empty_unverified"
    message = f"{type(exc).__name__} {exc}".lower()
    if "cookie" in message or "auth" in message or "login" in message:
        return "auth_failed"
    if "manifest" in message or "graphql" in message:
        return "manifest_failed"
    if "protected" in message or "private" in message:
        return "protected_account"
    if (
        "rate" in message
        or "429" in message
        or "accountpoolexhausted" in message
        or "no eligible account" in message
    ):
        return "rate_limited"
    return "collector_failed"
