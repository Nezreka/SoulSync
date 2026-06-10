"""Tidal manifest fetch backs off on HTTP 429 instead of instant-failing.

Tidal aggressively rate-limits the trackManifests endpoint. The bare
request previously failed 429 immediately, burning the quality tier and
re-queueing the track, which hammered Tidal again — a self-amplifying
storm (thousands of 429s, downloads stalled). These pin the backoff:
retry on 429, honour Retry-After, give up after a bounded number of
attempts, bail on shutdown, and never retry a normal 4xx.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.tidal_download_client import TidalDownloadClient


def _client():
    # Bypass __init__ (which builds a tidalapi session + mkdirs); we only
    # exercise the pure HTTP-retry helpers.
    c = TidalDownloadClient.__new__(TidalDownloadClient)
    c.shutdown_check = None
    return c


def _resp(status, retry_after=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {'Retry-After': str(retry_after)} if retry_after is not None else {}
    return r


# ── _retry_after_seconds ──────────────────────────────────────────────

def test_exponential_backoff_without_header():
    assert TidalDownloadClient._retry_after_seconds(_resp(429), 0) == 2.0
    assert TidalDownloadClient._retry_after_seconds(_resp(429), 1) == 4.0
    assert TidalDownloadClient._retry_after_seconds(_resp(429), 2) == 8.0


def test_backoff_capped():
    # 2 * 2**10 = 2048 → capped at 30.
    assert TidalDownloadClient._retry_after_seconds(_resp(429), 10) == 30.0


def test_retry_after_header_honoured():
    assert TidalDownloadClient._retry_after_seconds(_resp(429, retry_after=7), 0) == 7.0


def test_retry_after_header_capped():
    assert TidalDownloadClient._retry_after_seconds(_resp(429, retry_after=999), 0) == 30.0


def test_garbage_retry_after_falls_back_to_exponential():
    assert TidalDownloadClient._retry_after_seconds(_resp(429, retry_after='soon'), 1) == 4.0


# ── _get_with_rate_limit_retry ────────────────────────────────────────

def test_retries_then_succeeds():
    c = _client()
    responses = [_resp(429), _resp(429), _resp(200)]
    with patch('core.tidal_download_client.http_requests.get',
               side_effect=responses) as g, \
         patch('core.tidal_download_client.time.sleep') as sleep:
        out = c._get_with_rate_limit_retry('http://x')
    assert out.status_code == 200
    assert g.call_count == 3
    assert sleep.call_count >= 2  # backed off before each retry


def test_non_429_4xx_returns_immediately_no_retry():
    c = _client()
    with patch('core.tidal_download_client.http_requests.get',
               side_effect=[_resp(403)]) as g, \
         patch('core.tidal_download_client.time.sleep') as sleep:
        out = c._get_with_rate_limit_retry('http://x')
    assert out.status_code == 403
    assert g.call_count == 1
    sleep.assert_not_called()


def test_gives_up_after_max_retries():
    c = _client()
    with patch('core.tidal_download_client.http_requests.get',
               side_effect=[_resp(429)] * 20) as g, \
         patch('core.tidal_download_client.time.sleep'):
        out = c._get_with_rate_limit_retry('http://x')
    assert out.status_code == 429
    # initial try + MAX_RETRIES retries
    assert g.call_count == TidalDownloadClient._MANIFEST_MAX_RETRIES + 1


def test_transient_5xx_is_retried():
    c = _client()
    with patch('core.tidal_download_client.http_requests.get',
               side_effect=[_resp(503), _resp(200)]) as g, \
         patch('core.tidal_download_client.time.sleep'):
        out = c._get_with_rate_limit_retry('http://x')
    assert out.status_code == 200
    assert g.call_count == 2


def test_shutdown_aborts_backoff():
    c = _client()
    c.shutdown_check = lambda: True  # shutdown requested
    with patch('core.tidal_download_client.http_requests.get',
               side_effect=[_resp(429), _resp(200)]) as g, \
         patch('core.tidal_download_client.time.sleep'):
        out = c._get_with_rate_limit_retry('http://x')
    # First call 429 → enters backoff → shutdown → returns the 429 without
    # making the second request.
    assert out.status_code == 429
    assert g.call_count == 1
