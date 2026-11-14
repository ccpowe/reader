import pytest

from reader.ingestion.errors import ScanError, upstream_errors
from reader.ingestion.url_safety import UnresolvableHost, UnsafeSourceUrl


def _scan_error(exc: Exception) -> ScanError:
    with pytest.raises(ScanError) as caught, upstream_errors("rss"):
        raise exc
    return caught.value


def test_dns_failure_is_a_retryable_network_error() -> None:
    error = _scan_error(UnresolvableHost("Source hostname could not be resolved."))
    assert error.code == "rss_network_error" and not error.long_lived


def test_an_unsafe_destination_is_long_lived() -> None:
    error = _scan_error(UnsafeSourceUrl("Source URL resolves to a non-public address."))
    assert error.code == "unsafe_url" and error.long_lived
