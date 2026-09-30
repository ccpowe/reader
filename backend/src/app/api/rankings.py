"""Authenticated, live ranking preview endpoints."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_current_user
from app.domain.enums import TranslationPurpose, TranslationStatus
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
from app.services.rankings import RankingItem, split_ranking_body
from app.storage.database import get_session
from app.translation.domain import TranslationProjection
from app.translation.projection import (
    TextInput,
    TranslationContext,
    get_translation_context,
    read_text_translations,
)

router = APIRouter(prefix="/rankings", tags=["rankings"])


class RankingBodySegmentResponse(BaseModel):
    segment_id: str
    text: str
    translated_text: str | None
    translation_status: str | None


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
    body_text: str | None = None
    body_segments: list[RankingBodySegmentResponse] = Field(default_factory=list)
    author_handle: str | None = None
    published_at: datetime | None = None
    retweet_count: int | None = None
    bookmark_count: int | None = None
    video_url: str | None = None
    video_thumbnail_url: str | None = None


class RankingResponse(BaseModel):
    kind: Literal["hacker_news", "reddit", "github", "techtwitter"]
    title: str
    subtitle: str
    effective_engine_fingerprint: str | None
    items: list[RankingItemResponse]
    fetched_at: datetime


def _to_response(
    kind: str,
    item: RankingItem,
    *,
    context: TranslationContext | None = None,
    projections: dict[str, TranslationProjection] | None = None,
) -> RankingItemResponse:
    key = _translation_key(kind, item)
    cached = projections or {}
    title_projection = cached.get(f"{key}:title") if kind != "techtwitter" else None
    description_projection = cached.get(f"{key}:description") if kind != "techtwitter" else None
    translated_title = _successful_translation(title_projection)
    translated_description = _successful_translation(description_projection)
    body_segments = [
        RankingBodySegmentResponse(
            segment_id=segment_id,
            text=text,
            translated_text=(translated := _successful_translation(cached.get(segment_id))),
            translation_status="succeeded" if translated else None,
        )
        for segment_id, text in body_segments_for_item(kind, item)
    ]
    translation_locale = (
        context.target_locale
        if context is not None
        and (
            translated_title
            or translated_description
            or any(segment.translated_text for segment in body_segments)
        )
        else None
    )
    return RankingItemResponse(
        **(
            item.__dict__
            | {
                "translation_key": key,
                "translated_title": translated_title,
                "title_translation_status": "succeeded" if translated_title else None,
                "translated_description": translated_description,
                "description_translation_status": "succeeded" if translated_description else None,
                "translation_locale": translation_locale,
                "body_segments": body_segments,
                "image_urls": list(item.image_urls),
            }
        )
    )


def body_segments_for_item(kind: str, item: RankingItem) -> list[tuple[str, str]]:
    """Partition the complete original by UTF-16 request size, never truncating it."""
    if kind != "techtwitter" or not item.body_text:
        return []
    prefix = f"{_translation_key(kind, item)}:body:"
    return [
        (f"{prefix}{index}", text) for index, text in enumerate(split_ranking_body(item.body_text))
    ]


@router.get("/{kind}", response_model=RankingResponse)
async def get_ranking(
    kind: Literal["hacker_news", "reddit", "github", "techtwitter"],
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
    """Return ranking originals together with any existing successful translations.

    The response identifies the effective translation engine. Cache misses stay
    empty so clients can render the original text and request only those missing
    segments through the interactive translation endpoint.
    """
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
        return await _ranking_response(
            session,
            _current_user.id,
            data,
            limit=limit,
        )
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


async def _ranking_response(
    session: AsyncSession,
    user_id: UUID,
    data: RankingData,
    *,
    limit: int,
) -> RankingResponse:
    items = data.items[:limit]
    context = await get_translation_context(session, user_id)
    projections: dict[str, TranslationProjection] = {}
    if context.can_translate:
        translation_inputs: list[TextInput] = []
        for item in items:
            key = _translation_key(data.kind, item)
            if data.kind == "techtwitter":
                translation_inputs.extend(
                    TextInput(
                        item_id=segment_id,
                        text=text,
                        purpose=TranslationPurpose.PARAGRAPH,
                    )
                    for segment_id, text in body_segments_for_item(data.kind, item)
                    if text.strip()
                )
                continue
            if item.title.strip():
                translation_inputs.append(
                    TextInput(
                        item_id=f"{key}:title",
                        text=item.title,
                        purpose=TranslationPurpose.RANKING_TITLE,
                    )
                )
            if item.description and item.description.strip():
                translation_inputs.append(
                    TextInput(
                        item_id=f"{key}:description",
                        text=item.description,
                        purpose=TranslationPurpose.RANKING_DESCRIPTION,
                    )
                )
        projections = await read_text_translations(
            session,
            translation_inputs,
            context=context,
        )
    return RankingResponse(
        kind=data.kind,
        title=data.title,
        subtitle=data.subtitle,
        effective_engine_fingerprint=(
            context.engine.fingerprint
            if context.can_translate and context.engine is not None
            else None
        ),
        items=[
            _to_response(data.kind, item, context=context, projections=projections)
            for item in items
        ],
        fetched_at=data.fetched_at,
    )


def _translation_key(kind: str, item: RankingItem) -> str:
    """Build a stable opaque key without coupling rankings to Content rows."""
    identity = item.native_id or item.url
    digest = hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()[:24]
    return f"ranking:{kind}:{digest}"


def _successful_translation(projection: TranslationProjection | None) -> str | None:
    if (
        projection is None
        or projection.status != TranslationStatus.SUCCEEDED
        or not projection.translated_text
    ):
        return None
    return projection.translated_text.strip() or None
