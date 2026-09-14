"""Candidate-independent DOM card boundaries shared by inspection and validation.

The excluded flag follows article discovery landmarks, including navigation.
Continuation detection must apply its own landmark policy instead of treating
that flag as a reason to discard navigation controls.
"""

from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass(slots=True)
class ArticleStructure:
    excluded: bool
    in_article: bool
    heading_count: int
    has_link: bool
    contains_card: bool = False
    local_card: bool = False
    in_card: bool = False


def article_structure(soup: BeautifulSoup) -> dict[int, ArticleStructure]:
    """Find minimal local heading/link units without borrowing other cards' titles.

    Forward inheritance and reverse child aggregation avoid rescanning the
    same container per link. Heading counts saturate at two: a multi-heading
    collection is not itself evidence for an unrelated sibling control.
    """
    nodes = list(soup.find_all(True))
    state: dict[int, ArticleStructure] = {}
    for node in nodes:
        parent = state.get(id(node.parent))
        article = bool(parent and parent.in_article) or node.name == "article"
        excluded = (
            bool(parent and parent.excluded)
            or node.name == "nav"
            or (node.name in {"header", "footer"} and not article)
            or node.get("role") in {"navigation", "banner", "contentinfo"}
        )
        state[id(node)] = ArticleStructure(
            excluded,
            article,
            int(node.name in HEADINGS),
            node.name == "a" and node.has_attr("href"),
        )
    for node in reversed(nodes):
        info = state[id(node)]
        if info.excluded:
            continue
        info.local_card = (
            node.name == "article"
            or (node.name == "a" and node.has_attr("href") and info.heading_count > 0)
            or (
                node.name not in {"html", "body", "main", "ul", "ol"}
                and info.heading_count == 1
                and info.has_link
                and not info.contains_card
            )
        )
        if parent := state.get(id(node.parent)):
            parent.heading_count = min(2, parent.heading_count + info.heading_count)
            parent.has_link = parent.has_link or info.has_link
            parent.contains_card = parent.contains_card or info.local_card or info.contains_card
    for node in nodes:
        info = state[id(node)]
        parent = state.get(id(node.parent))
        info.in_card = info.local_card or bool(parent and parent.in_card)
    return state
