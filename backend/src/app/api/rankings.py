"""Authenticated, live ranking preview endpoints."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_current_user
from app.ingestion.source_identity import MAX_RAW_SUBREDDIT_LENGTH, canonical_reddit_identity
from app.services.ranking_snapshots import (
    RankingData,
    RankingRefreshInProgress,
    RankingRefreshRateLimited,
    RankingRequest,
    create_or_refresh_snapshot,
    get_snapshot,
    user_has_active_reddit_subscription,
)
from app.services.rankings import RankingItem
from app.storage.database import get_session

router = APIRouter(prefix="/rankings", tags=["rankings"])


class RankingItemResponse(BaseModel):
    translation_key: str
    rank: int
    title: str
    translated_title: str | None
    title_translation_status: str | None
    url: str
    source_label: str | None
    description: str | None
    translated_description: str | None
    description_translation_status: str | None
    translation_locale: str | None
    author: str | None
    score: int | None
    comments: int | None
    language: str | None
    stars: int | None
    forks: int | None
    stars_this_period: int | None
    image_urls: list[str]


class RankingResponse(BaseModel):
    kind: Literal["hacker_news", "reddit", "github"]
    title: str
    subtitle: str
    items: list[RankingItemResponse]
    fetched_at: datetime


def _to_response(
    kind: str,
    item: RankingItem,
) -> RankingItemResponse:
    key = _translation_key(kind, item)
    return RankingItemResponse(
        **(
            item.__dict__
            | {
                "translation_key": key,
                # Ranking delivery never waits on translation persistence. The
                # client requests only its visible window after original rows
                # are already on screen, then merges the compact projections.
                "translated_title": None,
                "title_translation_status": None,
                "translated_description": None,
                "description_translation_status": None,
                "translation_locale": None,
                "image_urls": list(item.image_urls),
            }
        )
    )


@router.get("/{kind}", response_model=RankingResponse)
async def get_ranking(
    kind: Literal["hacker_news", "reddit", "github"],
    limit: int = Query(default=100, ge=1, le=100),
    refresh: bool = Query(default=False),
    subreddit: str = Query(
        default="MachineLearning", min_length=2, max_length=MAX_RAW_SUBREDDIT_LENGTH
    ),
    sort: Literal["hot", "rising", "top"] = Query(default="hot"),
    time_filter: Literal["day", "week", "month", "year"] = Query(default="week"),
    _current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> RankingResponse:
    if kind == "reddit":
        try:
            identity = canonical_reddit_identity(subreddit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        subreddit = identity.canonical_key.removeprefix("reddit:")
        if not await user_has_active_reddit_subscription(session, _current_user.id, subreddit):
            raise HTTPException(
                status_code=403,
                detail="Subscribe to this subreddit before opening its ranking.",
            )
    request = RankingRequest(
        kind=kind,
        subreddit=subreddit.removeprefix("r/"),
        sort=sort,
        time_filter=time_filter,
    )
    try:
        if refresh:
            data = await create_or_refresh_snapshot(session, request, force=True)
        else:
            data = await get_snapshot(session, request)
            if data is None:
                data = await create_or_refresh_snapshot(session, request)
        return _ranking_response(data, limit=limit)
    except RankingRefreshRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail="该榜单刚刚刷新过，请稍后再试。",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except RankingRefreshInProgress as exc:
        raise HTTPException(
            status_code=503,
            detail="榜单正在刷新，请稍后重试。",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="该公开榜单暂时限制了请求，请稍后再试。",
            ) from exc
        raise HTTPException(status_code=502, detail=f"Unable to load {kind} ranking.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to load {kind} ranking.") from exc


def _ranking_response(
    data: RankingData,
    *,
    limit: int,
) -> RankingResponse:
    return RankingResponse(
        kind=data.kind,
        title=data.title,
        subtitle=data.subtitle,
        items=[_to_response(data.kind, item) for item in data.items[:limit]],
        fetched_at=data.fetched_at,
    )


def _translation_key(kind: str, item: RankingItem) -> str:
    """Build a stable opaque key without coupling rankings to Content rows."""
    identity = item.native_id or item.url
    digest = hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()[:24]
    return f"ranking:{kind}:{digest}"
