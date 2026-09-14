import asyncio

import httpx
import pytest

from app.ingestion.feed_discovery import discover_web_feed
from app.ingestion.url_safety import PublicAsyncClient, UnsafeSourceUrl
from app.ingestion.web_feed import configured_web_feed_url, web_feed_probe_required

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<title>Research</title><link>https://example.com/research</link><description>News</description>
<item><title>A new article</title><link>https://example.com/research/one</link></item>
</channel></rss>"""
EMPTY_RSS = b"""<rss version="2.0"><channel><title>Research</title>
<link>https://example.com/</link><description>News</description></channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<title>Research</title><id>https://example.com/research</id>
<updated>2026-09-10T00:00:00Z</updated><entry><title>One</title>
<id>https://example.com/one</id><link href="https://example.com/one"/>
<updated>2026-09-10T00:00:00Z</updated></entry></feed>"""
EMPTY_ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Research</title>
<id>https://example.com/research</id><updated>2026-09-10T00:00:00Z</updated></feed>"""


def response(url, body=b"", *, status=200, final_url=None, content_type="text/html"):
    return httpx.Response(
        status,
        content=body,
        headers={"Content-Type": content_type},
        request=httpx.Request("GET", final_url or url),
    )


class Fetcher:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __call__(self, client, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.routes.get(url, response(url, status=404))
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as instance:
        yield instance


@pytest.mark.parametrize("body", [RSS, ATOM, EMPTY_RSS, EMPTY_ATOM])
async def test_input_itself_can_be_a_feed_including_valid_empty_feeds(client, body):
    url = "https://example.com/research"
    fetch = Fetcher({url: response(url, body)})

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.status == "found"
    assert result.feed_url == url
    assert result.evidence["feed_version"].startswith(("rss", "atom"))
    assert len(fetch.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"<html><title>Not a feed</title></html>",
        b"<document><item>one</item></document>",
        b'<rss version="2.0"><channel><title>unfinished',
        b'<rss version="2.0"/>',
    ],
)
async def test_html_xml_and_malformed_empty_feed_are_not_feeds(client, body):
    url = "https://example.com/research"
    fetch = Fetcher({url: response(url, body, content_type="application/rss+xml")})

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.status == "not_found"
    assert result.feed_url is None
    assert len(fetch.calls) == 7


@pytest.mark.parametrize(
    "entry",
    [
        "<title>No link</title>",
        "<link>/article</link>",
        "<title>Unsafe</title><link>http://127.0.0.1/article</link>",
    ],
)
async def test_nonempty_feed_requires_a_usable_article_pair(client, entry):
    url = "https://example.com/blog"
    body = f'<rss version="2.0"><channel><title>News</title><item>{entry}</item></channel></rss>'
    fetch = Fetcher({url: response(url, body.encode())})
    assert (await discover_web_feed(url, client, fetch=fetch)).status == "not_found"


async def test_relative_article_link_in_feed_is_usable(client):
    url = "https://example.com/blog/feed"
    body = RSS.replace(b"https://example.com/research/one", b"../article")
    fetch = Fetcher({url: response(url, body)})
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "found"
    assert result.evidence["samples"][0]["url"] == "https://example.com/article"


@pytest.mark.parametrize("feed_status, expected", [(200, "found"), (503, "retry")])
async def test_body_metadata_feed_declaration_is_verified_before_guessing(
    client, feed_status, expected
):
    url = "https://example.com/news"
    feed_url = url + "/rss"
    html = b"""<html><head><title>News</title></head><body><main>Updates</main>
      <link rel="alternate" type="application/rss+xml" href="/news/rss">
    </body></html>"""
    fetch = Fetcher(
        {url: response(url, html), feed_url: response(feed_url, RSS, status=feed_status)}
    )

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.status == expected
    assert result.feed_url == (feed_url if expected == "found" else None)
    assert [call[0] for call in fetch.calls[:2]] == [url, feed_url]
    assert len(fetch.calls) == (2 if expected == "found" else 7)
    assert result.evidence["attempts"][1]["origin"] == "document_alternate"


