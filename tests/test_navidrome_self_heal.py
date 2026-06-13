"""Navidrome connection self-heals after a transient ping failure (jimmydotcom).

A failed ping nukes the creds in _setup_client; previously _connection_attempted
latched the client 'disconnected' until a manual Test. Now is_connected re-attempts
(throttled) so a blip recovers on its own."""

from __future__ import annotations

from core.navidrome_client import NavidromeClient


def test_self_heals_after_transient_failure(monkeypatch):
    c = NavidromeClient()
    calls = {'n': 0}

    def fake_setup():
        calls['n'] += 1
        if calls['n'] == 1:                      # transient failure nukes creds
            c.base_url = c.username = c.password = None
        else:                                     # blip passed → connects
            c.base_url, c.username, c.password = 'http://nd:4533', 'u', 'p'
    monkeypatch.setattr(c, '_setup_client', fake_setup)

    assert c.is_connected() is False             # first attempt fails
    assert calls['n'] == 1

    assert c.is_connected() is False             # immediate recheck: throttled
    assert calls['n'] == 1                       # did NOT re-ping (no storm)

    c._last_connect_attempt -= (c._RECONNECT_THROTTLE_S + 1)   # throttle window elapses
    assert c.is_connected() is True              # re-attempts → recovers itself
    assert calls['n'] == 2                       # no manual reconnect was needed


def test_connected_client_does_not_reattempt(monkeypatch):
    c = NavidromeClient()
    c.base_url, c.username, c.password = 'http://nd', 'u', 'p'
    c._connection_attempted = True
    calls = {'n': 0}
    monkeypatch.setattr(c, '_setup_client', lambda: calls.__setitem__('n', calls['n'] + 1))
    assert c.is_connected() is True
    assert calls['n'] == 0                        # already connected → never re-pings


def test_first_connect_attempts_once(monkeypatch):
    c = NavidromeClient()
    calls = {'n': 0}

    def fake_setup():
        calls['n'] += 1
        c.base_url, c.username, c.password = 'http://nd', 'u', 'p'
    monkeypatch.setattr(c, '_setup_client', fake_setup)
    assert c.is_connected() is True
    assert calls['n'] == 1
