"""#1606 (cremonies): with every hifi server down, post-processing waited about
a minute per track. each lookup walked the whole pool, a timeout per instance,
three lookups a track. once a full pass fails, calls now skip hifi for a while.

hermetic: session mocked, clock injected, no network.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import requests as http_requests

from core.hifi_client import AllInstancesDownBreaker, HiFiClient


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _client(instances, clock):
    c = HiFiClient.__new__(HiFiClient)
    c._instances = list(instances)
    c._current_instance = instances[0]
    c._instance_lock = threading.Lock()
    c._api_lock = threading.Lock()
    c._last_api_call = 0.0
    c._min_interval = 0.0
    c.session = MagicMock()
    c._down_breaker = AllInstancesDownBreaker(clock=clock)
    return c


def _ok(payload):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    return r


def _status(code):
    r = MagicMock()
    r.status_code = code
    r.raise_for_status.side_effect = http_requests.exceptions.HTTPError(response=r)
    return r


def test_a_dead_pool_is_skipped_after_one_full_pass():
    clock = _Clock()
    c = _client(['https://a.example', 'https://b.example'], clock)
    c.session.get.side_effect = http_requests.exceptions.Timeout()

    assert c._api_get('/search/', timeout=1) is None
    assert c.session.get.call_count == 2          # one pass over the pool

    # the next lookups (track info, album, the next track) don't wait again
    for _ in range(5):
        assert c._api_get('/info/', timeout=1) is None
    assert c.session.get.call_count == 2


def test_the_pool_is_asked_again_once_the_wait_is_over_and_waits_double():
    clock = _Clock()
    c = _client(['https://a.example'], clock)
    c.session.get.side_effect = http_requests.exceptions.ConnectionError()

    c._api_get('/x', timeout=1)
    assert c._breaker().remaining() == 30
    clock.t += 31
    c._api_get('/x', timeout=1)
    assert c.session.get.call_count == 2
    assert c._breaker().remaining() == 60         # still dead, wait longer

    for _ in range(10):
        clock.t += 1000
        c._api_get('/x', timeout=1)
    assert c._breaker().remaining() == 300        # capped at five minutes


def test_any_answer_resets_the_wait():
    clock = _Clock()
    c = _client(['https://a.example'], clock)
    c.session.get.side_effect = [http_requests.exceptions.Timeout(), _ok({'items': [1]}),
                                 _ok({'items': [2]})]
    c._api_get('/x', timeout=1)
    clock.t += 31
    assert c._api_get('/x', timeout=1) == {'items': [1]}
    assert not c._breaker().is_open()
    assert c._api_get('/x', timeout=1) == {'items': [2]}


def test_a_request_error_is_an_answer_not_a_dead_pool():
    # a 404 describes the request, the server is fine
    clock = _Clock()
    c = _client(['https://a.example', 'https://b.example'], clock)
    c.session.get.side_effect = [_status(404), _ok({'ok': True})]
    assert c._api_get('/x', timeout=1) is None
    assert not c._breaker().is_open()
    assert c._api_get('/x', timeout=1) == {'ok': True}


def test_one_live_instance_keeps_the_gate_open():
    clock = _Clock()
    c = _client(['https://dead.example', 'https://live.example'], clock)
    c.session.get.side_effect = [http_requests.exceptions.Timeout(), _ok({'ok': 1})]
    assert c._api_get('/x', timeout=1) == {'ok': 1}
    assert not c._breaker().is_open()


def test_no_instances_configured_is_not_a_dead_pool():
    clock = _Clock()
    c = _client(['https://a.example'], clock)
    c._current_instance = None
    assert c._api_get('/x', timeout=1) is None
    assert not c._breaker().is_open()


def test_reloading_the_instance_list_asks_again(monkeypatch):
    clock = _Clock()
    c = _client(['https://a.example'], clock)
    c.session.get.side_effect = http_requests.exceptions.Timeout()
    c._api_get('/x', timeout=1)
    assert c._breaker().is_open()
    monkeypatch.setattr(c, '_load_instances_from_db', lambda: None)
    c.reload_instances()
    assert not c._breaker().is_open()


def test_a_client_built_without_init_still_gets_a_breaker():
    c = HiFiClient.__new__(HiFiClient)
    assert isinstance(c._breaker(), AllInstancesDownBreaker)
    assert c._breaker() is c._breaker()
