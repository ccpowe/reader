"""Source-bound RSS discovery state, independent of model availability."""

from app.ingestion.url_safety import validate_http_url
from app.ingestion.web_rules import parse_web_rule

WEB_FEED_DISCOVERY_VERSION = 1


def web_feed_discovery(source_url: str, config: dict | None) -> dict:
    record = (config or {}).get("web_feed_discovery")
    if (
        not isinstance(record, dict)
        or type(record.get("version")) is not int
        or record["version"] != WEB_FEED_DISCOVERY_VERSION
        or record.get("source_url") != source_url
    ):
        return {}
    return record


def configured_web_feed_url(source_url: str, config: dict | None) -> str | None:
    record = web_feed_discovery(source_url, config)
    url = record.get("feed_url")
    if record.get("status") != "found" or not isinstance(url, str):
        return None
    try:
        validate_http_url(url)
    except ValueError:
        return None
    return url


def _legacy_blocked_discovery(record: dict) -> bool:
    """Repair only old negative results that stopped at an input-page 403."""
    evidence = record.get("evidence")
    if record.get("status") != "not_found" or not isinstance(evidence, dict):
        return False
    attempts = evidence.get("attempts")
    return (
        evidence.get("requests") == 1
        and isinstance(attempts, list)
        and len(attempts) == 1
        and isinstance(attempts[0], dict)
        and attempts[0].get("origin") == "source"
        and attempts[0].get("status_code") == 403
    )


def web_feed_probe_required(source_url: str, config: dict | None) -> bool:
    if configured_web_feed_url(source_url, config):
        return False
    try:
        parse_web_rule((config or {}).get("web_rule"), source_url=source_url)
        return False
    except (ValueError, TypeError):
        pass
    record = web_feed_discovery(source_url, config)
    return record.get("status") != "not_found" or _legacy_blocked_discovery(record)
