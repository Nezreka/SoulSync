"""#1073 (Lain2077) — HiFi rotation semantics: fail-over like monochrome does.

Live probing showed the ecosystem's post-outage reality: most default
instances are dead, the healthy ones rate-limit hard (429) and geo-block
per-instance (403). Our client rotated only on network errors and 5xx —
a 429/403 hard-failed the request even though the NEXT instance would have
served it, which is exactly "my requests fail while they succeed on
monochrome". Rotation now covers every status that depends on WHICH
instance you ask (5xx, 429, 403); statuses that describe the REQUEST
(400/404/422) still fail fast without burning the pool.

Hermetic: session mocked, no network.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import requests as http_requests

from core.hifi_client import HiFiClient


def _client(instances):
    c = HiFiClient.__new__(HiFiClient)
    import threading
    c._instances = list(instances)
    c._current_instance = instances[0]
    c._instance_lock = threading.Lock()
    c._api_lock = threading.Lock()
    c._last_api_call = 0.0
    c._min_interval = 0.0
    c.session = MagicMock()
    return c


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    if status >= 400:
        err = http_requests.exceptions.HTTPError(response=r)
        r.raise_for_status.side_effect = err
    else:
        r.raise_for_status.return_value = None
    r.json.return_value = payload if payload is not None else {}
    return r


def test_429_rotates_to_the_next_instance():
    c = _client(['https://dead.example', 'https://live.example'])
    c.session.get.side_effect = [_resp(429), _resp(200, {'items': [1]})]
    out = c._api_get('/search/', params={'s': 'x'})
    assert out == {'items': [1]}
    assert c.session.get.call_count == 2
    assert c._current_instance == 'https://live.example'   # rotation persisted


def test_403_rotates_too():
    c = _client(['https://blocked.example', 'https://live.example'])
    c.session.get.side_effect = [_resp(403), _resp(200, {'ok': True})]
    assert c._api_get('/track/', params={'id': 1}) == {'ok': True}


def test_404_fails_fast_without_burning_the_pool():
    c = _client(['https://a.example', 'https://b.example'])
    c.session.get.side_effect = [_resp(404)]
    assert c._api_get('/track/', params={'id': 999}) is None
    assert c.session.get.call_count == 1                    # no rotation
    assert c._current_instance == 'https://a.example'


def test_500_rotation_unchanged():
    c = _client(['https://sick.example', 'https://live.example'])
    c.session.get.side_effect = [_resp(500), _resp(200, {'ok': True})]
    assert c._api_get('/search/', params={'s': 'x'}) == {'ok': True}


def test_exhaustion_returns_none():
    c = _client(['https://a.example', 'https://b.example'])
    c.session.get.side_effect = [_resp(429), _resp(429)]
    assert c._api_get('/search/', params={'s': 'x'}) is None
    assert c.session.get.call_count == 2                    # each tried once