@pytest.mark.parametrize("category", ["research", "security"])
async def test_query_selected_column_never_guesses_an_unscoped_site_feed(client, category):
    url = f"https://example.com/?category={category}"
    fetch = Fetcher(
        {
            url: response(url, b"<html>News</html>"),
            "https://example.com/feed": response("https://example.com/feed", RSS),
        }
    )
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "not_found" and len(fetch.calls) == 1


async def test_declared_feeds_precede_body_anchors_and_keep_dom_order(client):
    url = "https://example.com/research"
    html = b"""<html><head>
    <link rel="alternate" type="application/rss+xml" href="/first.xml">
    <link rel="alternate" type="application/atom+xml" href="/second.xml">
    </head><body><a href="/body.xml">RSS</a></body></html>"""
    fetch = Fetcher(
        {
            url: response(url, html),
            "https://example.com/first.xml": response("https://example.com/first.xml", RSS),
            "https://example.com/second.xml": response("https://example.com/second.xml", ATOM),
        }
    )

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.feed_url == "https://example.com/first.xml"
    assert [call[0] for call in fetch.calls] == [url, result.feed_url]


async def test_candidates_are_verified_instead_of_trusting_extension_or_mime(client):
    url = "https://example.com/"
    html = b"""<head><link rel="alternate" type="application/rss+xml" href="/fake.rss">
    <link rel="alternate" type="application/atom+xml" href="/actual"></head>"""
    fetch = Fetcher(
        {
            url: response(url, html),
            "https://example.com/fake.rss": response(
                "https://example.com/fake.rss",
                b"<html>not feed</html>",
                content_type="application/rss+xml",
            ),
            "https://example.com/actual": response("https://example.com/actual", ATOM),
        }
    )

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.feed_url == "https://example.com/actual"
    assert result.evidence["attempts"][1]["result"] == "not_feed"


async def test_relative_feed_and_html_base_use_final_redirect_location(client):
    url = "https://example.com/research"
    final_page = "https://www.example.com/research/"
    candidate = "https://www.example.com/research/feeds/news.xml"
    final_feed = "https://cdn.example.com/news.xml"
    html = b"""<head><base href="feeds/">
    <link rel="ALTERNATE stylesheet" type="application/atom+xml; charset=utf-8"
    href="news.xml#latest"></head>"""
    fetch = Fetcher(
        {
            url: response(url, html, final_url=final_page),
            candidate: response(candidate, ATOM, final_url=final_feed),
        }
    )

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.feed_url == final_feed
    assert [call[0] for call in fetch.calls] == [url, candidate]


async def test_explicit_public_cross_domain_feed_is_allowed(client):
    url = "https://example.com/research"
    feed_url = "https://feeds.example.net/research.xml"
    fetch = Fetcher(
        {
            url: response(
                url,
                f'<head><link rel="alternate" type="application/rss+xml" '
                f'href="{feed_url}"></head>'.encode(),
            ),
            feed_url: response(feed_url, RSS),
        }
    )

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.feed_url == feed_url


async def test_explicit_anchor_and_comment_feed_exclusion(client):
    url = "https://example.com/research"
    html = b"""<head>
    <link rel="alternate" type="application/rss+xml" href="/comments/feed">
    <link rel="alternate" type="application/rss+xml" href="/discussion" title="Comments RSS">
    </head><body><a href="/comments.rss">RSS</a>
    <a href="/subscribe">Subscribe</a><a href="/updates" title="RSS Feed">Updates</a></body>"""
    feed_url = "https://example.com/updates"
    fetch = Fetcher({url: response(url, html), feed_url: response(feed_url, RSS)})

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.feed_url == feed_url
    assert [call[0] for call in fetch.calls] == [url, feed_url]


