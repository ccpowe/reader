"""Compact translation convergence endpoints for loaded client content."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi import status as http_status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_current_user
from app.domain.enums import TranslationPurpose
from app.storage.database import get_session
from app.translation.projection import translation_response_values
from app.translation.quota import (
    TranslationDisabledForUser,
    TranslationQuotaError,
    TranslationQuotaStorageUnavailable,
)
from app.translation.requests import (
    SegmentInput,
    TranslationStorageBusy,
    resolve_accessible_titles,
    resolve_segments,
)

router = APIRouter(prefix="/translations", tags=["translation"])


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
    resolution = await resolve_accessible_titles(session, current_user.id, payload.content_ids)
    context = resolution.context
    projections = resolution.projections
    response: list[TitleTranslationResponse] = []
    for content_id_value in resolution.content_ids:
        content_id = str(content_id_value)
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
    state = request.app.state
    try:
        resolution = await resolve_segments(
            session,
            current_user.id,
            [
                SegmentInput(
                    segment_id=segment.segment_id,
                    text=segment.text,
                    purpose=segment.purpose,
                )
                for segment in payload.segments
            ],
            providers=getattr(state, "translation_providers", {}),
            coordinator=getattr(state, "realtime_translation_coordinator", None),
            quota=getattr(state, "translation_quota", None),
            session_factory=getattr(state, "session_factory", None),
            defer=background_tasks.add_task,
            request_id=request.headers.get("x-reader-request-id", ""),
        )
    except TranslationDisabledForUser as exc:
        raise _quota_http_exception(exc, status_code=http_status.HTTP_403_FORBIDDEN) from None
    except TranslationQuotaError as exc:
        raise _quota_http_exception(exc, status_code=429) from None
    except TranslationQuotaStorageUnavailable as exc:
        raise _quota_http_exception(exc, status_code=503) from None
    except TranslationStorageBusy:
        raise _translation_storage_busy_http_exception() from None
    context = resolution.context
    projections = resolution.projections
    response: list[TranslationSegmentResponse] = []
    for segment in payload.segments:
        projection = projections.get(segment.segment_id)
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
    return response


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
