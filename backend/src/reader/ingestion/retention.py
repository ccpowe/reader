"""Daily pruning of articles and scan history. Saved articles are never removed."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_OLD_ARTICLES = text(
    """
    DELETE FROM articles AS a
    USING (
        SELECT id, row_number() OVER (
            PARTITION BY source_id ORDER BY first_seen_at DESC, id DESC
        ) AS position
        FROM articles
        WHERE source_id IS NOT NULL
    ) AS ranked
    WHERE a.id = ranked.id
      AND ranked.position > :keep
      AND NOT EXISTS (SELECT 1 FROM saved_articles s WHERE s.article_id = a.id)
    """
)

# Items saved from a board belong to no source; once nobody keeps them, drop them.
_UNSAVED_BOARD_ITEMS = text(
    """
    DELETE FROM articles AS a
    WHERE a.source_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM saved_articles s WHERE s.article_id = a.id)
    """
)

_OLD_SCAN_RUNS = text(
    """
    DELETE FROM source_scan_runs AS r
    USING (
        SELECT id, row_number() OVER (
            PARTITION BY source_id ORDER BY started_at DESC, id DESC
        ) AS position
        FROM source_scan_runs
    ) AS ranked
    WHERE r.id = ranked.id AND ranked.position > :keep
    """
)


async def prune_articles(db: AsyncSession, keep_per_source: int) -> int:
    """Keep the newest `keep_per_source` articles of each source, plus every saved one."""
    removed = (await db.execute(_OLD_ARTICLES, {"keep": keep_per_source})).rowcount
    removed += (await db.execute(_UNSAVED_BOARD_ITEMS)).rowcount
    await db.commit()
    return removed


async def prune_scan_runs(db: AsyncSession, keep_per_source: int) -> int:
    removed = (await db.execute(_OLD_SCAN_RUNS, {"keep": keep_per_source})).rowcount
    await db.commit()
    return removed
