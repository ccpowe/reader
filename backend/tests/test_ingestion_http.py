"""Upstream access errors stay distinct from parsing and do not retain challenges."""

import httpx
import pytest

from app.ingestion.http import raise_for_provider_status
from app.ingestion.models import SourceScanError


@pytest.mark.parametrize("provider", ["web", "rss", "web_rss"])
@pytest.mark.parametrize(
    "status,headers,category",
    [
        (403, {}, "http_403"),
        (403, {"cf-mitigated": "challenge"}, "challenge_required"),
        (200, {"cf-mitigated": "challenge"}, "challenge_required"),
    ],
)
def test_access_restrictions_preserve_evidence_without_response_tokens(
    provider, status, headers, category
):
    response = httpx.Response(
        status,
        headers={**headers, "retry-after": "1800"},
        text="<html>private-verification-token</html>",
        request=httpx.Request("GET", "https://example.com/research"),
    )
    with pytest.raises(SourceScanError) as raised:
        raise_for_provider_status(response, provider)
    error = raised.value
    assert error.code == f"{provider}_{category}"
    assert error.long_lived and error.retry_after_seconds == 1800
    assert error.evidence == {
        "http_status": status,
        "browser_verification": category == "challenge_required",
    }
    assert "private-verification-token" not in str(error)


def test_successful_page_title_does_not_prove_a_challenge():
    # Ordinary publisher content may discuss verification; only upstream
    # challenge evidence should classify a successful response as restricted.
    response = httpx.Response(200, text="<h1>Just a moment: how verification works</h1>")
    raise_for_provider_status(response, "web")
