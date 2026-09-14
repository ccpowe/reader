"""Resolve collected self-reply roots before pagination, within the authorized scope."""

from sqlalchemy import and_, func, or_, select


def grouped_feed_entries(ranked_entries):
    entries = select(ranked_entries).where(ranked_entries.c.content_rank == 1).cte("feed_entries")
    child = entries.alias("child")
    parent = entries.alias("parent")
    # Author IDs are the evidence; handles can change. Never join on conversation ID.
    edges = (
        select(child.c.entry_id.label("child_id"), parent.c.entry_id.label("parent_id"))
        .join(
            parent,
            and_(
                child.c.source_id == parent.c.source_id,
                child.c.reply_id == parent.c.native_id,
                child.c.author_id == parent.c.author_id,
            ),
        )
        .where(
            child.c.source_kind == "x",
            parent.c.source_kind == "x",
            child.c.author_id.is_not(None),
            or_(child.c.reply_author_id.is_(None), child.c.reply_author_id == parent.c.author_id),
            func.coalesce(child.c.is_repost, "false") != "true",
            func.coalesce(parent.c.is_repost, "false") != "true",
        )
        .cte("x_edges")
    )
    # Walk downward from collected roots, rather than materializing every
    # leaf/ancestor pair (quadratic for a long chain). Cycles have no root and
    # are unreachable, so their entries safely fall back to standalone cards.
    roots = (
        select(entries.c.entry_id.label("leaf_id"), entries.c.entry_id.label("root_id"))
        .outerjoin(edges, edges.c.child_id == entries.c.entry_id)
        .where(edges.c.parent_id.is_(None))
        .cte("x_roots", recursive=True)
    )
    roots = roots.union(
        select(edges.c.child_id, roots.c.root_id).join(edges, edges.c.parent_id == roots.c.leaf_id)
    )
    resolved = (
        select(func.coalesce(roots.c.root_id, entries.c.entry_id).label("entry_id"))
        .select_from(entries)
        .outerjoin(roots, roots.c.leaf_id == entries.c.entry_id)
        .cte("resolved")
    )
    return (
        select(resolved.c.entry_id, func.count().label("loaded_count"))
        .group_by(resolved.c.entry_id)
        .cte("feed_groups")
    )
