import pytest

from app.ingestion.source_identity import (
    canonical_reddit_identity,
    canonical_rss_identity,
    canonical_web_identity,
    canonical_x_identity,
    canonical_youtube_identity,
)
from app.ingestion.url_safety import UnsafeSourceUrl


def test_rss_identity_normalises_scheme_host_default_port_and_fragment() -> None:
    identity = canonical_rss_identity("HTTPS://Example.COM:443/feed.xml#latest")

    assert identity.canonical_url == "https://example.com/feed.xml"
    assert identity.canonical_key == "rss:https://example.com/feed.xml"


def test_rss_identity_retains_meaningful_query_parameters() -> None:
    identity = canonical_rss_identity("https://example.com/feed?language=en")

    assert identity.canonical_url == "https://example.com/feed?language=en"


def test_rss_identity_rejects_embedded_credentials() -> None:
    with pytest.raises(UnsafeSourceUrl):
        canonical_rss_identity("https://token@example.com/feed.xml")


def test_web_identity_normalises_host_default_port_and_fragment() -> None:
    identity = canonical_web_identity("HTTPS://WWW.Example.COM:443/blog/#latest")

    assert identity.kind.value == "web"
    assert identity.canonical_url == "https://www.example.com/blog/"
    assert identity.canonical_key == "web:https://www.example.com/blog/"


def test_reddit_identity_is_one_community_regardless_of_ranking_views() -> None:
    identity = canonical_reddit_identity("r/LocalLLaMA")

    assert identity.kind.value == "reddit"
    assert identity.canonical_key == "reddit:localllama"
    assert identity.canonical_url == "https://www.reddit.com/r/LocalLLaMA/"


def test_reddit_identity_enforces_limit_after_normalising_prefix() -> None:
    identity = canonical_reddit_identity(f" r/{'a' * 21} ")

    assert identity.canonical_key == f"reddit:{'a' * 21}"
    with pytest.raises(ValueError, match="Invalid subreddit"):
        canonical_reddit_identity(f"r/{'a' * 22}")


def test_web_identity_has_no_site_specific_aliases() -> None:
    identity = canonical_web_identity("https://blog.langchain.com/some-path")
    assert identity.canonical_url == "https://blog.langchain.com/some-path"


def test_youtube_and_x_identities_are_stable() -> None:
    youtube = canonical_youtube_identity("UCXZCJLdBC09xxGZ6gcdrc6A")
    x_source = canonical_x_identity("@OpenAI")

    assert youtube.canonical_key == "youtube:UCXZCJLdBC09xxGZ6gcdrc6A"
    assert x_source.canonical_key == "x:openai"
