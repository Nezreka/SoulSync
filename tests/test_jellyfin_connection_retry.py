"""a failed jellyfin connection attempt is retried, not remembered for good.

ensure_connection used to answer, after the first attempt, only with what
that attempt left behind: a jellyfin still starting up when soulsync came
up, or one blip, read as disconnected until someone pressed Test in the
sidebar (reload_config). the navidrome client already re-attempts after
a throttle; jellyfin does the same now.
"""

from __future__ import annotations

import core.jellyfin_client as jc
from core.jellyfin_client import JellyfinClient


def _client(monkeypatch, answers):
    """answers: per attempt, True = jellyfin answers, False = it does not"""
    client = JellyfinClient()
    monkeypatch.setattr(jc.config_manager, 'get_jellyfin_config', lambda: {'base_url': 'http://jf', 'api_key': 'k'})
    attempts = []

    def fake_setup():
        ok = answers[min(len(attempts), len(answers) - 1)]
        attempts.append(ok)
        if ok:
            client.base_url, client.api_key = 'http://jf', 'k'
        else:
            client.base_url = client.api_key = None
    monkeypatch.setattr(client, '_setup_client', fake_setup)
    return client, attempts


def test_a_failed_first_attempt_is_retried_after_the_throttle(monkeypatch):
    client, attempts = _client(monkeypatch, [False, True])
    clock = {'t': 1000.0}
    monkeypatch.setattr(jc.time, 'monotonic', lambda: clock['t'])
    assert client.ensure_connection() is False
    assert client.ensure_connection() is False          # inside the throttle: no hammering
    assert attempts == [False]
    clock['t'] += client._RECONNECT_THROTTLE_S + 1
    assert client.ensure_connection() is True, "still disconnected after jellyfin came back"
    assert attempts == [False, True]


def test_a_connected_client_does_not_reconnect(monkeypatch):
    client, attempts = _client(monkeypatch, [True])
    assert client.ensure_connection() is True
    assert client.ensure_connection() is True
    assert attempts == [True]


def test_reload_config_still_forces_a_fresh_attempt(monkeypatch):
    client, attempts = _client(monkeypatch, [False, True])
    clock = {'t': 1000.0}
    monkeypatch.setattr(jc.time, 'monotonic', lambda: clock['t'])
    assert client.ensure_connection() is False
    client.reload_config()
    assert client.ensure_connection() is True


def test_the_status_check_reaches_the_retry(monkeypatch):
    """/status asks is_connected, which used to skip ensure_connection once
    any attempt had been made: the sidebar dot stayed red for good"""
    client, attempts = _client(monkeypatch, [False, True])
    clock = {'t': 1000.0}
    monkeypatch.setattr(jc.time, 'monotonic', lambda: clock['t'])
    assert client.is_connected() is False
    clock['t'] += client._RECONNECT_THROTTLE_S + 1
    client.user_id = 'u'
    client.music_library_id = 'lib'
    assert client.is_connected() is True
    assert attempts == [False, True]
