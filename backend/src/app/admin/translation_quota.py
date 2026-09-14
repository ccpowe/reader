"""Production repository/service for per-user translation quota overrides."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.storage.models import (
    Profile,
    TranslationQuotaAudit,
    TranslationQuotaBucket,
    TranslationQuotaOverride,
)

_SECRET_PATTERN = re.compile(
    r"(?i)(?:bearer\s+)?(?:sb_publishable_|sb_secret_|service_role|secret|token|jwt)[A-Za-z0-9._~+/=-]*"
)
_ACTOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:+-]{0,159}$")
_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+){0,7}$")
_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_UNSAFE_METADATA_PATTERN = re.compile(
    r"(?i)(?:://|bearer|jwt|(?:^|[^a-z])(?:sk-|sb_))"
)


class TranslationQuotaAdminError(ValueError):
    """A safe operator input/data error."""


class TranslationQuotaAdminService:
    """Use the same ORM tables and transactions as the API quota service."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def show(self, user_id: UUID) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            override = await session.get(TranslationQuotaOverride, user_id)
            usage = await _usage_rows(session, user_id)
            return _override_payload(override, usage)

    async def list(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            statement = select(TranslationQuotaOverride).order_by(
                TranslationQuotaOverride.updated_at.desc()
            )
            if not include_disabled:
                statement = statement.where(TranslationQuotaOverride.is_disabled.is_(False))
            rows = list(await session.scalars(statement))
            return [_override_payload(row, []) for row in rows]

    async def set(
        self,
        user_id: UUID,
        *,
        requests: int | None,
        actual_miss_chars: int | None,
        actor: str,
        reason: str,
        reference: str | None = None,
    ) -> dict[str, Any]:
        if requests is None and actual_miss_chars is None:
            raise TranslationQuotaAdminError(
                "set requires --requests and/or --actual-miss-chars; use delete to clear"
            )
        actor, reason, reference = _validate_mutation_metadata(actor, reason, reference)
        return await self._mutate(
            user_id,
            action="set",
            actor=actor,
            reason=reason,
            reference=reference,
            requests=requests,
            actual_miss_chars=actual_miss_chars,
        )

    async def disable(
        self,
        user_id: UUID,
        *,
        actor: str,
        reason: str,
        reference: str | None = None,
    ) -> dict[str, Any]:
        actor, reason, reference = _validate_mutation_metadata(actor, reason, reference)
        return await self._mutate(
            user_id,
            action="disable",
            actor=actor,
            reason=reason,
            reference=reference,
            is_disabled=True,
        )

    async def enable(
        self,
        user_id: UUID,
        *,
        actor: str,
        reason: str,
        reference: str | None = None,
    ) -> dict[str, Any]:
        actor, reason, reference = _validate_mutation_metadata(actor, reason, reference)
        return await self._mutate(
            user_id,
            action="enable",
            actor=actor,
            reason=reason,
            reference=reference,
            is_disabled=False,
        )

    async def delete(
        self,
        user_id: UUID,
        *,
        actor: str,
        reason: str,
        reference: str | None = None,
    ) -> dict[str, Any]:
        actor, reason, reference = _validate_mutation_metadata(actor, reason, reference)
        async with self.session_factory() as session:
            try:
                await _require_profile(session, user_id)
                override = await session.get(
                    TranslationQuotaOverride,
                    user_id,
                    with_for_update=True,
                )
                before_state = _override_state(override)
                deleted = override is not None
                await _append_audit(
                    session,
                    user_id=user_id,
                    action="delete",
                    actor=actor,
                    reason=reason,
                    reference=reference,
                    before_state=before_state,
                    after_state=None,
                    override=override,
                )
                if override is not None:
                    await session.delete(override)
                result = {"user_id": str(user_id), "deleted": deleted}
                await session.commit()
                return result
            except BaseException:
                await session.rollback()
                raise

    async def _mutate(
        self,
        user_id: UUID,
        *,
        action: str,
        actor: str,
        reason: str,
        reference: str | None,
        **changes,
    ):
        async with self.session_factory() as session:
            try:
                await _require_profile(session, user_id)
                override, created = await _lock_or_create_override(session, user_id)
                before_state = None if created else _override_state(override)
                for field, value in changes.items():
                    if value is not None:
                        setattr(override, field, value)
                after_state = _override_state(override)
                await _append_audit(
                    session,
                    user_id=user_id,
                    action=action,
                    actor=actor,
                    reason=reason,
                    reference=reference,
                    before_state=before_state,
                    after_state=after_state,
                    override=override,
                )
                # Flush while the ORM instance is live so server defaults and
                # on-update timestamps are captured before commit expires it.
                await session.flush()
                await session.refresh(override)
                result = _override_payload(override, [])
                await session.commit()
                return result
            except BaseException:
                await session.rollback()
                raise


async def _require_profile(session, user_id: UUID) -> None:
    if await session.scalar(select(Profile.id).where(Profile.id == user_id)) is None:
        raise TranslationQuotaAdminError("Reader user profile not found")


async def _lock_or_create_override(session, user_id: UUID) -> tuple[TranslationQuotaOverride, bool]:
    """Create once, then lock the row for the whole mutation transaction."""
    result = await session.execute(
        pg_insert(TranslationQuotaOverride)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=[TranslationQuotaOverride.user_id])
        .returning(TranslationQuotaOverride.user_id)
    )
    created = result.scalar_one_or_none() is not None
    override = await session.get(TranslationQuotaOverride, user_id, with_for_update=True)
    if override is None:
        raise TranslationQuotaAdminError("Translation quota override could not be locked")
    return override, created


