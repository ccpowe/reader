"""Board routes and saving a board entry."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from reader.accounts.auth import CurrentUser, current_user
from reader.boards.fetchers import BoardItem
from reader.boards.service import Boards, BoardView, Target, resolve
from reader.core.db import get_session
from reader.core.errors import AppError, error_responses
from reader.core.server_access import document_server_token
from reader.core.settings import Settings, get_settings
from reader.ingestion.items import Item, Media
from reader.ingestion.scanner import url_hash
from reader.models import Article, BoardSnapshot, SavedArticle
from reader.translation.titles import TitleTranslation, title_translations

boards_router = APIRouter(
    prefix="/boards", tags=["boards"], dependencies=[Depends(document_server_token)]
)
saved_board_router = APIRouter(
    prefix="/saved", tags=["reading"], dependencies=[Depends(document_server_token)]
)

BoardName = Literal["hn", "github", "techtwitter", "product_hunt", "reddit"]
_BOARD_ERRORS = (
    (422, "invalid_variant"),
    (422, "invalid_subreddit"),
    (502, "board_unavailable"),
    (503, "board_loading"),
)


def boards_of(app) -> Boards:
    """One Boards per API process, created on first use. Called on the event loop with
    no await between the check and the assignment, so two cannot be created."""
    boards = getattr(app.state, "boards", None)
    if boards is None:
        factory = app.state.session_factory
        if factory is None:
            raise AppError(503, "database_unavailable", "The database is not configured.")
        boards = app.state.boards = Boards(factory, get_settings())
    return boards


async def get_boards(request: Request) -> Boards:
    return boards_of(request.app)


class TranslatedText(BaseModel):
    text: str
    engine_fingerprint: str


class BoardEntry(BoardItem):
    is_saved: bool = False
    # Finished translations for your preference; ask POST /translations
    # (purposes board_title / board_description) for missing ones.
    title_translation: TranslatedText | None = None
    description_translation: TranslatedText | None = None


class BoardResponse(BaseModel):
    board: str
    variant: str
    subreddit: str | None
    # When the shown items were fetched; empty while a board has never loaded.
    fetched_at: datetime | None
    age_seconds: int | None
    # Older than the board's refresh interval (the latest fetch failed, is still
    # running, or the upstream asked us to pause).
    stale: bool
    error_code: str | None
    paused_until: datetime | None
    items: list[BoardEntry]


def _out(found: TitleTranslation | None) -> TranslatedText | None:
    return (
        TranslatedText(text=found.text, engine_fingerprint=found.engine_fingerprint)
        if found
        else None
    )


async def _response(
    target: Target, view: BoardView, db: AsyncSession, user: CurrentUser, settings: Settings
) -> BoardResponse:
    items = view.items
    hashes = {url_hash(item.url) for item in items}
    saved = (
        set(
            await db.scalars(
                select(Article.url_hash)
                .join(SavedArticle, SavedArticle.article_id == Article.id)
                .where(
                    SavedArticle.user_id == user.id,
                    Article.source_id.is_(None),
                    Article.url_hash.in_(hashes),
                )
            )
        )
        if hashes
        else set()
    )
    titles = await title_translations(
        db, user.id, settings, [item.title for item in items], "board_title"
    )
    descriptions = await title_translations(
        db,
        user.id,
        settings,
        [item.description for item in items if item.description],
        "board_description",
    )
    now = datetime.now(UTC)
    return BoardResponse(
        board=target.board,
        variant=target.variant,
        subreddit=target.subreddit,
        fetched_at=view.fetched_at,
        age_seconds=int((now - view.fetched_at).total_seconds()) if view.fetched_at else None,
        stale=view.stale,
        error_code=view.error_code if view.stale else None,
        paused_until=view.paused_until,
        items=[
            BoardEntry(
                **item.model_dump(),
                is_saved=url_hash(item.url) in saved,
                title_translation=_out(titles.get(item.title)),
                description_translation=_out(descriptions.get(item.description or "")),
            )
            for item in items
        ],
    )


_VARIANT_HELP = (
    "hn: top · techtwitter: trending · github: daily|weekly|monthly · "
    "product_hunt: today|yesterday (Los Angeles days) · "
    "reddit: hot|rising|top-day|top-week|top-month|top-year. Default: the first."
)


@boards_router.get(
    "/{board}", response_model=BoardResponse, responses=error_responses(*_BOARD_ERRORS)
)
async def get_board(
    board: BoardName,
    variant: str | None = Query(default=None, max_length=32, description=_VARIANT_HELP),
    subreddit: str | None = Query(default=None, max_length=32, description="Reddit only."),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
    boards: Boards = Depends(get_boards),
):
    """The board now; fetched on the spot when expired (waits up to ~10 s, then shows
    the previous copy with `stale` and its age)."""
    target = resolve(settings, board, variant, subreddit)
    await db.rollback()  # no connection held while the upstream answers
    view = await boards.get(target)
    return await _response(target, view, db, user, settings)


@boards_router.post(
    "/{board}/refresh", response_model=BoardResponse, responses=error_responses(*_BOARD_ERRORS)
)
async def refresh_board(
    board: BoardName,
    variant: str | None = Query(default=None, max_length=32, description=_VARIANT_HELP),
    subreddit: str | None = Query(default=None, max_length=32, description="Reddit only."),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
    boards: Boards = Depends(get_boards),
):
    """Fetch again regardless of age; at most once a minute per board (otherwise the
    current copy is returned)."""
    target = resolve(settings, board, variant, subreddit)
    await db.rollback()
    view = await boards.get(target, refresh=True)
    return await _response(target, view, db, user, settings)


# --- Saving -----------------------------------------------------------------------


class SaveFromBoardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board: BoardName
    variant: str | None = Field(default=None, max_length=32)
    subreddit: str | None = Field(default=None, max_length=32)
    # The entry's `url`; the content is copied from the stored board, not the request.
    url: str = Field(min_length=1, max_length=4096)


class SavedFromBoard(BaseModel):
    article_id: str


def _article_from(board: str, entry: BoardItem) -> dict:
    item = Item(
        native_id=url_hash(entry.url),
        kind="post" if board == "techtwitter" else "article",
        title=entry.title[:1000],
        url=entry.url,
        published_at=entry.published_at,
        author=(entry.author or entry.author_handle or None),
        summary=entry.description,
        body_html=html.escape(entry.body_text).replace("\n", "<br>") if entry.body_text else None,
        media=tuple(Media(url) for url in entry.image_urls),
        extra={"board": {"name": board, "item": entry.model_dump(mode="json")}},
    )
    return {
        "source_id": None,
        "native_id": item.native_id,
        "url": item.url,
        "url_hash": item.native_id,
        "kind": item.kind,
        "title": item.title,
        "author": item.author[:300] if item.author else None,
        "published_at": item.published_at,
        "summary": item.summary,
        "body_html": item.body_html,
        "media": [media.to_json() for media in item.media],
        "extra": item.extra,
        "content_hash": item.content_hash(),
    }


@saved_board_router.post(
    "/from-board",
    response_model=SavedFromBoard,
    responses=error_responses(
        (404, "board_item_not_found"), (422, "invalid_variant"), (422, "invalid_subreddit")
    ),
)
async def save_from_board(
    body: SaveFromBoardRequest,
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
):
    """Save an entry of a board as an article that belongs to no source. Saving the
    same link again returns the same article."""
    target = resolve(settings, body.board, body.variant, body.subreddit)
    snapshot = await db.get(BoardSnapshot, target.key)
    entry = next(
        (
            BoardItem.model_validate(raw)
            for raw in (snapshot.items if snapshot else [])
            if raw.get("url") == body.url
        ),
        None,
    )
    if entry is None:
        raise AppError(404, "board_item_not_found", "That entry is not on the board now.")
    values = _article_from(body.board, entry)
    # One statement that inserts the article or locks the existing one, so the daily
    # clean-up (which skips locked rows) cannot delete it before it is saved.
    statement = pg_insert(Article).values(**values)
    article_id: UUID = await db.scalar(
        statement.on_conflict_do_update(
            index_elements=[Article.url_hash],
            index_where=Article.source_id.is_(None),
            set_={"url_hash": statement.excluded.url_hash},
        ).returning(Article.id)
    )
    await db.execute(
        pg_insert(SavedArticle)
        .values(user_id=user.id, article_id=article_id)
        .on_conflict_do_nothing()
    )
    await db.commit()
    return SavedFromBoard(article_id=str(article_id))
