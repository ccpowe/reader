"""Reading queries: the feed, its update summary, single articles and saved articles.

Who may read what:
  - the feed shows articles of sources the user follows;
  - an article opens when the user follows its source or has saved it.

X self-reply threads are folded: a post whose thread root (set when it was stored)
is also in the result is hidden, and the root reports how many posts it stands for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, delete, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from reader.core.errors import AppError
from reader.models import Article, SavedArticle, Source, Subscription


@dataclass(frozen=True)
class FeedScope:
    """Which subscriptions and which time window a feed query covers.

    With no subscription or group: the enabled subscriptions shown on home, or all
    enabled ones with `everything` (agents briefing on every subscription).
    `since` asks for updates, so first-scan baseline items are left out.
    """

    subscription_id: UUID | None = None
    group: str | None = None
    everything: bool = False
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True)
class FeedRow:
    article: Article
    subscription: Subscription
    source: Source
    is_saved: bool
    thread_count: int


@dataclass(frozen=True)
class SummaryRow:
    subscription: Subscription
    source: Source
    count: int


@dataclass(frozen=True)
class ArticleView:
    article: Article
    subscription: Subscription | None
    source: Source | None
    is_saved: bool
    thread: list[Article]
    # Which posts of the thread the user saved.
    saved_in_thread: frozenset[UUID] = frozenset()


@dataclass(frozen=True)
class SavedRow:
    article: Article
    subscription: Subscription | None
    source: Source | None
    saved_at: datetime


def _article_not_found() -> AppError:
    return AppError(404, "article_not_found", "Article not found.")


# --- Feed ---------------------------------------------------------------------------


def _thread_root_id(article) -> ColumnElement:
    return article.extra["x"]["thread_root"].astext


def _in_window(article, scope: FeedScope) -> list[ColumnElement]:
    conditions = []
    if scope.since is not None:
        conditions += [article.first_seen_at >= scope.since, article.sequence.is_not(None)]
    if scope.until is not None:
        conditions.append(article.first_seen_at < scope.until)
    return conditions


def _folded_into_root(scope: FeedScope) -> ColumnElement[bool]:
    """This article continues an X thread whose root is in the same result."""
    root = aliased(Article)
    return exists().where(
        root.source_id == Article.source_id,
        root.native_id == _thread_root_id(Article),
        *_in_window(root, scope),
    )


def _thread_count(scope: FeedScope):
    reply = aliased(Article)
    replies = (
        select(func.count())
        .where(
            reply.source_id == Article.source_id,
            _thread_root_id(reply) == Article.native_id,
            *_in_window(reply, scope),
        )
        .scalar_subquery()
    )
    return 1 + replies


def _is_saved(user_id: UUID):
    return exists().where(SavedArticle.user_id == user_id, SavedArticle.article_id == Article.id)


async def _check_subscription(db: AsyncSession, user_id: UUID, subscription_id: UUID) -> None:
    owned = await db.scalar(
        select(Subscription.id).where(
            Subscription.id == subscription_id, Subscription.user_id == user_id
        )
    )
    if owned is None:
        raise AppError(404, "subscription_not_found", "Subscription not found.")


def _scope_conditions(user_id: UUID, scope: FeedScope) -> list[ColumnElement]:
    conditions: list[ColumnElement] = [Subscription.user_id == user_id]
    if scope.subscription_id is not None:
        conditions.append(Subscription.id == scope.subscription_id)
    else:
        conditions.append(Subscription.is_enabled.is_(True))
        if scope.group == "":
            # "" asks for the subscriptions without a group (stored group names are never empty).
            conditions.append(Subscription.group_name.is_(None))
        elif scope.group is not None:
            conditions.append(Subscription.group_name == scope.group)
        elif not scope.everything:
            conditions.append(Subscription.include_in_home.is_(True))
    conditions += _in_window(Article, scope)
    conditions.append(~_folded_into_root(scope))
    return conditions


async def feed_page(
    db: AsyncSession,
    user_id: UUID,
    scope: FeedScope,
    *,
    after: tuple[datetime, UUID] | None,
    limit: int,
) -> tuple[list[FeedRow], bool]:
    """Newest first by when Reader first saw the article; returns (rows, has_more)."""
    if scope.subscription_id is not None:
        await _check_subscription(db, user_id, scope.subscription_id)
    query = (
        select(
            Article,
            Subscription,
            Source,
            _is_saved(user_id).label("is_saved"),
            _thread_count(scope).label("thread_count"),
        )
        .join(Subscription, Subscription.source_id == Article.source_id)
        .join(Source, Source.id == Article.source_id)
        .where(*_scope_conditions(user_id, scope))
        .order_by(Article.first_seen_at.desc(), Article.id.desc())
        .limit(limit + 1)
    )
    if after is not None:
        at, article_id = after
        query = query.where(
            or_(
                Article.first_seen_at < at,
                and_(Article.first_seen_at == at, Article.id < article_id),
            )
        )
    rows = [FeedRow(*row) for row in await db.execute(query)]
    return rows[:limit], len(rows) > limit


async def feed_summary(db: AsyncSession, user_id: UUID, scope: FeedScope) -> list[SummaryRow]:
    """How many feed items each subscription has in the window, busiest first."""
    count = func.count(Article.id)
    rows = await db.execute(
        select(Subscription, Source, count)
        .join(Source, Source.id == Subscription.source_id)
        .join(Article, Article.source_id == Source.id)
        .where(*_scope_conditions(user_id, scope))
        .group_by(Subscription.id, Source.id)
        .order_by(count.desc(), Subscription.created_at)
    )
    return [SummaryRow(*row) for row in rows]


# --- One article ------------------------------------------------------------------


async def get_article(db: AsyncSession, user_id: UUID, article_id: UUID) -> ArticleView:
    article = await db.get(Article, article_id)
    if article is None:
        raise _article_not_found()
    subscription = source = None
    if article.source_id is not None:
        subscription = await db.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id, Subscription.source_id == article.source_id
            )
        )
        source = await db.get(Source, article.source_id)
    is_saved = await db.scalar(
        select(
            exists().where(SavedArticle.user_id == user_id, SavedArticle.article_id == article.id)
        )
    )
    if subscription is None and not is_saved:
        raise _article_not_found()
    thread = []
    if source is not None and source.kind == "x":
        conditions = [
            Article.source_id == article.source_id,
            _thread_root_id(Article) == article.native_id,
        ]
        if subscription is None:
            # Opened through "saved" only: show just the posts this user saved too.
            conditions.append(
                exists().where(
                    SavedArticle.user_id == user_id, SavedArticle.article_id == Article.id
                )
            )
        thread = list(
            await db.scalars(
                select(Article)
                .where(*conditions)
                .order_by(Article.published_at.asc().nulls_last(), Article.first_seen_at)
            )
        )
    saved_in_thread = frozenset(
        await db.scalars(
            select(SavedArticle.article_id).where(
                SavedArticle.user_id == user_id,
                SavedArticle.article_id.in_([post.id for post in thread]),
            )
        )
        if thread
        else ()
    )
    return ArticleView(article, subscription, source, bool(is_saved), thread, saved_in_thread)


# --- Saved ------------------------------------------------------------------------


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def saved_page(
    db: AsyncSession,
    user_id: UUID,
    *,
    q: str | None,
    after: tuple[datetime, UUID] | None,
    limit: int,
) -> tuple[list[SavedRow], bool]:
    """Most recently saved first; `q` matches title or summary."""
    query = (
        select(Article, Subscription, Source, SavedArticle.saved_at)
        .join(SavedArticle, SavedArticle.article_id == Article.id)
        .outerjoin(Source, Source.id == Article.source_id)
        .outerjoin(
            Subscription,
            and_(Subscription.source_id == Article.source_id, Subscription.user_id == user_id),
        )
        .where(SavedArticle.user_id == user_id)
        .order_by(SavedArticle.saved_at.desc(), Article.id.desc())
        .limit(limit + 1)
    )
    if q:
        pattern = _like(q)
        query = query.where(or_(Article.title.ilike(pattern), Article.summary.ilike(pattern)))
    if after is not None:
        at, article_id = after
        query = query.where(
            or_(
                SavedArticle.saved_at < at,
                and_(SavedArticle.saved_at == at, Article.id < article_id),
            )
        )
    rows = [SavedRow(*row) for row in await db.execute(query)]
    return rows[:limit], len(rows) > limit


async def save_article(db: AsyncSession, user_id: UUID, article_id: UUID) -> None:
    """Save an article from a followed source; saving twice changes nothing."""
    # The row lock keeps the daily clean-up from deleting it mid-save (it skips locked rows).
    followed = await db.scalar(
        select(Article.id)
        .join(Subscription, Subscription.source_id == Article.source_id)
        .where(Article.id == article_id, Subscription.user_id == user_id)
        .with_for_update(of=Article, key_share=True)
    )
    if followed is None:
        already = await db.scalar(
            select(SavedArticle.article_id).where(
                SavedArticle.user_id == user_id, SavedArticle.article_id == article_id
            )
        )
        if already is None:
            raise _article_not_found()
        return
    await db.execute(
        pg_insert(SavedArticle)
        .values(user_id=user_id, article_id=article_id)
        .on_conflict_do_nothing()
    )
    await db.commit()


async def unsave_article(db: AsyncSession, user_id: UUID, article_id: UUID) -> None:
    """Removing an article that is not saved is not an error."""
    await db.execute(
        delete(SavedArticle).where(
            SavedArticle.user_id == user_id, SavedArticle.article_id == article_id
        )
    )
    await db.commit()
