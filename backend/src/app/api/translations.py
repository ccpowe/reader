"""Compact translation convergence endpoints for loaded client content."""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi import status as http_status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import exists, or_, select
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_current_user
from app.core.settings import get_settings
from app.domain.enums import TranslationPriority, TranslationPurpose, TranslationScope
from app.storage.database import get_session
from app.storage.models import Content, SourceEntry, SourceSubscription, UserSavedContent
from app.translation.interactive import persist_interactive_artifacts, resolve_interactive_texts
from app.translation.projection import (
    TextInput,
    TitleInput,
    get_title_translation_context,
    get_translation_context,
    project_titles,
    translation_response_values,
)
from app.translation.quota import (
    TranslationDisabledForUser,
    TranslationQuotaError,
    TranslationQuotaStorageUnavailable,
)
from app.translation.rich_text import uses_reader_format

router = APIRouter(prefix="/translations", tags=["translation"])
logger = logging.getLogger(__name__)


class ResolveTitleTranslationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_ids: list[UUID] = Field(min_length=1, max_length=100)


class TitleTranslationResponse(BaseModel):
    content_id: str
    translated_title: str | None
    translation_locale: str | None
    translation_status: str | None
    engine_id: str | None
    engine_label: str | None
    error_code: str | None
    error_retryable: bool | None
    retry_after_ms: int | None


class TranslationQuotaErrorDetail(BaseModel):
    """Safe, stable error detail returned for translation quota failures."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=120)
    error_code: str = Field(min_length=1, max_length=120)


class TranslationQuotaErrorResponse(BaseModel):
    """FastAPI's HTTPException envelope for quota failures."""

    model_config = ConfigDict(extra="forbid")

    detail: TranslationQuotaErrorDetail


_TRANSLATION_QUOTA_RESPONSES = {
    http_status.HTTP_403_FORBIDDEN: {
        "model": TranslationQuotaErrorResponse,
        "description": "Translation is disabled for this user.",
    },
    http_status.HTTP_429_TOO_MANY_REQUESTS: {
        "model": TranslationQuotaErrorResponse,
        "description": "The authenticated request or translation miss budget is exhausted.",
        "headers": {
            "Retry-After": {
                "description": "Seconds until the fixed UTC quota window resets.",
                "schema": {"type": "integer", "minimum": 1},
            }
        },
    },
    http_status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": TranslationQuotaErrorResponse,
        "description": "Translation storage or its durable quota store is unavailable.",
    },
}


_CLIENT_SEGMENT_PURPOSES = {
    TranslationPurpose.PARAGRAPH,
    TranslationPurpose.RANKING_TITLE,
    TranslationPurpose.RANKING_DESCRIPTION,
    TranslationPurpose.WEB_SEGMENT,
    TranslationPurpose.CAPTION,
}
_MAX_SEGMENT_CHARS = 8_000
_MAX_REQUEST_CHARS = 30_000


class TranslationSegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=_MAX_SEGMENT_CHARS)
    purpose: TranslationPurpose


class ResolveTranslationSegmentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments: list[TranslationSegmentRequest] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_segments(self) -> ResolveTranslationSegmentsRequest:
        identifiers = [segment.segment_id for segment in self.segments]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("segment_id values must be unique")
        if any(segment.purpose not in _CLIENT_SEGMENT_PURPOSES for segment in self.segments):
            raise ValueError("unsupported client translation purpose")
        if any(not segment.text.strip() for segment in self.segments):
            raise ValueError("segment text cannot be blank")
        if sum(len(segment.text) for segment in self.segments) > _MAX_REQUEST_CHARS:
            raise ValueError(f"segment text exceeds {_MAX_REQUEST_CHARS} characters")
        return self


class TranslationSegmentResponse(BaseModel):
    effective_engine_fingerprint: str | None = None
    cache_expires_at: datetime | None = None
    segment_id: str
    purpose: TranslationPurpose
    translated_text: str | None
    translation_locale: str | None
    translation_status: str | None
    engine_id: str | None
    engine_label: str | None
    error_code: str | None
    error_retryable: bool | None
    retry_after_ms: int | None


