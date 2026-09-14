"""Small allow-list sanitizer for untrusted reader HTML."""

from __future__ import annotations

from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

_SAFE_HTML_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "figcaption",
    "figure",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
_DROP_HTML_TAGS = {
    "base",
    "button",
    "embed",
    "form",
    "iframe",
    "input",
    "link",
    "meta",
    "object",
    "option",
    "script",
    "select",
    "style",
    "textarea",
}
_SAFE_HTML_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"alt", "height", "src", "title", "width"},
}


def sanitize_html(html: str | None) -> str | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for element in list(soup.find_all(tuple(_DROP_HTML_TAGS))):
        element.decompose()
    for element in list(soup.find_all(True)):
        if not isinstance(element, Tag):
            continue
        if element.name not in _SAFE_HTML_TAGS:
            element.unwrap()
            continue
        allowed = _SAFE_HTML_ATTRIBUTES.get(element.name, set())
        element.attrs = {
            key: value
            for key, value in element.attrs.items()
            if key.lower() in allowed and not key.lower().startswith("on")
        }
        for attribute in ("href", "src"):
            value = element.get(attribute)
            if isinstance(value, str) and not _is_safe_embedded_url(value):
                del element[attribute]
    return soup.decode_contents()


def _is_safe_embedded_url(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    parsed = urlsplit(value)
    return not parsed.scheme or parsed.scheme.lower() in {"http", "https", "mailto"}
