"""Application-owned relational models.

Reader owns identity tables in auth_models and all product data here.
Shared source records never contain a user's
personal reading state or credentials.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    CacheStatus,
    CandidateStatus,
    ContentKind,
    ExtractionStatus,
    MediaType,
    SourceKind,
    SourceStatus,
    SourceVisibility,
    SyncPhase,
    SyncRunStatus,
    TranslationProviderMode,
    TranslationPurpose,
    TranslationScope,
    TranslationStatus,
    WebFrontierStatus,
)

from .database import Base

source_entry_update_sequence = Sequence("source_entry_update_sequence", metadata=Base.metadata)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Profile(Timestamped, Base):
    __tablename__ = "profiles"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    avatar_url: Mapped[str | None] = mapped_column(Text)

    subscriptions: Mapped[list[SourceSubscription]] = relationship(back_populates="user")
    reading_states: Mapped[list[UserContentState]] = relationship(back_populates="user")
    saved_contents: Mapped[list[UserSavedContent]] = relationship(back_populates="user")
    translation_preference: Mapped[TranslationPreference | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    translation_artifacts: Mapped[list[TranslationArtifact]] = relationship(back_populates="owner")
    translation_work: Mapped[list[TranslationWork]] = relationship(back_populates="owner")


class TranslationQuotaBucket(Base):
    """One fixed UTC 60-second quota window.

    Rows are keyed by a stable user/global namespace and locked with
    ``SELECT ... FOR UPDATE`` by the quota service. Keeping the counter in
    PostgreSQL makes reservations safe across API processes and workers.
    """

    __tablename__ = "translation_quota_buckets"
    __table_args__ = (
        CheckConstraint("requests_used >= 0", name="ck_translation_quota_requests_used"),
        CheckConstraint(
            "actual_miss_chars_used >= 0",
            name="ck_translation_quota_actual_miss_chars_used",
        ),
        Index("ix_translation_quota_buckets_user_window", "user_id", "window_started_at"),
    )

    bucket_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE")
    )
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requests_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_miss_chars_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ``TranslationQuotaUsage`` is the public service name used by operational
# tooling. Keep it as an alias so callers do not accidentally create a second
# table/model for the same durable counter.
TranslationQuotaUsage = TranslationQuotaBucket


class TranslationQuotaOverride(Base):
    """Optional per-user limits managed by the break-glass CLI only."""

    __tablename__ = "translation_quota_overrides"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    requests: Mapped[int | None] = mapped_column(Integer)
    actual_miss_chars: Mapped[int | None] = mapped_column(Integer)
    is_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "requests IS NULL OR requests >= 0",
            name="ck_translation_override_requests",
        ),
        CheckConstraint(
            "actual_miss_chars IS NULL OR actual_miss_chars >= 0",
            name="ck_translation_override_chars",
        ),
    )


class TranslationQuotaAudit(Base):
    """Append-only, redacted record of every quota override mutation."""

    __tablename__ = "translation_quota_audits"
    __table_args__ = (Index("ix_translation_quota_audits_user_created", "user_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(128))
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    requests: Mapped[int | None] = mapped_column(Integer)
    actual_miss_chars: Mapped[int | None] = mapped_column(Integer)
    is_disabled: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FeedSource(Timestamped, Base):
    __tablename__ = "feed_sources"
    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_feed_sources_canonical_key"),
        CheckConstraint("visibility IN ('shared', 'private')", name="ck_feed_sources_visibility"),
        CheckConstraint("status IN ('pending', 'active', 'paused')", name="ck_feed_sources_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[SourceKind] = mapped_column(String(32), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(300))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    visibility: Mapped[SourceVisibility] = mapped_column(
        String(16), nullable=False, default=SourceVisibility.SHARED
    )
    owner_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    status: Mapped[SourceStatus] = mapped_column(
        String(16), nullable=False, default=SourceStatus.PENDING
    )
    latest_update_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    subscriptions: Mapped[list[SourceSubscription]] = relationship(back_populates="source")
    sync_runs: Mapped[list[SourceSyncRun]] = relationship(back_populates="source")
    entries: Mapped[list[SourceEntry]] = relationship(back_populates="source")
    sync_state: Mapped[SourceSyncState | None] = relationship(
        back_populates="source", uselist=False, cascade="all, delete-orphan"
    )
    candidates: Mapped[list[IngestionCandidate]] = relationship(back_populates="source")
    web_frontier: Mapped[list[WebFrontier]] = relationship(back_populates="source")


class SourceSubscription(Timestamped, Base):
    __tablename__ = "source_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "source_id", name="uq_source_subscriptions_user_source"),
        Index("ix_source_subscriptions_source_id", "source_id"),
        Index(
            "ix_source_subscriptions_user_enabled_folder_source",
            "user_id",
            "is_enabled",
            "folder_name",
            "source_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_sources.id", ondelete="CASCADE"), nullable=False
    )
    custom_name: Mapped[str | None] = mapped_column(String(300))
    folder_name: Mapped[str | None] = mapped_column(String(120))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_in_home: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_viewed_update_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    user: Mapped[Profile] = relationship(back_populates="subscriptions")
    source: Mapped[FeedSource] = relationship(back_populates="subscriptions")


class SourceSyncRun(Base):
    __tablename__ = "source_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'partial', 'failed')",
            name="ck_source_sync_runs_status",
        ),
        Index("ix_source_sync_runs_source_started", "source_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_sources.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[SyncRunStatus] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_mode: Mapped[str | None] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)

    source: Mapped[FeedSource] = relationship(back_populates="sync_runs")


class SourceSyncState(Timestamped, Base):
    """Durable scanner progress; this is the only source scheduling authority."""

    __tablename__ = "source_sync_states"
    __table_args__ = (
        CheckConstraint(
            "phase IN ('idle', 'catching_up', 'degraded')",
            name="ck_source_sync_states_phase",
        ),
        Index("ix_source_sync_states_due", "next_scan_at", "lease_expires_at"),
    )

    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("feed_sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    phase: Mapped[SyncPhase] = mapped_column(String(24), nullable=False, default=SyncPhase.IDLE)
    committed_checkpoint: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    pending_checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    continuation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provider_mode: Mapped[str | None] = mapped_column(String(64))
    initial_sync_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_complete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_limit_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    web_rule_structural_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    web_rule_failure_episode_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    gap_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gap_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[FeedSource] = relationship(back_populates="sync_state")


class WebRuleJob(Timestamped, Base):
    """Source-scoped, durable model budget and fenced rule authoring work."""

    __tablename__ = "web_rule_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_web_rule_jobs_idempotency"),
        CheckConstraint("reason IN ('author', 'repair')", name="ck_web_rule_jobs_reason"),
        CheckConstraint("goal_resumptions_reserved >= 0", name="ck_web_rule_jobs_goal_resumptions"),
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', "
            "'blocked', 'cancelled')",
            name="ck_web_rule_jobs_status",
        ),
        Index("ix_web_rule_jobs_due", "status", "available_at"),
        Index("ix_web_rule_jobs_source_created", "source_id", "created_at"),
        Index(
            "uq_web_rule_jobs_active_source",
            "source_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'retry_wait')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_sources.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    failure_episode_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    retry_of: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("web_rule_jobs.id", ondelete="SET NULL")
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    base_rule_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    base_rule_hash: Mapped[str | None] = mapped_column(String(64))
    base_rule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    prompt_version: Mapped[str | None] = mapped_column(String(120))
    rule_schema_version: Mapped[str | None] = mapped_column(String(120))
    validator_version: Mapped[str | None] = mapped_column(String(120))
    engine_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    limits_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="preflight")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_calls_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_calls_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goal_resumptions_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unknown_usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conservative_tokens_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage_reservations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    candidate_rule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    activated_version: Mapped[str | None] = mapped_column(String(64))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    diagnostics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class WebRuleAgentRuntime(Timestamped, Base):
    """Singleton deployment-wide execution slot and durable engine circuit breaker."""

    __tablename__ = "web_rule_agent_runtime"
    __table_args__ = (CheckConstraint("id = 'default'", name="ck_web_rule_runtime_singleton"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    current_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("web_rule_jobs.id", ondelete="SET NULL")
    )
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    engine_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    paused_code: Mapped[str | None] = mapped_column(String(120))
    paused_message: Mapped[str | None] = mapped_column(Text)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionCandidate(Timestamped, Base):
    """Idempotent hand-off between upstream scanning and content persistence."""

    __tablename__ = "ingestion_candidates"
    __table_args__ = (
        UniqueConstraint("source_id", "native_id", name="uq_ingestion_candidates_source_native"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_ingestion_candidates_status",
        ),
        Index("ix_ingestion_candidates_claim", "status", "available_at", "observed_at"),
        Index("ix_ingestion_candidates_source_observed", "source_id", "observed_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_sources.id", ondelete="CASCADE"), nullable=False
    )
    native_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    suggested_feed_sort_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    counts_as_update: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[CandidateStatus] = mapped_column(
        String(16), nullable=False, default=CandidateStatus.PENDING
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contents.id", ondelete="SET NULL")
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_error_message: Mapped[str | None] = mapped_column(Text)

    source: Mapped[FeedSource] = relationship(back_populates="candidates")


class WebFrontier(Timestamped, Base):
    """Durable discovery evidence for generic and site-specific Web scans."""

    __tablename__ = "web_frontier"
    __table_args__ = (
        UniqueConstraint("source_id", "url_hash", name="uq_web_frontier_source_url_hash"),
        CheckConstraint(
            "status IN ('pending', 'fetched', 'uncertain', 'rejected', 'failed')",
            name="ck_web_frontier_status",
        ),
        Index("ix_web_frontier_due", "source_id", "status", "available_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_sources.id", ondelete="CASCADE"), nullable=False
    )
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    discovered_from: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[WebFrontierStatus] = mapped_column(
        String(16), nullable=False, default=WebFrontierStatus.PENDING
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified: Mapped[str | None] = mapped_column(String(512))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    source: Mapped[FeedSource] = relationship(back_populates="web_frontier")


class RankingSnapshot(Base):
    """A shared, provider-facing ranking cache rather than per-user content."""

    __tablename__ = "ranking_snapshots"
    __table_args__ = (
        CheckConstraint(
            "(refresh_token IS NULL) = (refresh_expires_at IS NULL)",
            name="ck_ranking_snapshots_refresh_lease",
        ),
        Index("ix_ranking_snapshots_next_refresh", "next_refresh_at"),
        Index("ix_ranking_snapshots_inactive", "kind", "last_accessed_at"),
    )

    cache_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_refresh_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_ready: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    refresh_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_refresh_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class RankingProviderBudget(Base):
    """Cross-process refresh budget shared by all snapshot keys for one provider."""

    __tablename__ = "ranking_provider_budgets"
    __table_args__ = (
        CheckConstraint("refresh_count >= 0", name="ck_ranking_provider_budget_count"),
    )

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProviderQuotaUsage(Base):
    """Small durable ledger for provider-level daily soft budgets."""

    __tablename__ = "provider_quota_usage"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkerLoopHeartbeat(Base):
    """Cross-process evidence that each durable worker loop is still progressing."""

    __tablename__ = "worker_loop_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "expected_interval_seconds > 0",
            name="ck_worker_loop_heartbeats_expected_interval",
        ),
        Index("ix_worker_loop_heartbeats_loop_updated", "loop_name", "updated_at"),
    )

    worker_instance_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    loop_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    expected_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppSchemaContract(Base):
    """Durable minimum-compatible schema capabilities installed by migrations."""

    __tablename__ = "app_schema_contracts"

    contract: Mapped[str] = mapped_column(String(64), primary_key=True)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Content(Timestamped, Base):
    __tablename__ = "contents"
    __table_args__ = (
        CheckConstraint("kind IN ('article', 'post', 'video', 'podcast')", name="ck_contents_kind"),
        CheckConstraint(
            "extraction_status IN ('not_needed', 'pending', 'success', 'failed')",
            name="ck_contents_extraction_status",
        ),
        UniqueConstraint(
            "id",
            "authority_source_id",
            name="uq_contents_id_authority_source",
        ),
        Index("ix_contents_published_at", "published_at"),
        Index("ix_contents_canonical_url", "canonical_url"),
        Index("ix_contents_content_hash", "content_hash"),
        Index("ix_contents_authority_source_id", "authority_source_id"),
        Index(
            "uq_contents_authority_url_hash",
            "authority_source_id",
            "url_hash",
            unique=True,
            postgresql_where=text("url_hash IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    authority_source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("feed_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[ContentKind] = mapped_column(String(16), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    url_hash: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(300))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    excerpt: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        String(16), nullable=False, default=ExtractionStatus.NOT_NEEDED
    )

    # The database FK also carries source_id to enforce authority ownership.
    # ORM writes only content_id here; SourceEntry.source owns source_id.
    entries: Mapped[list[SourceEntry]] = relationship(
        back_populates="content",
        foreign_keys="SourceEntry.content_id",
    )
    media: Mapped[list[ContentMedia]] = relationship(back_populates="content")
    reading_states: Mapped[list[UserContentState]] = relationship(back_populates="content")
    saved_by: Mapped[list[UserSavedContent]] = relationship(back_populates="content")


class SourceEntry(Base):
    __tablename__ = "source_entries"
    __table_args__ = (
        UniqueConstraint("source_id", "native_id", name="uq_source_entries_source_native"),
        ForeignKeyConstraint(
            ["content_id", "source_id"],
            ["contents.id", "contents.authority_source_id"],
            name="fk_source_entries_content_authority",
            ondelete="CASCADE",
        ),
        Index("ix_source_entries_content_id", "content_id"),
        Index("ix_source_entries_source_feed_sort", "source_id", "feed_sort_at", "id"),
        Index("ix_source_entries_source_update_sequence", "source_id", "update_sequence"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feed_sources.id", ondelete="CASCADE"), nullable=False
    )
    content_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    native_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    external_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(300))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feed_sort_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    update_sequence: Mapped[int | None] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    source: Mapped[FeedSource] = relationship(back_populates="entries")
    content: Mapped[Content] = relationship(
        back_populates="entries",
        foreign_keys=[content_id],
    )


class ContentMedia(Base):
    __tablename__ = "content_media"
    __table_args__ = (
        CheckConstraint(
            "media_type IN ('image', 'audio', 'video', 'embed')", name="ck_content_media_type"
        ),
        CheckConstraint(
            "cache_status IN ('external', 'queued', 'cached', 'failed')",
            name="ck_content_media_cache_status",
        ),
        UniqueConstraint("content_id", "original_url", name="uq_content_media_content_url"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    content_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contents.id", ondelete="CASCADE"), nullable=False
    )
    media_type: Mapped[MediaType] = mapped_column(String(16), nullable=False)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    cached_path: Mapped[str | None] = mapped_column(Text)
    cache_status: Mapped[CacheStatus] = mapped_column(
        String(16), nullable=False, default=CacheStatus.EXTERNAL
    )
    mime_type: Mapped[str | None] = mapped_column(String(200))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    poster_url: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    content: Mapped[Content] = relationship(back_populates="media")


class TranslationPreference(Timestamped, Base):
    """The active language/provider selection for one authenticated user.

    API credentials are deliberately not stored here.  This row only contains
    non-secret routing preferences and can later point at encrypted provider
    credentials managed by a dedicated secret store.
    """

    __tablename__ = "user_translation_preferences"
    __table_args__ = (
        CheckConstraint(
            "provider_mode IN ('app_default', 'app_managed', 'byok')",
            name="ck_user_translation_preferences_provider_mode",
        ),
        Index("ix_user_translation_preferences_target_locale", "target_locale"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    target_locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    provider_mode: Mapped[TranslationProviderMode] = mapped_column(
        String(16), nullable=False, default=TranslationProviderMode.APP_DEFAULT
    )
    engine_id: Mapped[str | None] = mapped_column(String(64))
    provider_name: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(160))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[Profile] = relationship(back_populates="translation_preference")


class TranslationArtifact(Base):
    """Immutable successful result shared by matching translation demands."""

    __tablename__ = "translation_artifacts"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('title', 'excerpt', 'body', 'paragraph', 'ranking_title', "
            "'ranking_description', 'web_segment', 'caption')",
            name="ck_translation_artifacts_purpose",
        ),
        CheckConstraint("scope IN ('shared', 'user')", name="ck_translation_artifacts_scope"),
        CheckConstraint(
            "length(engine_id) > 0",
            name="ck_translation_artifacts_engine_id",
        ),
        CheckConstraint(
            "(scope = 'shared' AND owner_id IS NULL) OR (scope = 'user' AND owner_id IS NOT NULL)",
            name="ck_translation_artifacts_scope_owner",
        ),
        Index(
            "uq_translation_artifacts_shared_identity",
            "purpose",
            "source_hash",
            "source_locale",
            "target_locale",
            "engine_fingerprint",
            unique=True,
            postgresql_where=text("owner_id IS NULL"),
        ),
        Index(
            "uq_translation_artifacts_user_identity",
            "owner_id",
            "purpose",
            "source_hash",
            "source_locale",
            "target_locale",
            "engine_fingerprint",
            unique=True,
            postgresql_where=text("owner_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE")
    )
    purpose: Mapped[TranslationPurpose] = mapped_column(String(32), nullable=False)
    scope: Mapped[TranslationScope] = mapped_column(String(16), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_locale: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    target_locale: Mapped[str] = mapped_column(String(16), nullable=False)
    engine_fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    engine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    glossary_version: Mapped[str | None] = mapped_column(String(64))
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped[Profile | None] = relationship(back_populates="translation_artifacts")


class TranslationWork(Timestamped, Base):
    """Single authoritative execution state for one translation demand."""

    __tablename__ = "translation_work"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('title', 'excerpt', 'body', 'paragraph', 'ranking_title', "
            "'ranking_description', 'web_segment', 'caption')",
            name="ck_translation_work_purpose",
        ),
        CheckConstraint("scope IN ('shared', 'user')", name="ck_translation_work_scope"),
        CheckConstraint("length(engine_id) > 0", name="ck_translation_work_engine_id"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_translation_work_status",
        ),
        CheckConstraint(
            "(scope = 'shared' AND owner_id IS NULL) OR (scope = 'user' AND owner_id IS NOT NULL)",
            name="ck_translation_work_scope_owner",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status != 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_translation_work_running_lease",
        ),
        CheckConstraint("priority >= 0", name="ck_translation_work_priority"),
        CheckConstraint("length(source_text) > 0", name="ck_translation_work_source_text"),
        Index(
            "ix_translation_work_claim",
            "engine_fingerprint",
            "status",
            "available_at",
            "priority",
            "created_at",
        ),
        Index(
            "uq_translation_work_shared_identity",
            "purpose",
            "source_hash",
            "source_locale",
            "target_locale",
            "engine_fingerprint",
            unique=True,
            postgresql_where=text("owner_id IS NULL"),
        ),
        Index(
            "uq_translation_work_user_identity",
            "owner_id",
            "purpose",
            "source_hash",
            "source_locale",
            "target_locale",
            "engine_fingerprint",
            unique=True,
            postgresql_where=text("owner_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE")
    )
    purpose: Mapped[TranslationPurpose] = mapped_column(String(32), nullable=False)
    scope: Mapped[TranslationScope] = mapped_column(String(16), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_before: Mapped[str | None] = mapped_column(Text)
    context_after: Mapped[str | None] = mapped_column(Text)
    source_locale: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    target_locale: Mapped[str] = mapped_column(String(16), nullable=False)
    engine_fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    engine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    glossary_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[TranslationStatus] = mapped_column(
        String(16), nullable=False, default=TranslationStatus.PENDING
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)

    owner: Mapped[Profile | None] = relationship(back_populates="translation_work")


class UserContentState(Base):
    __tablename__ = "user_content_states"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    content_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contents.id", ondelete="CASCADE"), primary_key=True
    )
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offline_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[Profile] = relationship(back_populates="reading_states")
    content: Mapped[Content] = relationship(back_populates="reading_states")


class UserSavedContent(Base):
    __tablename__ = "user_saved_contents"
    __table_args__ = (
        UniqueConstraint("user_id", "content_id", name="uq_user_saved_contents_user_content"),
        Index("ix_user_saved_contents_user_saved", "user_id", "saved_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    content_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contents.id", ondelete="CASCADE"), nullable=False
    )
    collection_name: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[Profile] = relationship(back_populates="saved_contents")
    content: Mapped[Content] = relationship(back_populates="saved_by")


# Register identity tables when migration metadata imports the domain model module.
from .auth_models import (  # noqa: E402, F401
    ProfileAvatar,
    ReaderRefreshToken,
    ReaderSession,
    ReaderUser,
)
