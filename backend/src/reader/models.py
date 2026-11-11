"""All Reader tables (14).

Core:      users, sources, subscriptions, articles, saved_articles
Caches:    translations, translation_preferences, board_snapshots
Machinery: sessions, refresh_tokens, source_scan_runs, provider_usage,
           translation_budget, worker_heartbeats
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column

from reader.core.db import Base

SOURCE_KINDS = ("rss", "youtube", "x", "reddit")
SOURCE_STATUSES = ("pending", "active", "paused")
ARTICLE_KINDS = ("article", "post", "video")
SCAN_RUN_STATUSES = ("running", "success", "failed")
TRANSLATION_PURPOSES = (
    "title",
    "paragraph",
    "board_title",
    "board_description",
    "web_segment",
    "caption",
)
TRANSLATION_STATUSES = ("pending", "running", "succeeded", "failed")
BUDGET_PERIODS = ("minute", "day")


def _one_of(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# --- Accounts ---------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    # Up to a few MB; deferred so ordinary user lookups never load it.
    avatar_data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    avatar_content_type: Mapped[str | None] = mapped_column(String(32))
    avatar_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Session(Base):
    """One signed-in device; revoking it ends every token issued to it."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base):
    """Single-use refresh token, stored as a hash; reuse revokes the session."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_session_id", "session_id"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Sources, subscriptions and articles -------------------------------------


class Source(Base):
    """An RSS feed, YouTube channel, X account or subreddit, shared by everyone.

    The scan columns replace the former source_sync_states table.
    """

    __tablename__ = "sources"
    __table_args__ = (
        _one_of("kind", SOURCE_KINDS, "ck_sources_kind"),
        _one_of("status", SOURCE_STATUSES, "ck_sources_status"),
        Index("ix_sources_next_scan_at", "next_scan_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(300))
    icon_url: Mapped[str | None] = mapped_column(Text)
    # Kind-specific settings, e.g. {"channel_id": ...} or {"handle": ...}.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    # Highest new-content sequence handed out for this source.
    latest_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )

    # Scanning
    initial_scan_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_new_items: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    unchanged_scans: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    # The last scan hit its item limit before reaching known articles.
    may_have_gap: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "source_id", name="uq_subscriptions_user_source"),
        Index("ix_subscriptions_source_id", "source_id"),
        Index("ix_subscriptions_user_group", "user_id", "is_enabled", "group_name"),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    custom_name: Mapped[str | None] = mapped_column(String(300))
    group_name: Mapped[str | None] = mapped_column(String(120))
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    include_in_home: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # New-content badge = articles of this source with a higher sequence.
    last_viewed_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Article(Base):
    """One item from one source; not deduplicated across sources.

    `source_id` is empty for items saved from a board. `sequence` is empty for
    the baseline items of a first scan, so they never count as new.
    """

    __tablename__ = "articles"
    __table_args__ = (
        _one_of("kind", ARTICLE_KINDS, "ck_articles_kind"),
        UniqueConstraint("source_id", "native_id", name="uq_articles_source_native"),
        Index("ix_articles_source_first_seen", "source_id", "first_seen_at", "id"),
        Index("ix_articles_source_sequence", "source_id", "sequence"),
        Index(
            "uq_articles_board_url",
            "url_hash",
            unique=True,
            postgresql_where=text("source_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    source_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE")
    )
    native_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    author: Mapped[str | None] = mapped_column(String(300))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    # [{"type": "image", "url": ..., "width": ..., ...}, ...]
    media: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Source-specific data, e.g. X quote/reply relations or board metrics.
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Hash of the fields above, to skip rewriting unchanged items on rescans.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sequence: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = _updated_at()


class SavedArticle(Base):
    __tablename__ = "saved_articles"
    __table_args__ = (
        Index("ix_saved_articles_user_saved", "user_id", "saved_at"),
        # Retention checks "is this article saved by anyone" per article.
        Index("ix_saved_articles_article", "article_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceScanRun(Base):
    """Recent scan history per source, for diagnosing why it stopped updating."""

    __tablename__ = "source_scan_runs"
    __table_args__ = (
        _one_of("status", SCAN_RUN_STATUSES, "ck_source_scan_runs_status"),
        Index("ix_source_scan_runs_source_started", "source_id", "started_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_read: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    new_items: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)


# --- Translation --------------------------------------------------------------


class Translation(Base):
    """Both the cache and the work queue: a row moves pending → running → done."""

    __tablename__ = "translations"
    __table_args__ = (
        _one_of("purpose", TRANSLATION_PURPOSES, "ck_translations_purpose"),
        _one_of("status", TRANSLATION_STATUSES, "ck_translations_status"),
        UniqueConstraint(
            "purpose",
            "source_hash",
            "target_locale",
            "engine_fingerprint",
            name="uq_translations_identity",
        ),
        Index("ix_translations_status_created", "status", "created_at"),
        Index("ix_translations_last_used", "last_used_at"),
    )

    id: Mapped[UUID] = _uuid_pk()
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_locale: Mapped[str] = mapped_column(String(16), nullable=False)
    engine_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    error_code: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = _created_at()


class TranslationPreference(Base):
    __tablename__ = "translation_preferences"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    target_locale: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # Empty means the server's default engine.
    engine_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = _updated_at()


class TranslationBudget(Base):
    """Characters actually sent to a model, per minute and per day, server-wide."""

    __tablename__ = "translation_budget"
    __table_args__ = (_one_of("period", BUDGET_PERIODS, "ck_translation_budget_period"),)

    period: Mapped[str] = mapped_column(String(8), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    chars_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )


# --- Boards and external usage -------------------------------------------------


class BoardSnapshot(Base):
    """The latest copy of one board variant, refreshed when opened after expiry."""

    __tablename__ = "board_snapshots"
    __table_args__ = (Index("ix_board_snapshots_fetched_at", "fetched_at"),)

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    board: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    items: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderUsage(Base):
    """Units used of an external service in one window (e.g. YouTube per day)."""

    __tablename__ = "provider_usage"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    """Each background loop records its last run; /worker-ready reads these."""

    __tablename__ = "worker_heartbeats"

    loop_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    expected_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime] = _updated_at()
