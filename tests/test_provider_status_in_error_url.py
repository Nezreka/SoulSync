"""An entity ID in an error URL must not masquerade as an HTTP status."""

import threading
import time
from types import SimpleNamespace

import pytest
import requests
from spotipy.exceptions import SpotifyException

import core.deezer_client as deezer
import core.jiosaavn_client as jiosaavn
import core.spotify_client as spotify
import core.tidal_client as tidal


def _http_error(status, entity_id):
    response = requests.Response()
    response.status_code = status
    response.url = f'https://example.test/items/{entity_id}'
    with pytest.raises(requests.HTTPError) as caught:
        response.raise_for_status()
    return caught.value


def _capture_current_thread_sleep(monkeypatch, module):
    sleeps = []
    current_thread = threading.current_thread()
    real_sleep = time.sleep

    def capture(delay):
        if threading.current_thread() is current_thread:
            sleeps.append(delay)
        else:
            real_sleep(delay)

    monkeypatch.setattr(module.time, 'sleep', capture)
    return sleeps


@pytest.mark.parametrize('entity_id', ['item429', 'item503'])
def test_spotify_404_with_status_digits_does_not_trigger_global_ban_or_retry(monkeypatch, entity_id):
    error = SpotifyException(404, -1, f'https://api.spotify.com/v1/tracks/{entity_id}: not found')
    bans = []
    sleeps = _capture_current_thread_sleep(monkeypatch, spotify)
    monkeypatch.setattr(spotify, '_set_global_rate_limit', lambda *a, **kw: bans.append((a, kw)))
    monkeypatch.setattr(spotify, '_is_globally_rate_limited', lambda: False)
    monkeypatch.setattr(spotify, '_get_min_api_interval', lambda: 0)
    monkeypatch.setattr(spotify, '_last_api_call_time', 0)

    assert spotify._detect_and_set_rate_limit(error) is False
    calls = []

    @spotify.rate_limited
    def fetch():
        calls.append(True)
        raise error

    with pytest.raises(SpotifyException):
        fetch()
    assert len(calls) == 1
    assert bans == []
    assert sleeps == []


def test_spotify_real_429_still_triggers_global_ban(monkeypatch):
    bans = []
    monkeypatch.setattr(spotify, '_set_global_rate_limit', lambda *a, **kw: bans.append((a, kw)))
    error = SpotifyException(429, -1, 'rate limit', headers={'Retry-After': '120'})
    assert spotify._detect_and_set_rate_limit(error) is True
    assert len(bans) == 1


@pytest.mark.parametrize('entity_id', ['item429', 'item503'])
def test_tidal_404_with_status_digits_is_not_retried(monkeypatch, entity_id):
    error = _http_error(404, entity_id)
    sleeps = _capture_current_thread_sleep(monkeypatch, tidal)
    calls = []
    monkeypatch.setattr(tidal, '_last_api_call_time', 0)

    @tidal.rate_limited
    def fetch():
        calls.append(True)
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert len(calls) == 1
    assert sleeps == []


def test_tidal_real_503_is_still_retried(monkeypatch):
    error = _http_error(503, 'item429')
    sleeps = _capture_current_thread_sleep(monkeypatch, tidal)
    calls = []
    monkeypatch.setattr(tidal, '_last_api_call_time', 0)

    @tidal.rate_limited
    def fetch():
        calls.append(True)
        if len(calls) == 1:
            raise error
        return 'ok'

    assert fetch() == 'ok'
    assert len(calls) == 2
    assert 2.0 in sleeps


@pytest.mark.parametrize('status,expected_sleeps', [(404, []), (429, [4.0])])
def test_deezer_backoff_uses_response_status(monkeypatch, status, expected_sleeps):
    error = _http_error(status, 'item429')
    sleeps = _capture_current_thread_sleep(monkeypatch, deezer)
    monkeypatch.setattr('core.deezer_throttle.wait_for_slot', lambda: None)

    @deezer.rate_limited
    def fetch():
        raise error

    with pytest.raises(requests.HTTPError):
        fetch()
    assert sleeps == expected_sleeps


@pytest.mark.parametrize('status,expected_sleeps', [(404, []), (503, [2.0])])
def test_jiosaavn_backoff_uses_response_status(monkeypatch, status, expected_sleeps):
    error = _http_error(status, 'item429')
    sleeps = _capture_current_thread_sleep(monkeypatch, jiosaavn)
    monkeypatch.setattr(jiosaavn, '_rate_limit', lambda: None)
    client = jiosaavn.JioSaavnClient.__new__(jiosaavn.JioSaavnClient)
    client.base_url = 'https://example.test'
    client.timeout = 5
    client.session = SimpleNamespace(get=lambda *a, **kw: error.response)

    with pytest.raises(requests.HTTPError):
        client._get_json('/items/item429')
    assert sleeps == expected_sleeps
