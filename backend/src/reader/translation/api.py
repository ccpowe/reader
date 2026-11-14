"""POST /translations and the translation preference."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from reader.accounts.auth import CurrentUser, current_user
from reader.core.db import get_session
from reader.core.errors import AppError, error_responses
from reader.core.server_access import document_server_token
from reader.core.settings import Settings, get_settings
from reader.models import TranslationPreference
from reader.translation.engines import catalog
from reader.translation.service import Translator
from reader.translation.titles import Preference, load_preference

translations_router = APIRouter(
    prefix="/translations", tags=["translation"], dependencies=[Depends(document_server_token)]
)
preference_router = APIRouter(
    prefix="/me/translation-preference",
    tags=["translation"],
    dependencies=[Depends(document_server_token)],
)

Purpose = Literal[
    "title", "paragraph", "board_title", "board_description", "web_segment", "caption"
]
Locale = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$", max_length=16)
]
_MAX_TEXTS = 100
_UNAVAILABLE = (503, "translation_unavailable")


async def get_translator(request: Request) -> Translator:
    """One translator per API process, created on first use.

    Async on purpose: it runs on the event loop with no await between the check and
    the assignment, so concurrent first requests cannot create two.
    """
    translator = getattr(request.app.state, "translator", None)
    if translator is None:
        factory = request.app.state.session_factory
        if factory is None:
            raise AppError(503, "database_unavailable", "The database is not configured.")
        translator = request.app.state.translator = Translator(factory, get_settings())
    return translator


# --- Preference -------------------------------------------------------------------


class EngineInfo(BaseModel):
    id: str
    label: str
    available: bool


class PreferenceResponse(BaseModel):
    target_locale: str
    # Translate titles in the feed automatically.
    enabled: bool
    engine_id: str
    # Results carry the fingerprint they were made with; discard ones that differ.
    engine_fingerprint: str
    engines: list[EngineInfo]


class UpdatePreferenceRequest(BaseModel):
    """Send only the fields to change; `engine_id: null` returns to the server default."""

    model_config = ConfigDict(extra="forbid")

    target_locale: Locale | None = None
    enabled: bool | None = None
    engine_id: str | None = Field(default=None, max_length=64)


def _preference_response(preference: Preference, settings: Settings) -> PreferenceResponse:
    locale, enabled, engine = preference.target_locale, preference.enabled, preference.engine
    if engine is None:
        raise AppError(503, "translation_unavailable", "No translation engine is configured.")
    return PreferenceResponse(
        target_locale=locale,
        enabled=enabled,
        engine_id=engine.id,
        engine_fingerprint=engine.fingerprint,
        engines=[
            EngineInfo(id=item.id, label=item.label, available=item.available)
            for item in catalog(settings)
        ],
    )


@preference_router.get(
    "", response_model=PreferenceResponse, responses=error_responses(_UNAVAILABLE)
)
async def get_preference(
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    return _preference_response(await load_preference(db, user.id, settings), settings)


@preference_router.patch(
    "",
    response_model=PreferenceResponse,
    responses=error_responses((422, "unknown_engine"), (422, "engine_unavailable"), _UNAVAILABLE),
)
async def update_preference(
    body: UpdatePreferenceRequest,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    row = await db.get(TranslationPreference, user.id)
    if row is None:
        row = TranslationPreference(
            user_id=user.id, target_locale=settings.translation_default_target_locale
        )
        db.add(row)
    if body.target_locale is not None:
        row.target_locale = body.target_locale
    if body.enabled is not None:
        row.enabled = body.enabled
    if "engine_id" in body.model_fields_set:
        if body.engine_id is None:
            row.engine_id = None
        else:
            engine = next((e for e in catalog(settings) if e.id == body.engine_id), None)
            if engine is None:
                raise AppError(422, "unknown_engine", "No such translation engine.")
            if not engine.available:
                raise AppError(422, "engine_unavailable", "This engine is not configured.")
            row.engine_id = engine.id
    await db.commit()
    return _preference_response(await load_preference(db, user.id, settings), settings)


# --- Translations -----------------------------------------------------------------


class TranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Purpose
    # In reading order; the model may use neighbours for context.
    # No NUL characters: PostgreSQL text cannot store them.
    texts: list[
        Annotated[str, StringConstraints(min_length=1, max_length=12_000, pattern=r"^[^\x00]*$")]
    ] = Field(min_length=1, max_length=_MAX_TEXTS)
    # Default: the preference's target language.
    target_locale: Locale | None = None


class TranslatedItem(BaseModel):
    # succeeded: `text` holds the translation; pending: ask again shortly;
    # failed: `error_code` says why, retry after `retry_after_seconds`.
    status: Literal["succeeded", "pending", "failed"]
    text: str | None = None
    error_code: str | None = None
    retry_after_seconds: float | None = None


class TranslateResponse(BaseModel):
    target_locale: str
    engine_id: str
    engine_fingerprint: str
    # Same order and length as the request's texts.
    items: list[TranslatedItem]


@translations_router.post(
    "", response_model=TranslateResponse, responses=error_responses(_UNAVAILABLE)
)
async def translate(
    body: TranslateRequest,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
    translator: Translator = Depends(get_translator),
):
    """Translate texts with your engine. Waits a few seconds; unfinished items are
    `pending` and the same request a little later returns them once done."""
    preference = await load_preference(db, user.id, settings)
    locale, engine = preference.target_locale, preference.engine
    # Release the connection before waiting on the model.
    await db.rollback()
    if engine is None or not engine.available:
        raise AppError(503, "translation_unavailable", "The translation engine is not configured.")
    target = body.target_locale or locale
    outcomes = await translator.translate(
        body.texts, body.purpose, target, engine, wait=settings.translation_wait_seconds
    )
    return TranslateResponse(
        target_locale=target,
        engine_id=engine.id,
        engine_fingerprint=engine.fingerprint,
        items=[
            TranslatedItem(
                status=outcome.status,
                text=outcome.text,
                error_code=outcome.error_code,
                retry_after_seconds=outcome.retry_after,
            )
            for outcome in outcomes
        ],
    )
