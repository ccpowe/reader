"""Authenticated translation language and managed-engine preferences."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, require_current_user
from app.core.settings import Settings, get_settings
from app.domain.enums import TranslationProviderMode
from app.storage.database import get_session
from app.storage.locks import acquire_user_transaction_lock
from app.storage.models import TranslationPreference
from app.storage.profiles import ensure_profile
from app.translation.engines import (
    EngineDescriptor,
    default_engine_descriptor,
    engine_descriptor,
    managed_engine_catalog,
)
from app.translation.projection import (
    SUPPORTED_TRANSLATION_LOCALES,
    cache_translation_context,
)
from app.translation.store import cancel_stale_user_translation_work

router = APIRouter(prefix="/me", tags=["translation"])


class ManagedEngineResponse(BaseModel):
    engine_id: str
    label: str
    provider_name: str
    model_name: str
    available: bool
    unavailable_reason: str | None


class TranslationPreferenceResponse(BaseModel):
    target_locale: str
    provider_mode: TranslationProviderMode
    engine_id: str | None
    effective_engine_fingerprint: str | None = None
    effective_engine_id: str | None
    effective_engine_label: str | None
    effective_engine_available: bool
    engine_error_code: str | None
    provider_name: str | None
    model_name: str | None
    is_enabled: bool
    supported_locales: list[str]
    managed_engines: list[ManagedEngineResponse]


class UpdateTranslationPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_locale: str | None = Field(default=None, min_length=2, max_length=16)
    is_enabled: bool | None = None
    engine_id: str | None = Field(default=None, min_length=1, max_length=64)


def _selected_descriptor(
    settings: Settings,
    preference: TranslationPreference | None,
) -> EngineDescriptor | None:
    if preference is None or preference.provider_mode == TranslationProviderMode.APP_DEFAULT:
        return default_engine_descriptor(settings)
    if preference.provider_mode == TranslationProviderMode.APP_MANAGED and preference.engine_id:
        return engine_descriptor(settings, preference.engine_id)
    return None


def _to_response(preference: TranslationPreference | None) -> TranslationPreferenceResponse:
    settings = get_settings()
    descriptor = _selected_descriptor(settings, preference)
    enabled = preference.is_enabled if preference is not None else True
    if not enabled or descriptor is None or descriptor.available:
        engine_error_code = None if not enabled or descriptor is not None else "engine_unavailable"
    else:
        engine_error_code = descriptor.unavailable_reason or "engine_unavailable"
    return TranslationPreferenceResponse(
        target_locale=(
            preference.target_locale
            if preference is not None
            else settings.translation_default_target_locale
        ),
        provider_mode=(
            preference.provider_mode
            if preference is not None
            else TranslationProviderMode.APP_DEFAULT
        ),
        engine_id=(
            preference.engine_id
            if preference is not None
            and preference.provider_mode == TranslationProviderMode.APP_MANAGED
            else None
        ),
        effective_engine_fingerprint=descriptor.to_engine().fingerprint
        if descriptor is not None
        else None,
        effective_engine_id=descriptor.engine_id if descriptor is not None else None,
        effective_engine_label=descriptor.label if descriptor is not None else None,
        effective_engine_available=bool(descriptor is not None and descriptor.available),
        engine_error_code=engine_error_code,
        provider_name=descriptor.provider_name if descriptor is not None else None,
        model_name=descriptor.model_name if descriptor is not None else None,
        is_enabled=enabled,
        supported_locales=list(SUPPORTED_TRANSLATION_LOCALES),
        managed_engines=[
            ManagedEngineResponse(
                engine_id=item.engine_id,
                label=item.label,
                provider_name=item.provider_name,
                model_name=item.model_name,
                available=item.available,
                unavailable_reason=item.unavailable_reason,
            )
            for item in managed_engine_catalog(settings)
            if item.selectable
        ],
    )


@router.get("/translation-preference", response_model=TranslationPreferenceResponse)
async def get_translation_preference(
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> TranslationPreferenceResponse:
    preference = await session.get(TranslationPreference, current_user.id)
    cache_translation_context(current_user.id, preference)
    return _to_response(preference)


@router.patch("/translation-preference", response_model=TranslationPreferenceResponse)
async def update_translation_preference(
    payload: UpdateTranslationPreferenceRequest,
    current_user: AuthenticatedUser = Depends(require_current_user),
    session: AsyncSession = Depends(get_session),
) -> TranslationPreferenceResponse:
    settings = get_settings()
    target_locale = payload.target_locale.strip() if payload.target_locale else None
    if target_locale is not None and target_locale not in SUPPORTED_TRANSLATION_LOCALES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported translation locale: {target_locale}.",
        )

    engine_was_supplied = "engine_id" in payload.model_fields_set
    selected_descriptor: EngineDescriptor | None = None
    if engine_was_supplied and payload.engine_id is not None:
        selected_descriptor = engine_descriptor(settings, payload.engine_id)
        if selected_descriptor is None or not selected_descriptor.selectable:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown managed translation engine: {payload.engine_id}.",
            )
        if not selected_descriptor.available:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Translation engine {selected_descriptor.label} is unavailable: "
                    f"{selected_descriptor.unavailable_reason or 'unknown_reason'}."
                ),
            )

    await acquire_user_transaction_lock(
        session,
        namespace="translation-preference",
        user_id=current_user.id,
    )
    await ensure_profile(session, current_user.id)
    preference = await session.get(TranslationPreference, current_user.id)
    if preference is None:
        preference = TranslationPreference(
            user_id=current_user.id,
            target_locale=target_locale or settings.translation_default_target_locale,
            provider_mode=TranslationProviderMode.APP_DEFAULT,
            is_enabled=payload.is_enabled if payload.is_enabled is not None else True,
        )
        session.add(preference)
    else:
        if target_locale is not None:
            preference.target_locale = target_locale
        if payload.is_enabled is not None:
            preference.is_enabled = payload.is_enabled

    if engine_was_supplied:
        if selected_descriptor is None:
            preference.provider_mode = TranslationProviderMode.APP_DEFAULT
            preference.engine_id = None
            preference.provider_name = None
            preference.model_name = None
        else:
            preference.provider_mode = TranslationProviderMode.APP_MANAGED
            preference.engine_id = selected_descriptor.engine_id
            preference.provider_name = selected_descriptor.provider_name
            preference.model_name = selected_descriptor.model_name

    if engine_was_supplied or target_locale is not None or payload.is_enabled is False:
        from app.translation.lifecycle import clear_user_ephemeral_cache

        await clear_user_ephemeral_cache(session, current_user.id)
        effective = _selected_descriptor(settings, preference)
        await cancel_stale_user_translation_work(
            session,
            owner_id=current_user.id,
            keep_engine_id=(
                effective.engine_id if preference.is_enabled and effective is not None else None
            ),
            keep_target_locale=preference.target_locale if preference.is_enabled else None,
        )

    await session.commit()
    await session.refresh(preference)
    cache_translation_context(current_user.id, preference)
    return _to_response(preference)
