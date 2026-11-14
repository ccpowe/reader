"""Whether a text is already in the language it would be translated into, by writing system.

Only targets with their own script (Chinese, Japanese, Korean) are judged; a Latin-script target
cannot tell English from French by letters alone, so those texts are always translated. A CJK
character weighs about as much as a word, so the target's characters are compared with the number
of words in other alphabets: "Home Assistant 首发体验" is Chinese, "DeepSeek is fast (快)" is not.

Mirrors the app's mobile/src/lib/language-script.ts (live segments and list titles).
"""

from __future__ import annotations

import regex

_HAN = regex.compile(r"\p{Script=Han}")
_KANA = regex.compile(r"[\p{Script=Hiragana}\p{Script=Katakana}]")
_HANGUL = regex.compile(r"\p{Script=Hangul}")
_WORD = regex.compile(r"[\p{Script=Latin}\p{Script=Cyrillic}\p{Script=Greek}]+")
# Link / markup placeholders of page segments (⟪READER_OPEN_0⟫) are not words of the text.
_PLACEHOLDER = regex.compile(r"\u27eaREADER_(?:(?:OPEN|CLOSE)_)?\d+\u27eb")


def already_in_language(text: str, locale: str | None) -> bool:
    if not locale:
        return False
    language = locale.lower().split("-")[0]
    text = _PLACEHOLDER.sub(" ", text)
    words = len(_WORD.findall(text))
    kana = len(_KANA.findall(text))
    hangul = len(_HANGUL.findall(text))
    if language in ("zh", "yue"):
        # Kana means Japanese and Hangul Korean, even when most characters are Han.
        return kana == 0 and len(_HAN.findall(text)) > words + hangul
    if language == "ja":
        return kana > 0 and len(_HAN.findall(text)) + kana > words + hangul
    if language == "ko":
        return hangul > words + kana
    return False
