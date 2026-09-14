"""String enums used by persistence and source adapters."""

from enum import IntEnum, StrEnum


class SourceKind(StrEnum):
    RSS = "rss"
    WEB = "web"
    REDDIT = "reddit"
    YOUTUBE = "youtube"
    HACKER_NEWS = "hackernews"
    X = "x"


class SourceVisibility(StrEnum):
    SHARED = "shared"
    PRIVATE = "private"


class SourceStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"


class SyncPhase(StrEnum):
    """Runtime coverage state for a source scanner."""

    IDLE = "idle"
    CATCHING_UP = "catching_up"
    DEGRADED = "degraded"


class SyncRunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class WebFrontierStatus(StrEnum):
    PENDING = "pending"
    FETCHED = "fetched"
    UNCERTAIN = "uncertain"
    REJECTED = "rejected"
    FAILED = "failed"


class ContentKind(StrEnum):
    ARTICLE = "article"
    POST = "post"
    VIDEO = "video"
    PODCAST = "podcast"


class ExtractionStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class MediaType(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    EMBED = "embed"


class CacheStatus(StrEnum):
    EXTERNAL = "external"
    QUEUED = "queued"
    CACHED = "cached"
    FAILED = "failed"


class TranslationPurpose(StrEnum):
    """Product role of source text submitted for translation."""

    TITLE = "title"
    EXCERPT = "excerpt"
    BODY = "body"
    PARAGRAPH = "paragraph"
    RANKING_TITLE = "ranking_title"
    RANKING_DESCRIPTION = "ranking_description"
    WEB_SEGMENT = "web_segment"
    CAPTION = "caption"


class TranslationPriority(IntEnum):
    """Demand priority; larger values are claimed first."""

    HISTORICAL = 10
    INGESTION = 50
    PREFETCH = 100
    INTERACTIVE = 150


class TranslationScope(StrEnum):
    """Whether a translation can be shared between users."""

    SHARED = "shared"
    USER = "user"


class TranslationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TranslationProviderMode(StrEnum):
    """How a user's translation provider is selected."""

    APP_DEFAULT = "app_default"
    APP_MANAGED = "app_managed"
    BYOK = "byok"
