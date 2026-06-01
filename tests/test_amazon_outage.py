"""Tests for Amazon enrichment outage detection + back-off (core/amazon_outage.py).

Pins the contract behind issue #759: when the public T2Tunes proxy is down
(503 "Amazon Music API is not initialized", 5xx, or unreachable) the worker must
recognize a *source outage* — not treat every album as a per-item error and
grind/flood the whole library.
"""

from __future__ import annotations

from core.amazon_client import AmazonClientError
from core.amazon_outage import is_source_outage, next_poll_delay_seconds


# --- is_source_outage: the reported case + the surfaces it can arrive in -----


def test_503_not_initialized_is_outage_via_status_code():
    exc = AmazonClientError("HTTP 503 for .../search — body: '...not initialized...'",
                            status_code=503)
    assert is_source_outage(exc) is True


def test_503_is_outage_even_without_status_code_attr():
    # Message-only (e.g. re-raised/wrapped) — parsed from the "HTTP 503" prefix.
    assert is_source_outage(Exception("HTTP 503 for https://t2tunes.site/...")) is True


def test_not_initialized_phrase_is_outage_without_code():
    assert is_source_outage(Exception("Amazon Music API is not initialized")) is True


def test_gateway_5xx_are_outages():
    for code in (500, 502, 504):
        assert is_source_outage(AmazonClientError("x", status_code=code)) is True


def test_connection_failure_is_outage():
    assert is_source_outage(AmazonClientError(
        "Request failed for https://t2tunes.site/...: Connection refused")) is True
    assert is_source_outage(Exception("Max retries exceeded ... Connection timed out")) is True


def test_non_json_error_page_is_outage():
    assert is_source_outage(AmazonClientError(
        "Response not JSON for ...: '<html>503 Service Unavailable</html>'")) is True


# --- NOT outages: real per-item misses / client errors -----------------------


def test_404_is_not_outage():
    assert is_source_outage(AmazonClientError("HTTP 404 for ...", status_code=404)) is False


def test_transient_400_failed_to_search_is_not_outage():
    # A per-query Amazon-side hiccup, not the whole source being down.
    assert is_source_outage(AmazonClientError(
        "HTTP 400 for ... — body: 'Failed to search'", status_code=400)) is False


def test_generic_error_is_not_outage():
    assert is_source_outage(ValueError("something unrelated")) is False


# --- next_poll_delay_seconds: normal cadence vs escalating back-off ----------


def test_healthy_uses_normal_cadence():
    assert next_poll_delay_seconds(0) == 2
    assert next_poll_delay_seconds(-1) == 2


def test_backoff_escalates_then_caps():
    assert next_poll_delay_seconds(1) == 30
    assert next_poll_delay_seconds(2) == 60
    assert next_poll_delay_seconds(3) == 120
    assert next_poll_delay_seconds(4) == 240
    # Escalation is capped at 30 minutes so it never grows unbounded.
    assert next_poll_delay_seconds(50) == 1800


def test_backoff_is_monotonic_nondecreasing():
    delays = [next_poll_delay_seconds(s) for s in range(1, 20)]
    assert delays == sorted(delays)
    assert max(delays) == 1800
