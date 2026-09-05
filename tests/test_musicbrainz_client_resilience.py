from __future__ import annotations

import requests

from core.musicbrainz_client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    MusicBrainzClient,
)


class _Response:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error", response=self)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, outcomes):
        self.headers = {}
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, *, params=None, timeout=None, allow_redirects=False):
        self.calls.append({'url': url, 'params': params, 'timeout': timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(session, *, retries=0):
    client = MusicBrainzClient.__new__(MusicBrainzClient)
    client.session = session
    client.connect_timeout = DEFAULT_CONNECT_TIMEOUT
    client.read_timeout = DEFAULT_READ_TIMEOUT
    client.max_retries = retries
    return client


def test_musicbrainz_requests_use_generous_read_timeout():
    session = _Session([_Response({'artists': []})])
    client = _client(session)

    response = client._get('/artist', params={'query': 'artist:\"Fast Pussycats\"'})

    assert response.json() == {'artists': []}
    assert session.calls[0]['timeout'] == (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)
    assert DEFAULT_READ_TIMEOUT >= 30


def test_musicbrainz_read_timeout_is_retried_with_global_pacing(monkeypatch):
    session = _Session([
        requests.exceptions.ReadTimeout('delayed by upstream'),
        _Response({'artists': [{'id': 'mbid', 'name': 'Fast Pussycats'}]}),
    ])
    client = _client(session, retries=1)
    waits = []
    sleeps = []
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: waits.append('slot'))
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda seconds: sleeps.append(seconds))

    response = client._get('/artist', params={'query': 'artist:\"Fast Pussycats\"'})

    assert response.json()['artists'][0]['name'] == 'Fast Pussycats'
    assert len(session.calls) == 2
    assert waits == ['slot', 'slot']
    assert sleeps == [2.0]


def test_musicbrainz_503_is_retried(monkeypatch):
    session = _Session([_Response(status_code=503), _Response({'releases': []})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    response = client._get('/release', params={'query': 'release:\"Album\"'})

    assert response.json() == {'releases': []}
    assert len(session.calls) == 2