@router.post(
    "/titles",
    response_model=list[TitleTranslationResponse],
)
async def resolve_title_translations(
    payload: ResolveTitleTranslationsRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TitleTranslationResponse]:
    """Resolve only loaded, accessible titles so clients can converge cheaply."""
    requested_ids = list(dict.fromkeys(payload.content_ids))
    active_subscription = exists(
        select(SourceEntry.id)
        .join(
            SourceSubscription,
            SourceSubscription.source_id == SourceEntry.source_id,
        )
        .where(
            SourceEntry.content_id == Content.id,
            SourceSubscription.user_id == current_user.id,
            SourceSubscription.is_enabled.is_(True),
        )
    )
    saved = exists(
        select(UserSavedContent.id).where(
            UserSavedContent.content_id == Content.id,
            UserSavedContent.user_id == current_user.id,
        )
    )
    contents = list(
        await session.scalars(
            select(Content).where(
                Content.id.in_(requested_ids),
                or_(active_subscription, saved),
            )
        )
    )
    content_by_id = {content.id: content for content in contents}
    ordered_contents = [
        content_by_id[content_id] for content_id in requested_ids if content_id in content_by_id
    ]
    context = await get_title_translation_context(session, current_user.id)
    projections = await project_titles(
        session,
        [TitleInput(item_id=str(content.id), text=content.title) for content in ordered_contents],
        context=context,
    )
    await session.commit()
    response: list[TitleTranslationResponse] = []
    for content in ordered_contents:
        content_id = str(content.id)
        translated, locale, status = translation_response_values(
            context,
            projections.get(content_id),
        )
        response.append(
            TitleTranslationResponse(
                content_id=content_id,
                translated_title=translated,
                translation_locale=locale,
                translation_status=status,
                engine_id=context.engine.engine_id if context.engine is not None else None,
                engine_label=context.engine_label,
                error_code=(
                    projections[content_id].error_code if content_id in projections else None
                ),
                error_retryable=(
                    projections[content_id].error_retryable if content_id in projections else None
                ),
                retry_after_ms=(
                    projections[content_id].retry_after_ms if content_id in projections else None
                ),
            )
        )
    return response


