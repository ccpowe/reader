"""Discover a stable, site-level icon from public HTML."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag


def discover_site_icon_url(html: str, page_url: str) -> str | None:
    """Return the best declared site icon, resolved against ``page_url``.

    Site icons are intentionally different from ``og:image``: the latter is
    usually article artwork, while these ``link`` relations represent the site
    identity shown in a browser tab.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = [link for link in soup.find_all("link", href=True) if isinstance(link, Tag)]
    for relation in (
        "apple-touch-icon",
        "apple-touch-icon-precomposed",
        "icon",
        "shortcut icon",
        "mask-icon",
    ):
        for link in links:
            rel = " ".join(str(value).lower() for value in link.get("rel", []))
            if relation not in rel:
                continue
            resolved = _http_url(urljoin(page_url, str(link["href"]).strip()))
            if resolved is not None:
                return resolved
    return favicon_fallback_url(page_url)


def favicon_fallback_url(page_url: str) -> str | None:
    """Build the conventional root favicon URL without downloading the image."""
    parsed = urlsplit(page_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "/favicon.ico", "", ""))


def _http_url(url: str) -> str | None:
    parsed = urlsplit(url)
    return url if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else None