async def _usage_rows(session, user_id: UUID) -> list[dict[str, Any]]:
    window_start = _window_start(datetime.now(UTC))
    rows = list(
        await session.scalars(
            select(TranslationQuotaBucket)
            .where(
                (TranslationQuotaBucket.user_id == user_id)
                | (TranslationQuotaBucket.bucket_key == "global"),
                TranslationQuotaBucket.window_started_at == window_start,
            )
            .order_by(TranslationQuotaBucket.bucket_key)
        )
    )
    return [
        {
            "bucket": row.bucket_key,
            "window_started_at": row.window_started_at.isoformat(),
            "requests_used": row.requests_used,
            "actual_miss_chars_used": row.actual_miss_chars_used,
        }
        for row in rows
    ]


def _override_payload(override, usage: list[dict[str, Any]]) -> dict[str, Any] | None:
    if override is None:
        return None
    return {
        "user_id": str(override.user_id),
        "requests": override.requests,
        "actual_miss_chars": override.actual_miss_chars,
        "is_disabled": override.is_disabled,
        "created_at": _isoformat(override.created_at),
        "updated_at": _isoformat(override.updated_at),
        "usage": usage,
    }


def _override_state(override) -> dict[str, int | bool | None] | None:
    if override is None:
        return None
    return {
        "requests": override.requests,
        "actual_miss_chars": override.actual_miss_chars,
        "is_disabled": override.is_disabled,
    }


async def _append_audit(
    session,
    *,
    user_id: UUID,
    action: str,
    actor: str,
    reason: str,
    reference: str | None,
    before_state: dict[str, int | bool | None] | None,
    after_state: dict[str, int | bool | None] | None,
    override: TranslationQuotaOverride | None,
) -> None:
    session.add(
        TranslationQuotaAudit(
            id=uuid4(),
            user_id=user_id,
            actor=_redact_audit_text(actor, max_length=160),
            reason=_redact_audit_text(reason, max_length=500),
            reference=_redact_audit_text(reference, max_length=128) if reference else None,
            action=action,
            before_state=before_state,
            after_state=after_state,
            requests=override.requests if override is not None else None,
            actual_miss_chars=(override.actual_miss_chars if override is not None else None),
            is_disabled=override.is_disabled if override is not None else None,
        )
    )


def _validate_mutation_metadata(
    actor: str,
    reason: str,
    reference: str | None = None,
) -> tuple[str, str, str | None]:
    actor_value = actor.strip() if isinstance(actor, str) else ""
    reason_value = reason.strip() if isinstance(reason, str) else ""
    reference_value = reference.strip() if isinstance(reference, str) else None
    if not actor_value:
        raise TranslationQuotaAdminError("--actor is required")
    if not reason_value:
        raise TranslationQuotaAdminError("--reason is required")
    if not _ACTOR_PATTERN.fullmatch(actor_value) or _UNSAFE_METADATA_PATTERN.search(actor_value):
        raise TranslationQuotaAdminError("--actor must be a safe identifier")
    if not _REASON_PATTERN.fullmatch(reason_value) or _UNSAFE_METADATA_PATTERN.search(reason_value):
        raise TranslationQuotaAdminError("--reason must be a controlled code")
    if reference_value is not None and (
        not reference_value
        or not _REFERENCE_PATTERN.fullmatch(reference_value)
        or _UNSAFE_METADATA_PATTERN.search(reference_value)
    ):
        raise TranslationQuotaAdminError("--reference must be a safe identifier")
    return actor_value, reason_value, reference_value


def _redact_audit_text(value: str, *, max_length: int) -> str:
    text = _SECRET_PATTERN.sub("[REDACTED]", value.strip())
    return text[:max_length]


def _window_start(value: datetime) -> datetime:
    timestamp = int(value.timestamp()) // 60 * 60
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = ["TranslationQuotaAdminError", "TranslationQuotaAdminService"]
