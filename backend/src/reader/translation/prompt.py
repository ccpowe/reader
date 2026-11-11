"""The translation prompt, request batching and reply parsing.

A request is a JSON list of {item_id, text} in reading order; the reply must be
{"items": [{item_id, translated_text}]} with exactly one entry per input item.
Web segments (and paragraphs carrying them) contain ⟪READER_…⟫ markers that must
survive translation unchanged; `rich_text.validate_tokens` checks them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from reader.translation.engines import EngineError
from reader.translation.rich_text import FORMAT_VERSION, TOKEN

_BASE = (
    "You are a precise translation service. Translate every input item into the "
    "requested target language without commentary. Preserve URLs, code, handles, "
    "identifiers, product names and repository names. Preserve meaning rather than "
    "source-language word order. Items are in reading order and may be read together "
    "for continuity, but return exactly one translation for every input item; never "
    "omit, merge or invent item IDs. Return only a valid json object with an items "
    "array; each item has item_id and translated_text."
)

_PURPOSE_RULES = {
    "title": "These are headlines: keep them short and natural.",
    "caption": "Use concise, natural spoken language suitable for a two-line subtitle.",
    "board_title": "Preserve repository names, product names, handles and identifiers.",
    "board_description": "Translate the description naturally; preserve technical terms.",
}

_MARKER_RULE = (
    "Reader rich-text-v1: preserve every ⟪READER_n⟫, ⟪READER_OPEN_n⟫ and ⟪READER_CLOSE_n⟫ "
    "token exactly once and verbatim, with the same nesting. Translate the words between "
    "paired tokens; never translate or omit tokens. Each item's expected_tokens lists the "
    "exact markers its translated_text must contain. For example, 'Costs are "
    "⟪READER_OPEN_0⟫expected⟪READER_CLOSE_0⟫ to rise.' becomes "
    "'成本⟪READER_OPEN_0⟫预计⟪READER_CLOSE_0⟫会上升。' Do not add markers absent from the "
    "input. If an item has validation_feedback, the previous attempt lost or changed "
    "markers: check every expected token before returning. Preserve links, code and "
    "interface identifiers."
)


@dataclass(frozen=True)
class PromptItem:
    item_id: str
    text: str
    # The previous attempt broke the markers; say so in the prompt.
    marker_feedback: bool = False


def uses_markers(purpose: str, text: str) -> bool:
    return purpose == "web_segment" or (purpose == "paragraph" and TOKEN.search(text) is not None)


def build_messages(
    items: Sequence[PromptItem], purpose: str, target_locale: str
) -> tuple[str, str]:
    """(system prompt, user message) for one batch of one purpose."""
    rules = [_BASE]
    if purpose in _PURPOSE_RULES:
        rules.append(_PURPOSE_RULES[purpose])
    payload = []
    for item in items:
        entry: dict = {"item_id": item.item_id, "text": item.text}
        if uses_markers(purpose, item.text):
            tokens = [match.group() for match in TOKEN.finditer(item.text)]
            entry["format"] = FORMAT_VERSION
            entry["expected_tokens"] = tokens
            if item.marker_feedback:
                entry["validation_feedback"] = {
                    "code": "placeholder_mismatch",
                    "required_tokens": tokens,
                }
        payload.append(entry)
    if any(uses_markers(purpose, item.text) for item in items):
        rules.append(_MARKER_RULE)
    user = json.dumps(
        {"target_locale": target_locale, "purpose": purpose, "items": payload},
        ensure_ascii=False,
    )
    return " ".join(rules), user


def parse_reply(content: str, items: Sequence[PromptItem]) -> dict[str, str]:
    """item_id -> translation; the whole batch is rejected unless every item came back."""
    try:
        data = json.loads(content)
        results = data["items"]
        if not isinstance(results, list):
            raise ValueError
        translated: dict[str, str] = {}
        for result in results:
            item_id, text = result["item_id"], result["translated_text"]
            if not isinstance(item_id, str) or not isinstance(text, str) or not text.strip():
                raise ValueError
            if item_id in translated:
                raise ValueError
            translated[item_id] = text.strip()
    except (ValueError, KeyError, TypeError):
        raise EngineError(
            "invalid_response", "The model reply was not valid JSON.", isolate=True
        ) from None
    if set(translated) != {item.item_id for item in items}:
        raise EngineError(
            "incomplete_response", "The model did not translate every item.", isolate=True
        )
    return translated


def batches(
    items: Sequence[PromptItem], *, max_items: int, max_chars: int
) -> Iterator[list[PromptItem]]:
    """Consecutive groups within both limits; a single long item is a batch of its own."""
    batch: list[PromptItem] = []
    chars = 0
    for item in items:
        if batch and (len(batch) >= max_items or chars + len(item.text) > max_chars):
            yield batch
            batch, chars = [], 0
        batch.append(item)
        chars += len(item.text)
    if batch:
        yield batch
