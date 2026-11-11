"""Reader rich-text v1 contract; never logs source or translated content."""

import re
from collections import Counter

FORMAT_VERSION = "reader-rich-text-v1"
TOKEN = re.compile(r"⟪READER_(?:(OPEN|CLOSE)_)?(\d+)⟫")


def validate_tokens(source: str, translated: str) -> str | None:
    expected = Counter(match.group() for match in TOKEN.finditer(source))
    actual = Counter(match.group() for match in TOKEN.finditer(translated))
    if expected != actual:
        if actual - expected:
            return "unknown_or_duplicate_token"
        return "missing_token"
    stack: list[str] = []
    for match in TOKEN.finditer(translated):
        kind, index = match.groups()
        if kind == "OPEN":
            stack.append(index)
        elif kind == "CLOSE":
            if not stack or stack.pop() != index:
                return "invalid_token_nesting"
    return "unclosed_token" if stack else None


def uses_reader_format(purpose: str | None, text: str) -> bool:
    return purpose == "web_segment" or (purpose == "paragraph" and TOKEN.search(text) is not None)


def token_diagnostics(source: str, translated: str) -> dict[str, object]:
    expected = Counter(match.group() for match in TOKEN.finditer(source))
    actual = Counter(match.group() for match in TOKEN.finditer(translated))
    return {
        "expected_count": sum(expected.values()),
        "actual_count": sum(actual.values()),
        "missing": list((expected - actual).keys())[:16],
        "unknown_or_duplicate": list((actual - expected).keys())[:16],
    }