async def test_unsafe_candidates_are_never_requested_and_base_does_not_override(client):
    url = "https://example.com/research"
    unsafe = [
        "http://127.0.0.1/rss",
        "http://10.0.0.1/rss",
        "http://[::1]/rss",
        "http://localhost/rss",
        "https://user:secret@example.com/rss",
        "javascript:alert(1)",
        "data:text/plain,rss",
    ]
    links = "".join(
        f'<link rel="alternate" type="application/rss+xml" href="{href}">' for href in unsafe
    )
    html = f'<head><base href="http://127.0.0.1/">{links}</head><a href="feed">RSS</a>'
    feed_url = "https://example.com/feed"
    fetch = Fetcher({url: response(url, html.encode()), feed_url: response(feed_url, RSS)})

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.feed_url == feed_url
    assert [call[0] for call in fetch.calls] == [url, feed_url]
    assert "secret" not in str(result.evidence)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://localhost/", "file:///rss", "https://user:secret@example.com/"],
)
async def test_unsafe_source_is_rejected_without_fetch(client, url):
    fetch = Fetcher({})
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "not_found"
    assert result.evidence["reason"] == "unsafe_source_url"
    assert fetch.calls == []


async def test_guess_paths_remain_inside_column_and_never_use_site_wide_feed(client):
    url = "https://example.com/research"
    fetch = Fetcher({url: response(url, b"<html>No advertised feeds</html>")})

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.status == "not_found"
    assert [call[0] for call in fetch.calls] == [
        url,
        "https://example.com/research/feed",
        "https://example.com/research/rss.xml",
        "https://example.com/research/atom.xml",
        "https://example.com/research/rss",
        "https://example.com/research/feed.xml",
        "https://example.com/research/atom",
    ]


async def test_column_redirected_to_home_does_not_guess_whole_site_feed(client):
    url = "https://example.com/research"
    fetch = Fetcher({url: response(url, b"<html></html>", final_url="https://example.com/")})
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "not_found"
    assert len(fetch.calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_root_temporary_http_failure_requires_retry(client, status):
    url = "https://example.com/"
    fetch = Fetcher({url: response(url, status=status)})
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "retry"
    assert len(fetch.calls) == 7


async def test_declared_feed_temporary_failure_requires_retry_without_valid_alternative(client):
    url = "https://example.com/"
    feed_url = "https://example.com/rss"
    fetch = Fetcher(
        {
            url: response(
                url, b'<head><link rel="alternate" type="application/rss+xml" href="/rss"></head>'
            ),
            feed_url: response(feed_url, status=429),
        }
    )
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "retry"
    assert len(fetch.calls) == 7


async def test_valid_alternative_wins_over_temporary_failure(client):
    url = "https://example.com/"
    fetch = Fetcher(
        {
            url: response(
                url,
                b'<head><link rel="alternate" type="application/rss+xml" '
                b'href="/rss"><link rel="alternate" type="application/atom+xml" '
                b'href="/atom"></head>',
            ),
            "https://example.com/rss": response("https://example.com/rss", status=503),
            "https://example.com/atom": response("https://example.com/atom", ATOM),
        }
    )
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "found"
    assert result.feed_url == "https://example.com/atom"


async def test_guessed_path_temporary_failure_does_not_require_retry(client):
    url = "https://example.com/"
    fetch = Fetcher(
        {
            url: response(url, b"<html></html>"),
            "https://example.com/feed": response("https://example.com/feed", status=503),
        }
    )
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "not_found"


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectError("connection unavailable"),
        TimeoutError(),
    ],
)
async def test_root_timeout_and_network_failure_require_retry(client, error):
    url = "https://example.com/"
    result = await discover_web_feed(url, client, fetch=Fetcher({url: error}))
    assert result.status == "retry"


async def test_safety_rejection_from_real_fetch_path_is_not_a_temporary_error(client):
    url = "https://example.com/"
    result = await discover_web_feed(
        url,
        client,
        fetch=Fetcher({url: UnsafeSourceUrl("Source URL resolves to a non-public address.")}),
    )
    assert result.status == "not_found"
    assert result.evidence["attempts"][0]["result"] == "unsafe_url"


async def test_dns_timeout_from_safe_fetcher_is_retryable(client):
    url = "https://example.com/"
    result = await discover_web_feed(
        url,
        client,
        fetch=Fetcher({url: UnsafeSourceUrl("Source hostname resolution timed out.")}),
    )
    assert result.status == "retry"
    assert result.evidence["attempts"][0]["result"] == "dns_error"


