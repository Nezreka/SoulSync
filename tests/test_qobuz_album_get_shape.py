"""album/get is asked the way Qobuz accepts it.

Nov 13 2026: on one install every `album/get?extra=tracks` came back HTTP 400,
2,900 times in six hours, and no album had matched in 40 MB of log. The clients
that document the endpoint do not list `tracks` as a value for `extra`. That
could not be tried against Qobuz itself, so the client finds out: the old
request first, the plain one once when that is refused with a 400, and whichever
is accepted from then on."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import core.qobuz_client as qc


class _Qobuz:
    """Answers album/get by a rule and records every request."""
    def __init__(self, rule):
        self.rule, self.asked = rule, []

    def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.asked.append(params)
        status, body = self.rule(params)
        response = MagicMock(status_code=status, headers={})
        response.json.return_value = body
        return response


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(qc, "_qobuz_is_rate_limited", lambda: False)
    monkeypatch.setattr(qc, "_qobuz_throttle", lambda: None)
    monkeypatch.setattr(qc.QobuzClient, "_album_extra", "unknown")
    monkeypatch.setattr(qc.QobuzClient, "_album_plain_refused", False)

    def make(rule):
        c = qc.QobuzClient.__new__(qc.QobuzClient)
        c.session = _Qobuz(rule)
        c.is_authenticated = lambda: True
        return c
    return make


ALBUM = {"id": "a1", "title": "Album", "tracks": {"items": [{"id": 1}]}}


def test_when_extra_tracks_is_refused_the_plain_request_is_used_from_then_on(client):
    c = client(lambda p: (400, None) if "extra" in p else (200, ALBUM))
    assert c.get_album("a1") == ALBUM
    assert c.session.asked == [{"album_id": "a1", "extra": "tracks"}, {"album_id": "a1"}]
    assert c.get_album("a2") == ALBUM
    assert c.get_album("a3") == ALBUM
    assert c.session.asked[2:] == [{"album_id": "a2"}, {"album_id": "a3"}]


def test_when_extra_tracks_works_nothing_changes(client):
    c = client(lambda p: (200, ALBUM))
    assert c.get_album("a1") == ALBUM
    assert c.get_album("a2") == ALBUM
    assert all(p.get("extra") == "tracks" for p in c.session.asked) and len(c.session.asked) == 2


def test_an_album_refused_either_way_costs_one_extra_request_ever(client):
    c = client(lambda p: (400, None))
    assert c.get_album("a1") is None
    assert len(c.session.asked) == 2
    assert c.get_album("a2") is None
    assert c.get_album("a3") is None
    assert len(c.session.asked) == 4          # one each again, as before
    assert all("extra" in p for p in c.session.asked[2:])


def test_a_400_after_extra_has_worked_is_about_that_album(client):
    c = client(lambda p: (400, None) if p["album_id"] == "bad" else (200, ALBUM))
    assert c.get_album("a1") == ALBUM
    assert c.get_album("bad") is None
    assert len(c.session.asked) == 2
    assert c.get_album("a2") == ALBUM
    assert c.session.asked[-1] == {"album_id": "a2", "extra": "tracks"}


def test_a_failure_that_is_not_a_400_is_not_retried(client):
    c = client(lambda p: (500, None))
    assert c.get_album("a1") is None
    assert len(c.session.asked) == 1
