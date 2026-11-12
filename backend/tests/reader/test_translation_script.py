"""Texts already in the target language are not sent to the model (mirrors the app's tests)."""

import pytest

from reader.translation.script import already_in_language
from reader.translation.titles import pending_rows


@pytest.mark.parametrize(
    ("text", "locale", "expected"),
    [
        ("让老音箱焕发新生：Home Assistant Connect AUX-2 首发体验", "zh-CN", True),
        ("繁體中文的標題", "zh-Hant", True),
        ("⟪READER_OPEN_0⟫ 南桥北桥不是桥 ⟪READER_CLOSE_0⟫", "zh-CN", True),
        ("Show HN: Ramasite, a Self-Contained CMS", "zh-CN", False),
        ("DeepSeek 4.1 flash is here and it is fast (快)", "zh-CN", False),
        ("日本語の記事を読む", "zh-CN", False),
        ("日本語の記事を読む", "ja", True),
        ("中文标题", "ja", False),
        ("한국어 기사 제목", "ko", True),
        ("美 연준 금리 동결", "zh-CN", False),
        ("美 연준 금리 동결", "ko", True),
        ("An English title", "en", False),
        ("2026 · 100%", "zh-CN", False),
        ("中文", None, False),
    ],
)
def test_already_in_language(text, locale, expected):
    assert already_in_language(text, locale) is expected


def test_titles_already_in_the_target_language_are_not_queued():
    rows = pending_rows(["少数派的中文标题", "An English title"], [("zh-CN", "f"), ("en", "f")])
    assert sorted((row["source_text"], row["target_locale"]) for row in rows) == [
        ("An English title", "en"),
        ("An English title", "zh-CN"),
        ("少数派的中文标题", "en"),
    ]
