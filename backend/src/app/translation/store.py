"""PostgreSQL implementation of durable Translation Work and Artifacts.

This module owns identity predicates, set-based demand insertion, leases, and
atomic completion. Callers never coordinate work and artifact rows themselves.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import (
    and_,
    func,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import TranslationPurpose, TranslationScope, TranslationStatus
from app.storage.models import TranslationArtifact, TranslationWork
from app.translation.domain import (
    TranslationDemand,
    TranslationEngine,
    TranslationIdentity,
    normalize_source_text,
)

WORK_NOTIFY_CHANNEL = "reader_translation_work_v2"
ARTIFACT_NOTIFY_CHANNEL = "reader_translation_artifact_v2"


@dataclass(frozen=True, slots=True)
class WorkOutcome:
    translated_text: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    retry_after_seconds: float | None = None
    provider_latency_ms: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.translated_text is not None


@dataclass(frozen=True, slots=True)
class StoredTranslationState:
    status: TranslationStatus
    is_artifact: bool
    translated_text: str | None = None
    cache_expires_at: datetime | None = None
    error_code: str | None = None
    error_retryable: bool | None = None
    available_at: datetime | None = None
    priority: int = 0


async def load_translation_states(
    session: AsyncSession,
    identities: Iterable[TranslationIdentity],
    *,
    engine: TranslationEngine | None = None,
) -> dict[TranslationIdentity, StoredTranslationState]:
    """Load Artifact-first projection state for many identities in one round trip."""
    identity_list = list(identities)
    if not identity_list:
        return {}
    payload = [
        {
            "owner_id": str(identity.owner_id) if identity.owner_id is not None else None,
            "purpose": identity.purpose,
            "scope": identity.scope,
            "source_hash": identity.source_hash,
            "source_locale": identity.source_locale,
            "target_locale": identity.target_locale,
            "engine_fingerprint": identity.engine_fingerprint,
            "prompt_version": engine.prompt_version if engine is not None else None,
            "glossary_version": engine.glossary_version if engine is not None else None,
        }
        for identity in identity_list
    ]
    rows = (
        await session.execute(
            text(_LOAD_TRANSLATION_STATES_SQL),
            {"identities": json.dumps(payload)},
        )
    ).all()
    states: dict[TranslationIdentity, StoredTranslationState] = {}
    for row in rows:
        identity = _row_identity(row)
        if identity in states and not row.is_artifact:
            continue
        states[identity] = StoredTranslationState(
            status=TranslationStatus(row.status),
            is_artifact=row.is_artifact,
            translated_text=row.translated_text,
            cache_expires_at=row.cache_expires_at,
            error_code=row.error_code,
            error_retryable=row.error_retryable,
            available_at=row.available_at,
            priority=row.priority,
        )
    return states


async def load_and_ensure_translation_states(
    session: AsyncSession,
    demands: Sequence[TranslationDemand],
    *,
    engine: TranslationEngine,
) -> tuple[dict[TranslationIdentity, StoredTranslationState], int]:
    """Resolve states and upsert every eligible miss in one database statement.

    FAILED Work is intentionally terminal here. Background demand projection
    must not create an unbounded new retry cycle; only the interactive resolver
    may issue a new, request-scoped provider attempt for the same text.
    """
    prepared: dict[TranslationIdentity, TranslationDemand] = {}
    for demand in demands:
        identity = demand.identity(engine)
        existing = prepared.get(identity)
        if existing is None or demand.priority > existing.priority:
            prepared[identity] = demand
    if not prepared:
        return {}, 0
    payload = [
        {
            "work_id": str(uuid4()),
            "owner_id": str(identity.owner_id) if identity.owner_id is not None else None,
            "purpose": identity.purpose,
            "scope": identity.scope,
            "source_hash": identity.source_hash,
            "source_text": normalize_source_text(demand.text),
            "context_before": (
                normalize_source_text(demand.context_before) if demand.context_before else None
            ),
            "context_after": (
                normalize_source_text(demand.context_after) if demand.context_after else None
            ),
            "source_locale": identity.source_locale,
            "target_locale": identity.target_locale,
            "engine_fingerprint": identity.engine_fingerprint,
            "engine_id": engine.engine_id,
            "provider_name": engine.provider_name,
            "model_name": engine.model_name,
            "prompt_version": engine.prompt_version,
            "glossary_version": engine.glossary_version,
            "priority": demand.priority,
        }
        for identity, demand in prepared.items()
    ]
    rows = (
        await session.execute(
            text(_LOAD_AND_ENSURE_TRANSLATION_STATES_SQL),
            {"demands": json.dumps(payload)},
        )
    ).all()
    states: dict[TranslationIdentity, StoredTranslationState] = {}
    precedence: dict[TranslationIdentity, int] = {}
    changed = 0
    for row in rows:
        identity = _row_identity(row)
        row_precedence = int(row.precedence)
        if row.work_changed:
            changed += 1
        if row_precedence < precedence.get(identity, -1):
            continue
        precedence[identity] = row_precedence
        states[identity] = StoredTranslationState(
            status=TranslationStatus(row.status),
            is_artifact=row.is_artifact,
            translated_text=row.translated_text,
            cache_expires_at=row.cache_expires_at,
            error_code=row.error_code,
            error_retryable=row.error_retryable,
            available_at=row.available_at,
            priority=row.priority,
        )
    return states, changed


async def persist_translation_artifacts(
    session: AsyncSession,
    demands: Sequence[TranslationDemand],
    translated_texts: dict[str, str],
    *,
    engine: TranslationEngine,
    created_at: datetime | None = None,
) -> int:
    """Publish direct interactive successes without creating transient Work."""
    from app.translation.lifecycle import (
        EPHEMERAL_PURPOSES,
        SHARED_PURPOSES,
        current_ephemeral_context,
        live_shared_hashes,
        lock_shared_lifecycle,
    )

    # Provider I/O occurs before this transaction. Recheck publication authority
    # under the same lock as preference changes, including A -> B -> A switches.
    allowed = [d for d in demands if d.purpose not in EPHEMERAL_PURPOSES or d.owner_id is not None]
    for owner_id in sorted({d.owner_id for d in demands if d.owner_id is not None}, key=str):
        context = await current_ephemeral_context(session, owner_id)
        allowed = [
            d
            for d in allowed
            if d.owner_id != owner_id
            or d.purpose not in EPHEMERAL_PURPOSES
            or (
                context.can_translate
                and context.engine is not None
                and context.engine.fingerprint == engine.fingerprint
                and context.cache_generation == d.cache_generation
            )
        ]
    if any(d.purpose in SHARED_PURPOSES and d.owner_id is None for d in allowed):
        await lock_shared_lifecycle(session)
        refs = await live_shared_hashes(session)
        allowed = [
            d
            for d in allowed
            if d.purpose not in SHARED_PURPOSES
            or d.owner_id is not None
            or d.identity(engine).source_hash in refs[d.purpose]
        ]
    demands = allowed
    prepared: dict[TranslationIdentity, tuple[TranslationDemand, str]] = {}
    for demand in demands:
        translated_text = translated_texts.get(demand.item_id, "").strip()
        if not translated_text:
            continue
        prepared.setdefault(demand.identity(engine), (demand, translated_text))
    if not prepared:
        return 0
    payload = [
        {
            "artifact_id": str(uuid4()),
            "created_at": (created_at or datetime.now(UTC)).isoformat(),
            "owner_id": str(identity.owner_id) if identity.owner_id is not None else None,
            "purpose": identity.purpose,
            "scope": identity.scope,
            "source_hash": identity.source_hash,
            "source_locale": identity.source_locale,
            "target_locale": identity.target_locale,
            "engine_fingerprint": identity.engine_fingerprint,
            "engine_id": engine.engine_id,
            "provider_name": engine.provider_name,
            "model_name": engine.model_name,
            "prompt_version": engine.prompt_version,
            "glossary_version": engine.glossary_version,
            "translated_text": translated_text,
        }
        for identity, (_demand, translated_text) in prepared.items()
    ]
    inserted = await session.scalar(
        text(_PERSIST_TRANSLATION_ARTIFACTS_SQL),
        {"artifacts": json.dumps(payload)},
    )
    await session.commit()
    return int(inserted or 0)


_LOAD_TRANSLATION_STATES_SQL = """
WITH requested AS MATERIALIZED (
    SELECT
        CASE WHEN owner_id IS NULL THEN NULL ELSE CAST(owner_id AS uuid) END AS owner_id,
        purpose,
        scope,
        source_hash,
        source_locale,
        target_locale,
        engine_fingerprint, prompt_version, glossary_version
    FROM jsonb_to_recordset(CAST(:identities AS jsonb)) AS identity(
        owner_id text,
        purpose varchar(32),
        scope varchar(16),
        source_hash varchar(64),
        source_locale varchar(16),
        target_locale varchar(16),
        engine_fingerprint varchar(256), prompt_version varchar(64), glossary_version varchar(64)
    )
),
artifact_states AS MATERIALIZED (
    SELECT requested.owner_id, requested.purpose, requested.scope,
        requested.source_hash, requested.source_locale, requested.target_locale,
        requested.engine_fingerprint, 'succeeded'::text AS status, TRUE AS is_artifact,
        artifact.translated_text, NULL::text AS error_code,
        NULL::boolean AS error_retryable, NULL::timestamptz AS available_at,
        0::integer AS priority,
        CASE WHEN requested.purpose IN ('web_segment', 'caption')
             THEN artifact.created_at + interval '60 minutes' ELSE NULL END AS cache_expires_at
    FROM requested
    JOIN LATERAL (
        SELECT a.* FROM translation_artifacts a
        WHERE a.owner_id IS NOT DISTINCT FROM requested.owner_id
          AND a.scope = requested.scope
          AND a.source_hash = requested.source_hash
          AND a.source_locale = requested.source_locale
          AND a.target_locale = requested.target_locale
          AND (
            (a.purpose = requested.purpose AND a.engine_fingerprint = requested.engine_fingerprint)
            OR (requested.scope = 'shared'
              AND requested.prompt_version IS NOT NULL
              AND a.prompt_version = requested.prompt_version
              AND a.glossary_version IS NOT DISTINCT FROM requested.glossary_version
              AND ((requested.purpose IN ('title', 'ranking_title')
                    AND a.purpose IN ('title', 'ranking_title'))
                OR (requested.purpose IN ('excerpt', 'ranking_description')
                    AND a.purpose IN ('excerpt', 'ranking_description'))))
          )
          AND (a.purpose NOT IN ('web_segment', 'caption')
            OR (a.scope = 'user' AND a.created_at > now() - interval '60 minutes'))
        ORDER BY (a.engine_fingerprint = requested.engine_fingerprint) DESC,
                 (a.purpose = requested.purpose) DESC, a.created_at DESC, a.id DESC
        LIMIT 1
    ) artifact ON TRUE
),
work_states AS (
    SELECT
        work.owner_id,
        work.purpose,
        work.scope,
        work.source_hash,
        work.source_locale,
        work.target_locale,
        work.engine_fingerprint,
        work.status,
        FALSE AS is_artifact,
        NULL::text AS translated_text,
        work.error_code,
        work.error_retryable,
        work.available_at,
        work.priority,
        NULL::timestamptz AS cache_expires_at
    FROM translation_work AS work
    JOIN requested
      ON requested.scope = 'shared'
     AND work.owner_id IS NULL
     AND work.purpose = requested.purpose
     AND work.scope = requested.scope
     AND work.source_hash = requested.source_hash
     AND work.source_locale = requested.source_locale
     AND work.target_locale = requested.target_locale
     AND work.engine_fingerprint = requested.engine_fingerprint
    UNION ALL
    SELECT
        work.owner_id,
        work.purpose,
        work.scope,
        work.source_hash,
        work.source_locale,
        work.target_locale,
        work.engine_fingerprint,
        work.status,
        FALSE AS is_artifact,
        NULL::text AS translated_text,
        work.error_code,
        work.error_retryable,
        work.available_at,
        work.priority,
        NULL::timestamptz AS cache_expires_at
    FROM translation_work AS work
    JOIN requested
      ON requested.scope = 'user'
     AND work.owner_id = requested.owner_id
     AND work.purpose = requested.purpose
     AND work.scope = requested.scope
     AND work.source_hash = requested.source_hash
     AND work.source_locale = requested.source_locale
     AND work.target_locale = requested.target_locale
     AND work.engine_fingerprint = requested.engine_fingerprint
)
SELECT * FROM artifact_states
UNION ALL
SELECT * FROM work_states
"""


_LOAD_AND_ENSURE_TRANSLATION_STATES_SQL = """
WITH requested AS MATERIALIZED (
    SELECT
        CAST(work_id AS uuid) AS work_id,
        CASE WHEN owner_id IS NULL THEN NULL ELSE CAST(owner_id AS uuid) END AS owner_id,
        purpose,
        scope,
        source_hash,
        source_text,
        context_before,
        context_after,
        source_locale,
        target_locale,
        engine_fingerprint,
        engine_id,
        provider_name,
        model_name,
        prompt_version,
        glossary_version,
        priority
    FROM jsonb_to_recordset(CAST(:demands AS jsonb)) AS demand(
        work_id text,
        owner_id text,
        purpose varchar(32),
        scope varchar(16),
        source_hash varchar(64),
        source_text text,
        context_before text,
        context_after text,
        source_locale varchar(16),
        target_locale varchar(16),
        engine_fingerprint varchar(256),
        engine_id varchar(64),
        provider_name varchar(64),
        model_name varchar(160),
        prompt_version varchar(64),
        glossary_version varchar(64),
        priority integer
    )
),
artifact_states AS MATERIALIZED (
    SELECT requested.owner_id, requested.purpose, requested.scope,
        requested.source_hash, requested.source_locale, requested.target_locale,
        requested.engine_fingerprint, 'succeeded'::text AS status, TRUE AS is_artifact,
        artifact.translated_text, NULL::text AS error_code,
        NULL::boolean AS error_retryable, NULL::timestamptz AS available_at,
        0::integer AS priority,
        CASE WHEN requested.purpose IN ('web_segment', 'caption')
             THEN artifact.created_at + interval '60 minutes' ELSE NULL END AS cache_expires_at,
        FALSE AS work_changed, 3::integer AS precedence
    FROM requested
    JOIN LATERAL (
        SELECT a.* FROM translation_artifacts a
        WHERE a.owner_id IS NOT DISTINCT FROM requested.owner_id
          AND a.scope = requested.scope
          AND a.source_hash = requested.source_hash
          AND a.source_locale = requested.source_locale
          AND a.target_locale = requested.target_locale
          AND (
            (a.purpose = requested.purpose AND a.engine_fingerprint = requested.engine_fingerprint)
            OR (requested.scope = 'shared'
              AND requested.prompt_version IS NOT NULL
              AND a.prompt_version = requested.prompt_version
              AND a.glossary_version IS NOT DISTINCT FROM requested.glossary_version
              AND ((requested.purpose IN ('title', 'ranking_title')
                    AND a.purpose IN ('title', 'ranking_title'))
                OR (requested.purpose IN ('excerpt', 'ranking_description')
                    AND a.purpose IN ('excerpt', 'ranking_description'))))
          )
          AND (a.purpose NOT IN ('web_segment', 'caption')
            OR (a.scope = 'user' AND a.created_at > now() - interval '60 minutes'))
        ORDER BY (a.engine_fingerprint = requested.engine_fingerprint) DESC,
                 (a.purpose = requested.purpose) DESC, a.created_at DESC, a.id DESC
        LIMIT 1
    ) artifact ON TRUE
),
shared_upsert AS (
    INSERT INTO translation_work (
        id,
        owner_id,
        purpose,
        scope,
        source_hash,
        source_text,
        context_before,
        context_after,
        source_locale,
        target_locale,
        engine_fingerprint,
        engine_id,
        provider_name,
        model_name,
        prompt_version,
        glossary_version,
        status,
        priority,
        attempt_count,
        available_at
    )
    SELECT
        requested.work_id,
        NULL,
        requested.purpose,
        requested.scope,
        requested.source_hash,
        requested.source_text,
        requested.context_before,
        requested.context_after,
        requested.source_locale,
        requested.target_locale,
        requested.engine_fingerprint,
        requested.engine_id,
        requested.provider_name,
        requested.model_name,
        requested.prompt_version,
        requested.glossary_version,
        'pending',
        requested.priority,
        0,
        now()
    FROM requested
    WHERE requested.scope = 'shared'
      AND NOT EXISTS (
          SELECT 1
          FROM artifact_states
          WHERE artifact_states.owner_id IS NOT DISTINCT FROM requested.owner_id
            AND artifact_states.purpose = requested.purpose
            AND artifact_states.scope = requested.scope
            AND artifact_states.source_hash = requested.source_hash
            AND artifact_states.source_locale = requested.source_locale
            AND artifact_states.target_locale = requested.target_locale
            AND artifact_states.engine_fingerprint = requested.engine_fingerprint
      )
    ON CONFLICT (purpose, source_hash, source_locale, target_locale, engine_fingerprint)
        WHERE owner_id IS NULL
    DO UPDATE SET
        source_text = EXCLUDED.source_text,
        context_before = EXCLUDED.context_before,
        context_after = EXCLUDED.context_after,
        priority = greatest(translation_work.priority, EXCLUDED.priority),
        status = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN 'pending'
            ELSE translation_work.status
        END,
        attempt_count = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN 0
            ELSE translation_work.attempt_count
        END,
        available_at = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN now()
            ELSE translation_work.available_at
        END,
        finished_at = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN NULL
            ELSE translation_work.finished_at
        END,
        error_code = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN NULL
            ELSE translation_work.error_code
        END,
        error_message = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN NULL
            ELSE translation_work.error_message
        END,
        error_retryable = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN NULL
            ELSE translation_work.error_retryable
        END,
        updated_at = now()
    WHERE (
        translation_work.status IN ('pending', 'running')
        AND translation_work.priority < EXCLUDED.priority
    ) OR translation_work.status IN ('succeeded', 'cancelled')
    RETURNING
        owner_id,
        purpose,
        scope,
        source_hash,
        source_locale,
        target_locale,
        engine_fingerprint,
        status,
        FALSE AS is_artifact,
        NULL::text AS translated_text,
        error_code,
        error_retryable,
        available_at,
        priority,
        NULL::timestamptz AS cache_expires_at,
        TRUE AS work_changed,
        2::integer AS precedence
),
user_upsert AS (
    INSERT INTO translation_work (
        id,
        owner_id,
        purpose,
        scope,
        source_hash,
        source_text,
        context_before,
        context_after,
        source_locale,
        target_locale,
        engine_fingerprint,
        engine_id,
        provider_name,
        model_name,
        prompt_version,
        glossary_version,
        status,
        priority,
        attempt_count,
        available_at
    )
    SELECT
        requested.work_id,
        requested.owner_id,
        requested.purpose,
        requested.scope,
        requested.source_hash,
        requested.source_text,
        requested.context_before,
        requested.context_after,
        requested.source_locale,
        requested.target_locale,
        requested.engine_fingerprint,
        requested.engine_id,
        requested.provider_name,
        requested.model_name,
        requested.prompt_version,
        requested.glossary_version,
        'pending',
        requested.priority,
        0,
        now()
    FROM requested
    WHERE requested.scope = 'user'
      AND NOT EXISTS (
          SELECT 1
          FROM artifact_states
          WHERE artifact_states.owner_id IS NOT DISTINCT FROM requested.owner_id
            AND artifact_states.purpose = requested.purpose
            AND artifact_states.scope = requested.scope
            AND artifact_states.source_hash = requested.source_hash
            AND artifact_states.source_locale = requested.source_locale
            AND artifact_states.target_locale = requested.target_locale
            AND artifact_states.engine_fingerprint = requested.engine_fingerprint
      )
    ON CONFLICT (owner_id, purpose, source_hash, source_locale, target_locale, engine_fingerprint)
        WHERE owner_id IS NOT NULL
    DO UPDATE SET
        source_text = EXCLUDED.source_text,
        context_before = EXCLUDED.context_before,
        context_after = EXCLUDED.context_after,
        priority = greatest(translation_work.priority, EXCLUDED.priority),
        status = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN 'pending'
            ELSE translation_work.status
        END,
        attempt_count = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN 0
            ELSE translation_work.attempt_count
        END,
        available_at = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN now()
            ELSE translation_work.available_at
        END,
        finished_at = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN NULL
            ELSE translation_work.finished_at
        END,
        error_code = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN NULL
            ELSE translation_work.error_code
        END,
        error_message = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN NULL
            ELSE translation_work.error_message
        END,
        error_retryable = CASE
            WHEN translation_work.status IN ('succeeded', 'cancelled') THEN NULL
            ELSE translation_work.error_retryable
        END,
        updated_at = now()
    WHERE (
        translation_work.status IN ('pending', 'running')
        AND translation_work.priority < EXCLUDED.priority
    ) OR translation_work.status IN ('succeeded', 'cancelled')
    RETURNING
        owner_id,
        purpose,
        scope,
        source_hash,
        source_locale,
        target_locale,
        engine_fingerprint,
        status,
        FALSE AS is_artifact,
        NULL::text AS translated_text,
        error_code,
        error_retryable,
        available_at,
        priority,
        NULL::timestamptz AS cache_expires_at,
        TRUE AS work_changed,
        2::integer AS precedence
),
existing_work_states AS (
    SELECT
        work.owner_id,
        work.purpose,
        work.scope,
        work.source_hash,
        work.source_locale,
        work.target_locale,
        work.engine_fingerprint,
        work.status,
        FALSE AS is_artifact,
        NULL::text AS translated_text,
        work.error_code,
        work.error_retryable,
        work.available_at,
        work.priority,
        NULL::timestamptz AS cache_expires_at,
        FALSE AS work_changed,
        1::integer AS precedence
    FROM translation_work AS work
    JOIN requested
      ON requested.scope = 'shared'
     AND work.owner_id IS NULL
     AND work.purpose = requested.purpose
     AND work.scope = requested.scope
     AND work.source_hash = requested.source_hash
     AND work.source_locale = requested.source_locale
     AND work.target_locale = requested.target_locale
     AND work.engine_fingerprint = requested.engine_fingerprint
    UNION ALL
    SELECT
        work.owner_id,
        work.purpose,
        work.scope,
        work.source_hash,
        work.source_locale,
        work.target_locale,
        work.engine_fingerprint,
        work.status,
        FALSE AS is_artifact,
        NULL::text AS translated_text,
        work.error_code,
        work.error_retryable,
        work.available_at,
        work.priority,
        NULL::timestamptz AS cache_expires_at,
        FALSE AS work_changed,
        1::integer AS precedence
    FROM translation_work AS work
    JOIN requested
      ON requested.scope = 'user'
     AND work.owner_id = requested.owner_id
     AND work.purpose = requested.purpose
     AND work.scope = requested.scope
     AND work.source_hash = requested.source_hash
     AND work.source_locale = requested.source_locale
     AND work.target_locale = requested.target_locale
     AND work.engine_fingerprint = requested.engine_fingerprint
)
SELECT * FROM artifact_states
UNION ALL
SELECT * FROM shared_upsert
UNION ALL
SELECT * FROM user_upsert
UNION ALL
SELECT * FROM existing_work_states
"""


_PERSIST_TRANSLATION_ARTIFACTS_SQL = """
WITH requested AS MATERIALIZED (
    SELECT
        CAST(artifact_id AS uuid) AS artifact_id,
        CASE WHEN owner_id IS NULL THEN NULL ELSE CAST(owner_id AS uuid) END AS owner_id,
        purpose,
        scope,
        source_hash,
        source_locale,
        target_locale,
        engine_fingerprint,
        engine_id,
        provider_name,
        model_name,
        prompt_version,
        glossary_version,
        translated_text, created_at
    FROM jsonb_to_recordset(CAST(:artifacts AS jsonb)) AS artifact(
        artifact_id text,
        owner_id text,
        purpose varchar(32),
        scope varchar(16),
        source_hash varchar(64),
        source_locale varchar(16),
        target_locale varchar(16),
        engine_fingerprint varchar(256),
        engine_id varchar(64),
        provider_name varchar(64),
        model_name varchar(160),
        prompt_version varchar(64),
        glossary_version varchar(64),
        translated_text text, created_at timestamptz
    )
),
shared_insert AS (
    INSERT INTO translation_artifacts (
        id,
        owner_id,
        purpose,
        scope,
        source_hash,
        source_locale,
        target_locale,
        engine_fingerprint,
        engine_id,
        provider_name,
        model_name,
        prompt_version,
        glossary_version,
        translated_text, created_at
    )
    SELECT
        requested.artifact_id,
        NULL,
        requested.purpose,
        requested.scope,
        requested.source_hash,
        requested.source_locale,
        requested.target_locale,
        requested.engine_fingerprint,
        requested.engine_id,
        requested.provider_name,
        requested.model_name,
        requested.prompt_version,
        requested.glossary_version,
        requested.translated_text, requested.created_at
    FROM requested
    WHERE requested.scope = 'shared'
    ON CONFLICT (purpose, source_hash, source_locale, target_locale, engine_fingerprint)
        WHERE owner_id IS NULL
    DO UPDATE SET translated_text = EXCLUDED.translated_text, created_at = EXCLUDED.created_at
    WHERE translation_artifacts.purpose IN ('web_segment', 'caption')
      AND translation_artifacts.created_at <= now() - interval '60 minutes'
    RETURNING id
),
user_insert AS (
    INSERT INTO translation_artifacts (
        id,
        owner_id,
        purpose,
        scope,
        source_hash,
        source_locale,
        target_locale,
        engine_fingerprint,
        engine_id,
        provider_name,
        model_name,
        prompt_version,
        glossary_version,
        translated_text, created_at
    )
    SELECT
        requested.artifact_id,
        requested.owner_id,
        requested.purpose,
        requested.scope,
        requested.source_hash,
        requested.source_locale,
        requested.target_locale,
        requested.engine_fingerprint,
        requested.engine_id,
        requested.provider_name,
        requested.model_name,
        requested.prompt_version,
        requested.glossary_version,
        requested.translated_text, requested.created_at
    FROM requested
    WHERE requested.scope = 'user'
    ON CONFLICT (owner_id, purpose, source_hash, source_locale, target_locale, engine_fingerprint)
        WHERE owner_id IS NOT NULL
    DO UPDATE SET translated_text = EXCLUDED.translated_text, created_at = EXCLUDED.created_at
    WHERE translation_artifacts.purpose IN ('web_segment', 'caption')
      AND translation_artifacts.created_at <= now() - interval '60 minutes'
    RETURNING id
),
settled_pending_work AS (
    UPDATE translation_work AS work
    SET
        status = 'succeeded',
        lease_token = NULL,
        lease_expires_at = NULL,
        finished_at = now(),
        last_attempt_finished_at = now(),
        error_code = NULL,
        error_message = NULL,
        error_retryable = NULL,
        updated_at = now()
    FROM requested
    WHERE work.status = 'pending'
      AND work.owner_id IS NOT DISTINCT FROM requested.owner_id
      AND work.purpose = requested.purpose
      AND work.scope = requested.scope
      AND work.source_hash = requested.source_hash
      AND work.source_locale = requested.source_locale
      AND work.target_locale = requested.target_locale
      AND work.engine_fingerprint = requested.engine_fingerprint
    RETURNING work.id
)
SELECT
    (SELECT count(*) FROM shared_insert)
    + (SELECT count(*) FROM user_insert)
    + 0 * (SELECT count(*) FROM settled_pending_work)