@router.post(
    "/segments",
    response_model=list[TranslationSegmentResponse],
    responses=_TRANSLATION_QUOTA_RESPONSES,
)
async def resolve_translation_segments(
    payload: ResolveTranslationSegmentsRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TranslationSegmentResponse]:
    """Resolve bounded UI segments for ranking, reader, WebView, and captions.

    Web page text is user-scoped because a WebView may contain authenticated or
    otherwise private content. Public ranking, article, and caption text may
    safely reuse application-managed artifacts.
    """
    quota = getattr(request.app.state, "translation_quota", None)
    try:
        from app.storage.locks import acquire_user_transaction_lock
        from app.translation.lifecycle import (
            clear_user_ephemeral_cache,
            ephemeral_fingerprint,
        )

        await acquire_user_transaction_lock(
            session, namespace="translation-preference", user_id=current_user.id
        )
        context = await get_translation_context(session, current_user.id, fresh=True)
        await clear_user_ephemeral_cache(
            session,
            current_user.id,
            keep_fingerprint=ephemeral_fingerprint(context),
        )
        # End the request-scoped read transaction before any realtime quota,
        # shared-batch, or provider wait. Standard items may start a fresh
        # transaction later in the same session.
        await session.commit()
    except SQLAlchemyTimeoutError:
        raise _translation_storage_busy_http_exception() from None
    items = [
        TextInput(
            item_id=segment.segment_id,
            text=segment.text,
            purpose=segment.purpose,
            scope=(
                TranslationScope.USER
                if segment.purpose in {TranslationPurpose.WEB_SEGMENT, TranslationPurpose.CAPTION}
                else TranslationScope.SHARED
            ),
            owner_id=(
                current_user.id
                if segment.purpose in {TranslationPurpose.WEB_SEGMENT, TranslationPurpose.CAPTION}
                else None
            ),
            priority=(
                int(TranslationPriority.PREFETCH)
                if segment.purpose
                in {
                    TranslationPurpose.RANKING_TITLE,
                    TranslationPurpose.RANKING_DESCRIPTION,
                }
                else int(TranslationPriority.INTERACTIVE)
            ),
        )
        for segment in payload.segments
    ]
    providers = getattr(request.app.state, "translation_providers", {})
    provider = providers.get(context.engine.engine_id) if context.engine is not None else None
    settings = get_settings()
    realtime_items = [
        item
        for item in items
        if item.purpose in {TranslationPurpose.CAPTION, TranslationPurpose.WEB_SEGMENT}
        or uses_reader_format(item.purpose.value, item.text)
    ]
    standard_items = [item for item in items if item not in realtime_items]
    request_id = request.headers.get("x-reader-request-id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request_id):
        request_id = uuid4().hex
    for item in items:
        logger.info(
            "Translation request mapping: request_id=%s segment_id=%r format=reader-rich-text-v1",
            request_id,
            item.item_id,
        )
    projections = {}
    if realtime_items:
        coordinator = request.app.state.realtime_translation_coordinator
        try:
            projections.update(
                await coordinator.resolve(
                    realtime_items,
                    context=context,
                    provider=provider,
                    user_id=current_user.id,
                    wait_seconds=settings.translation_realtime_wait_seconds,
                )
            )
        except TranslationDisabledForUser as exc:
            raise _quota_http_exception(
                exc,
                status_code=http_status.HTTP_403_FORBIDDEN,
            ) from None
        except TranslationQuotaError as exc:
            raise _quota_http_exception(exc, status_code=429) from None
        except TranslationQuotaStorageUnavailable as exc:
            raise _quota_http_exception(exc, status_code=503) from None
        except SQLAlchemyTimeoutError:
            raise _translation_storage_busy_http_exception() from None
    interactive_options = {
        "timeout_seconds": settings.translation_interactive_timeout_seconds,
        "max_items_per_batch": settings.translation_interactive_max_items_per_batch,
        "max_chars_per_batch": settings.translation_interactive_max_chars_per_batch,
        "max_concurrency": settings.translation_interactive_max_concurrency,
    }
    if quota is not None and standard_items:
        interactive_options["reserve_budget"] = _reserve_translation_request_and_miss(
            quota,
            current_user.id,
        )
    if standard_items:
        try:
            resolution = await resolve_interactive_texts(
                session,
                standard_items,
                context=context,
                provider=provider,
                **interactive_options,
            )
        except TranslationDisabledForUser as exc:
            raise _quota_http_exception(exc, status_code=http_status.HTTP_403_FORBIDDEN) from None
        except TranslationQuotaError as exc:
            raise _quota_http_exception(exc, status_code=429) from None
        except TranslationQuotaStorageUnavailable as exc:
            raise _quota_http_exception(exc, status_code=503) from None
        except SQLAlchemyTimeoutError:
            raise _translation_storage_busy_http_exception() from None
        if resolution.persistence is not None:
            session_factory = getattr(request.app.state, "session_factory", None)
            if session_factory is not None:
                background_tasks.add_task(
                    persist_interactive_artifacts,
                    session_factory,
                    resolution.persistence,
                )
        projections.update(resolution.projections)
    response: list[TranslationSegmentResponse] = []
    # A preference update may commit while the provider is running. Reject its
    # old response as well as preventing stale artifact publication.
    latest_context = await get_translation_context(session, current_user.id, fresh=True)
    await session.commit()
    route_changed = (
        latest_context.cache_generation != context.cache_generation
        or latest_context.engine != context.engine
        or not latest_context.enabled
    )
    for segment in payload.segments:
        projection = projections.get(segment.segment_id)
        if route_changed and segment.purpose in {
            TranslationPurpose.WEB_SEGMENT,
            TranslationPurpose.CAPTION,
        }:
            from dataclasses import replace

            if projection is not None:
                projection = replace(
                    projection,
                    translated_text=None,
                    cache_expires_at=None,
                    status=None,
                    error_code="translation_route_changed",
                )
        translated, locale, status = translation_response_values(context, projection)
        response.append(
            TranslationSegmentResponse(
                effective_engine_fingerprint=context.engine.fingerprint
                if context.engine is not None
                else None,
                cache_expires_at=projection.cache_expires_at if projection is not None else None,
                segment_id=segment.segment_id,
                purpose=segment.purpose,
                translated_text=translated,
                translation_locale=locale,
                translation_status=status,
                engine_id=context.engine.engine_id if context.engine is not None else None,
                engine_label=context.engine_label,
                error_code=projection.error_code if projection is not None else None,
                error_retryable=(projection.error_retryable if projection is not None else None),
                retry_after_ms=projection.retry_after_ms if projection is not None else None,
            )
        )
    logger.info(
        "Translation segments resolved: purposes=%s items=%d characters=%d statuses=%s errors=%s",
        dict(Counter(segment.purpose.value for segment in payload.segments)),
        len(payload.segments),
        sum(len(segment.text) for segment in payload.segments),
        dict(Counter(item.translation_status or "disabled" for item in response)),
        dict(Counter(item.error_code for item in response if item.error_code)),
    )
    return response


def _reserve_translation_request_and_miss(quota, user_id: UUID):
    async def reserve(demands) -> object:
        actual_miss_chars = sum(
            len(demand.text) + len(demand.context_before or "") + len(demand.context_after or "")
            for demand in demands
        )
        return await quota.reserve(
            user_id,
            requests=1,
            actual_miss_chars=actual_miss_chars,
            provider_miss_chars=actual_miss_chars,
        )

    return reserve


def _translation_storage_busy_http_exception() -> HTTPException:
    """Map only SQLAlchemy pool checkout timeouts to a stable safe 503."""
    return HTTPException(
        status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "translation_storage_busy",
            "error_code": "translation_storage_busy",
        },
    )


def _quota_http_exception(exc: TranslationQuotaError, *, status_code: int) -> Exception:
    from fastapi import HTTPException

    headers = {}
    if status_code == 429:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "error_code": exc.code},
        headers=headers,
    )