async def test_total_budget_cancels_pending_fetch_and_preserves_retry_evidence(client):
    cancelled = asyncio.Event()

    async def slow_fetch(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    result = await discover_web_feed(
        "https://example.com/",
        client,
        fetch=slow_fetch,
        timeout_seconds=0.02,
    )
    assert result.status == "retry"
    assert result.evidence["reason"] == "time_budget_exhausted"
    assert cancelled.is_set()


async def test_caller_cancellation_propagates(client):
    entered = asyncio.Event()

    async def waiting_fetch(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        discover_web_feed(
            "https://example.com/",
            client,
            fetch=waiting_fetch,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_candidate_and_response_bounds_are_passed_to_each_serial_fetch(client):
    url = "https://example.com/"
    links = "".join(
        f'<link rel="alternate" type="application/rss+xml" href="/{index}.xml">'
        for index in range(10)
    )
    fetch = Fetcher({url: response(url, f"<head>{links}</head>".encode())})
    result = await discover_web_feed(url, client, fetch=fetch, max_response_bytes=1024)
    assert result.status == "not_found"
    assert len(fetch.calls) == 7
    assert result.evidence["candidate_limit_reached"]
    assert result.evidence["candidate_count"] == 6
    assert all(call[1]["max_response_bytes"] == 1024 for call in fetch.calls)
    assert all(0 < call[1]["timeout"] <= 8 for call in fetch.calls)


async def test_oversized_injected_response_is_not_parsed(client):
    url = "https://example.com/"
    result = await discover_web_feed(
        url,
        client,
        fetch=Fetcher({url: response(url, RSS)}),
        max_response_bytes=10,
    )
    assert result.status == "retry"
    assert result.evidence["attempts"][0]["result"] == "response_too_large"


async def test_samples_only_contain_bounded_titles_and_safe_article_urls(client):
    url = "https://example.com/"
    entries = "<item><title>private</title><link>http://127.0.0.1/secret</link></item>"
    entries += "".join(
        f"<item><title>{'t' * 500}</title><link>https://example.com/{i}</link>"
        f"<description>never retain body</description></item>"
        for i in range(6)
    )
    body = f'<rss version="2.0"><channel><title>News</title>{entries}</channel></rss>'.encode()
    result = await discover_web_feed(url, client, fetch=Fetcher({url: response(url, body)}))
    assert result.status == "found"
    samples = result.evidence["samples"]
    assert len(samples) == 3
    assert all(len(item["title"]) == 256 and set(item) == {"url", "title"} for item in samples)
    assert "secret" not in str(result.evidence)
    assert "never retain body" not in str(result.evidence)


async def test_default_fetch_uses_existing_safe_get(monkeypatch):
    url = "https://example.com/"
    fetch = Fetcher({url: response(url, RSS)})
    monkeypatch.setattr("app.ingestion.feed_discovery.safe_get", fetch)
    async with PublicAsyncClient() as client:
        result = await discover_web_feed(url, client)
    assert result.status == "found"
    assert len(fetch.calls) == 1


@pytest.mark.parametrize(
    "limits",
    [
        {"timeout_seconds": 0},
        {"max_response_bytes": 0},
        {"max_candidates": 0},
        {"max_candidates": 7},
    ],
)
async def test_invalid_limits_are_rejected(client, limits):
    with pytest.raises(ValueError):
        await discover_web_feed("https://example.com/", client, fetch=Fetcher({}), **limits)


@pytest.mark.parametrize("status, challenge", [(403, False), (403, True), (200, True)])
async def test_blocked_page_still_discovers_a_scoped_feed(client, status, challenge):
    url = "https://example.com/research"
    page = response(url, b"<html>Verification required</html>", status=status)
    if challenge:
        page.headers["cf-mitigated"] = "challenge"
    feed_url = url + "/rss.xml"
    fetch = Fetcher({url: page, feed_url: response(feed_url, RSS)})

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.status == "found" and result.feed_url == feed_url
    assert [call[0] for call in fetch.calls] == [url, url + "/feed", feed_url]
    assert result.evidence["attempts"][0]["result"] == "access_blocked"
    assert "error_code" not in result.evidence


@pytest.mark.parametrize("status, challenge", [(401, False), (403, False), (200, True)])
async def test_blocked_page_without_usable_candidates_is_unknown(client, status, challenge):
    url = "https://example.com/research"
    page = response(url, b"<html>Denied</html>", status=status)
    if challenge:
        page.headers["cf-mitigated"] = "challenge"
    fetch = Fetcher({url: page})

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.status == "blocked" and result.feed_url is None
    assert result.evidence["error_code"] == (
        "web_challenge_required" if challenge else f"web_http_{status}"
    )
    assert len(fetch.calls) == 7


@pytest.mark.parametrize("challenge", [False, True])
async def test_declared_candidate_access_denial_does_not_cache_absence(client, challenge):
    url = "https://example.com/research"
    feed_url = url + "/feed"
    html = b'<link rel="alternate" type="application/rss+xml" href="/research/feed">'
    denied = response(feed_url, b"Verification required", status=403)
    if challenge:
        denied.headers["cf-mitigated"] = "challenge"
    fetch = Fetcher({url: response(url, html), feed_url: denied})

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.status == "blocked"
    assert result.evidence["error_code"] == (
        "web_rss_challenge_required" if challenge else "web_rss_http_403"
    )
    assert len(fetch.calls) == 7


@pytest.mark.parametrize("all_guesses", [False, True])
@pytest.mark.parametrize("status, challenge", [(401, False), (403, False), (200, True)])
async def test_guessed_feed_denial_does_not_block_authoring_for_a_readable_page(
    client, all_guesses, status, challenge
):
    url = "https://example.com/research"
    routes = {url: response(url, b"<html>Research</html>")}
    suffixes = ("feed", "rss.xml", "atom.xml", "rss", "feed.xml", "atom")
    for suffix in suffixes if all_guesses else suffixes[:1]:
        feed_url = f"{url}/{suffix}"
        denied = response(feed_url, b"Access denied", status=status)
        if challenge:
            denied.headers["cf-mitigated"] = "challenge"
        routes[feed_url] = denied
    fetch = Fetcher(routes)

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.status == "not_found" and result.feed_url is None
    assert "error_code" not in result.evidence
    blocked_attempts = [
        attempt for attempt in result.evidence["attempts"] if attempt["result"] == "access_blocked"
    ]
    assert len(blocked_attempts) == (6 if all_guesses else 1)
    assert all(
        attempt["error_code"]
        == ("web_rss_challenge_required" if challenge else f"web_rss_http_{status}")
        for attempt in blocked_attempts
    )
    assert len(fetch.calls) == 7


@pytest.mark.parametrize("failure", [403, 404, 503])
async def test_failed_declaration_falls_back_to_valid_scoped_feed(client, failure):
    url = "https://example.com/research"
    html = b'<link rel="alternate" type="application/rss+xml" href="/old-feed">'
    feed_url = url + "/rss"
    fetch = Fetcher(
        {
            url: response(url, html),
            "https://example.com/old-feed": response(
                "https://example.com/old-feed", status=failure
            ),
            feed_url: response(feed_url, RSS),
        }
    )

    result = await discover_web_feed(url, client, fetch=fetch)

    assert result.status == "found" and result.feed_url == feed_url
    assert [call[0] for call in fetch.calls[:2]] == [url, "https://example.com/old-feed"]
    assert len(fetch.calls) <= 7


async def test_slow_page_leaves_time_for_independent_feed_discovery(client):
    url = "https://example.com/research"
    calls = []
    page_cancelled = asyncio.Event()

    async def fetch(_client, requested_url, **kwargs):
        calls.append(requested_url)
        if requested_url == url:
            try:
                await asyncio.Event().wait()
            finally:
                page_cancelled.set()
        return response(requested_url, RSS)

    result = await discover_web_feed(url, client, fetch=fetch, timeout_seconds=0.06)

    assert result.status == "found" and result.feed_url == url + "/feed"
    assert calls == [url, url + "/feed"]
    assert page_cancelled.is_set()
    assert result.evidence["attempts"][0]["result"] == "timeout"


async def test_slow_candidate_leaves_time_for_later_feed(client):
    url = "https://example.com/research"
    calls = []

    async def fetch(_client, requested_url, **kwargs):
        calls.append(requested_url)
        if requested_url == url:
            return response(url, b"<html>Research</html>")
        if requested_url == url + "/feed":
            await asyncio.Event().wait()
        return response(requested_url, RSS)

    result = await discover_web_feed(url, client, fetch=fetch, timeout_seconds=0.06)

    assert result.status == "found" and result.feed_url == url + "/rss.xml"
    assert calls == [url, url + "/feed", url + "/rss.xml"]


async def test_fallback_guesses_do_not_shorten_a_declared_feed_request_budget(client):
    url = "https://example.com/research"
    feed_url = url + "/feed"
    html = b'<link rel="alternate" type="application/rss+xml" href="/research/feed">'
    calls = []

    async def fetch(_client, requested_url, **kwargs):
        calls.append(requested_url)
        if requested_url == url:
            return response(url, html)
        if requested_url == feed_url:
            # Simulate an endpoint needing 70 ms without introducing real wait
            # or timing flakiness. Its five guesses must not reduce 100 to 50 ms.
            if kwargs["timeout"] < 0.07:
                raise httpx.ReadTimeout("The declared feed needs at least 70 ms.")
            return response(feed_url, RSS)
        return response(requested_url, status=404)

    result = await discover_web_feed(url, client, fetch=fetch, timeout_seconds=0.3)

    assert result.status == "found" and result.feed_url == feed_url
    assert result.evidence["candidate_count"] == 6
    assert calls == [url, feed_url]


async def test_blocked_query_column_never_guesses_without_scope(client):
    url = "https://example.com/?category=research"
    fetch = Fetcher({url: response(url, status=403)})
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "blocked"
    assert [call[0] for call in fetch.calls] == [url]


async def test_column_redirected_to_parent_does_not_guess_parent_feed(client):
    url = "https://example.com/blog/research"
    fetch = Fetcher({url: response(url, b"<html></html>", final_url="https://example.com/blog")})
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "not_found"
    assert [call[0] for call in fetch.calls] == [url]


@pytest.mark.parametrize("final_url", ["https://example.com/feed", "https://other.com/research/feed"])
async def test_guessed_feed_redirect_cannot_select_a_broader_or_unproven_feed(client, final_url):
    url = "https://example.com/research"
    feed_url = url + "/feed"
    fetch = Fetcher(
        {url: response(url, status=403), feed_url: response(feed_url, RSS, final_url=final_url)}
    )
    result = await discover_web_feed(url, client, fetch=fetch)
    assert result.status == "blocked" and result.feed_url is None
    assert result.evidence["attempts"][1]["result"] == "unscoped_redirect"


def _legacy_discovery_record(url):
    return {
        "version": 1,
        "source_url": url,
        "status": "not_found",
        "evidence": {
            "requests": 1,
            "attempts": [{"url": url, "origin": "source", "status_code": 403}],
        },
    }


def test_only_historical_one_request_page_403_negative_cache_is_reprobed():
    url = "https://example.com/research"
    record = _legacy_discovery_record(url)
    assert web_feed_probe_required(url, {"web_feed_discovery": record})
    record["evidence"]["attempts"][0]["status_code"] = 200
    assert not web_feed_probe_required(url, {"web_feed_discovery": record})
    record["evidence"]["attempts"][0]["status_code"] = 403
    record["evidence"]["requests"] = 2
    assert not web_feed_probe_required(url, {"web_feed_discovery": record})


def test_legacy_recovery_preserves_a_selected_feed():
    url = "https://example.com/research"
    record = _legacy_discovery_record(url)
    record.update(status="found", feed_url=url + "/feed")
    config = {"web_feed_discovery": record}
    assert configured_web_feed_url(url, config) == url + "/feed"
    assert not web_feed_probe_required(url, config)