"""


async def ensure_translation_work(
    session: AsyncSession,
    demands: Sequence[TranslationDemand],
    *,
    engine: TranslationEngine,
) -> int:
    """Ensure deduplicated work exists with at most one statement per scope.

    FAILED Work remains terminal. This function is also used by background
    ingestion, so reviving it here would turn a bounded worker retry policy
    into an unbounded loop.
    """
    _states, changed = await load_and_ensure_translation_states(session, demands, engine=engine)
    return changed


async def claim_translation_work(
    session: AsyncSession,
    *,
    engine_fingerprints: Sequence[str],
    limit: int,
    lease_seconds: int,
    min_priority: int | None = None,
    max_priority: int | None = None,
) -> list[TranslationWork]:
    """Claim due or abandoned work and commit its lease before provider I/O."""
    if limit < 1 or not engine_fingerprints:
        return []
    now = datetime.now(UTC)
    eligible = or_(
        and_(
            TranslationWork.status == TranslationStatus.PENDING,
            TranslationWork.available_at <= now,
        ),
        and_(
            TranslationWork.status == TranslationStatus.RUNNING,
            TranslationWork.lease_expires_at < now,
        ),
    )
    filters = [TranslationWork.engine_fingerprint.in_(engine_fingerprints), eligible]
    if min_priority is not None:
        filters.append(TranslationWork.priority >= min_priority)
    if max_priority is not None:
        filters.append(TranslationWork.priority <= max_priority)
    claimable = (
        select(TranslationWork.id)
        .where(*filters)
        .order_by(
            TranslationWork.priority.desc(),
            TranslationWork.available_at,
            TranslationWork.created_at,
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("claimable_translation_work")
    )
    rows = list(
        await session.scalars(
            update(TranslationWork)
            .where(TranslationWork.id.in_(select(claimable.c.id)))
            .values(
                status=TranslationStatus.RUNNING,
                attempt_count=TranslationWork.attempt_count + 1,
                lease_token=uuid4(),
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=func.coalesce(TranslationWork.started_at, now),
                last_attempt_started_at=now,
                last_attempt_finished_at=None,
                error_code=None,
                error_message=None,
                error_retryable=None,
                updated_at=now,
            )
            .returning(TranslationWork)
        )
    )
    await session.commit()
    return rows


async def renew_translation_work_leases(
    session: AsyncSession,
    leases: Sequence[tuple[UUID, UUID]],
    *,
    lease_seconds: float,
) -> int:
    """Extend only Work rows whose original lease token is still authoritative."""
    if not leases:
        return 0
    now = datetime.now(UTC)
    result = await session.execute(
        update(TranslationWork)
        .where(
            TranslationWork.status == TranslationStatus.RUNNING,
            tuple_(TranslationWork.id, TranslationWork.lease_token).in_(leases),
        )
        .values(
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )
    )
    await session.commit()
    return int(result.rowcount or 0)


async def cancel_stale_user_translation_work(
    session: AsyncSession,
    *,
    owner_id: UUID,
    keep_engine_id: str | None,
    keep_target_locale: str | None,
) -> int:
    """Cancel unclaimed private work after a user changes translation routing.

    Shared work is deliberately untouched because another user may still need
    it. Running work keeps its immutable engine identity and may finish safely.
    Passing no retained route cancels every pending private task, as when
    translation is disabled.
    """
    predicates = [
        TranslationWork.owner_id == owner_id,
        TranslationWork.scope == TranslationScope.USER,
        TranslationWork.status == TranslationStatus.PENDING,
    ]
    if keep_engine_id is not None and keep_target_locale is not None:
        predicates.append(
            or_(
                TranslationWork.engine_id != keep_engine_id,
                TranslationWork.target_locale != keep_target_locale,
            )
        )
    rows = await session.scalars(
        update(TranslationWork)
        .where(*predicates)
        .values(
            status=TranslationStatus.CANCELLED,
            finished_at=datetime.now(UTC),
            error_code="engine_preference_changed",
            error_message="Translation demand was superseded by an engine preference change.",
            error_retryable=False,
            updated_at=datetime.now(UTC),
        )
        .returning(TranslationWork.id)
    )
    return len(list(rows))


async def persist_work_outcomes(
    session: AsyncSession,
    claimed: Sequence[TranslationWork],
    outcomes: dict[UUID, WorkOutcome],
) -> int:
    """Atomically publish artifacts and transition leases to their next state."""
    if not claimed:
        return 0
    from app.translation.lifecycle import SHARED_PURPOSES, live_shared_hashes, lock_shared_lifecycle

    shared = [w for w in claimed if w.owner_id is None and w.purpose in SHARED_PURPOSES]
    if shared:
        await lock_shared_lifecycle(session)
        refs = await live_shared_hashes(session)
        obsolete = [w.id for w in shared if w.source_hash not in refs[w.purpose]]
        if obsolete:
            from sqlalchemy import delete

            await session.execute(delete(TranslationWork).where(TranslationWork.id.in_(obsolete)))
            claimed = [w for w in claimed if w.id not in obsolete]
            if not claimed:
                await session.commit()
                return 0
    payload: list[dict[str, object]] = []
    for claimed_work in claimed:
        outcome = outcomes.get(claimed_work.id)
        if outcome is None or claimed_work.lease_token is None:
            continue
        payload.append(
            {
                "id": str(claimed_work.id),
                "artifact_id": str(uuid4()),
                "lease_token": str(claimed_work.lease_token),
                "source_hash": claimed_work.source_hash,
                "succeeded": outcome.succeeded,
                "translated_text": outcome.translated_text,
                "error_code": outcome.error_code,
                "error_message": (outcome.error_message[:2000] if outcome.error_message else None),
                "retryable": outcome.retryable,
                "retry_after_seconds": outcome.retry_after_seconds,
                "provider_latency_ms": outcome.provider_latency_ms,
            }
        )
    if not payload:
        return 0
    applied = await session.scalar(
        text(_PERSIST_OUTCOMES_SQL),
        {
            "payload": json.dumps(payload),
            "artifact_channel": ARTIFACT_NOTIFY_CHANNEL,
        },
    )
    await session.commit()
    return int(applied or 0)


_PERSIST_OUTCOMES_SQL = """
WITH outcome_input AS (
    SELECT *
    FROM jsonb_to_recordset(CAST(:payload AS jsonb)) AS outcome(
        id text,
        artifact_id text,
        lease_token text,
        source_hash text,
        succeeded boolean,
        translated_text text,
        error_code text,
        error_message text,
        retryable boolean,
        retry_after_seconds double precision,
        provider_latency_ms integer
    )
),
valid_outcomes AS MATERIALIZED (
    SELECT
        work.id AS work_id,
        work.owner_id,
        work.purpose,
        work.scope,
        work.source_hash,
        work.source_locale,
        work.target_locale,
        work.engine_fingerprint,
        work.engine_id,
        work.provider_name,
        work.model_name,
        work.prompt_version,
        work.glossary_version,
        outcome.artifact_id,
        outcome.succeeded,
        outcome.translated_text,
        outcome.error_code,
        outcome.error_message,
        outcome.retryable,
        outcome.retry_after_seconds,
        outcome.provider_latency_ms
    FROM translation_work AS work
    JOIN outcome_input AS outcome ON work.id = CAST(outcome.id AS uuid)
    WHERE work.status = 'running'
      AND work.lease_token = CAST(outcome.lease_token AS uuid)
      AND work.source_hash = outcome.source_hash
    FOR UPDATE OF work
),
inserted_artifacts AS (
    INSERT INTO translation_artifacts (
        id,
        owner_id,
        purpose,
        scope,
        source_hash,
        source_locale,
        target_locale,
        engine_fingerprint,
        engine_id,
        provider_name,
        model_name,
        prompt_version,
        glossary_version,
        translated_text
    )
    SELECT
        CAST(valid.artifact_id AS uuid),
        valid.owner_id,
        valid.purpose,
        valid.scope,
        valid.source_hash,
        valid.source_locale,
        valid.target_locale,
        valid.engine_fingerprint,
        valid.engine_id,
        valid.provider_name,
        valid.model_name,
        valid.prompt_version,
        valid.glossary_version,
        valid.translated_text
    FROM valid_outcomes AS valid
    WHERE valid.succeeded AND valid.translated_text IS NOT NULL
    ON CONFLICT DO NOTHING
    RETURNING id
),
updated_work AS (
    UPDATE translation_work AS work
    SET
        status = CASE
            WHEN valid.succeeded THEN 'succeeded'
            WHEN valid.retryable THEN 'pending'
            ELSE 'failed'
        END,
        available_at = CASE
            WHEN NOT valid.succeeded AND valid.retryable
                THEN now()
                    + COALESCE(valid.retry_after_seconds, 1.0) * interval '1 second'
            ELSE work.available_at
        END,
        lease_token = NULL,
        lease_expires_at = NULL,
        finished_at = CASE
            WHEN NOT valid.succeeded AND valid.retryable THEN NULL
            ELSE now()
        END,
        last_attempt_finished_at = now(),
        provider_latency_ms = valid.provider_latency_ms,
        error_code = valid.error_code,
        error_message = valid.error_message,
        error_retryable = CASE
            WHEN valid.error_code IS NULL THEN NULL
            ELSE valid.retryable
        END,
        updated_at = now()
    FROM valid_outcomes AS valid
    WHERE work.id = valid.work_id
    RETURNING work.id
),
notified AS (
    SELECT pg_notify(:artifact_channel, count(*)::text)
    FROM inserted_artifacts
    HAVING count(*) > 0
)
SELECT
    (SELECT count(*) FROM updated_work) AS applied,
    (SELECT count(*) FROM notified) AS notifications
"""


def _row_identity(row: TranslationArtifact | TranslationWork) -> TranslationIdentity:
    return TranslationIdentity(
        owner_id=row.owner_id,
        purpose=TranslationPurpose(row.purpose),
        scope=TranslationScope(row.scope),
        source_hash=row.source_hash,
        source_locale=row.source_locale,
        target_locale=row.target_locale,
        engine_fingerprint=row.engine_fingerprint,
    )


def _identity_columns(model, scope: TranslationScope) -> list[object]:
    columns = [
        model.purpose,
        model.source_hash,
        model.source_locale,
        model.target_locale,
        model.engine_fingerprint,
    ]
    return [model.owner_id, *columns] if scope == TranslationScope.USER else columns


def _scope_predicate(model, scope: TranslationScope):
    return (
        model.owner_id.is_(None)
        if scope == TranslationScope.SHARED
        else model.owner_id.is_not(None)
    )
