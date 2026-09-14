from app.ingestion.site_icons import discover_site_icon_url


def test_site_icon_prefers_apple_touch_icon_and_resolves_relative_urls() -> None:
    html = """
    <link rel="icon" href="/favicon.svg">
    <link rel="apple-touch-icon" href="icons/touch.png">
    """

    assert discover_site_icon_url(html, "https://example.com/blog") == (
        "https://example.com/icons/touch.png"
    )


def test_site_icon_falls_back_to_the_origin_favicon() -> None:
    assert discover_site_icon_url("<html></html>", "https://example.com/feed.xml") == (
        "https://example.com/favicon.ico"
    )


def test_site_icon_ignores_non_http_link_targets() -> None:
    html = '<link rel="icon" href="data:image/svg+xml,icon">'

    assert discover_site_icon_url(html, "https://example.com/") == "https://example.com/favicon.ico"
