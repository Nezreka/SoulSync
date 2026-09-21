from __future__ import annotations

import pytest
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


# --- p5-mb-busy-negcache: a 200 OK "server busy" body is a transient failure ---
#
# MusicBrainz sometimes answers overload with HTTP 200 and a body like
# {"error": "The MusicBrainz web server is currently busy. Please try again
# later."} instead of a 503. raise_for_status() never fires on a 200, so
# without detecting this shape every caller's `data.get('recordings', [])`
# quietly returns `[]` — identical to a genuine empty result, and (via
# search_recording/search_release/search_artist) that got written down as a
# 30-day negative cache entry for an outage.

_BUSY_BODY = {'error': 'The MusicBrainz web server is currently busy. Please try again later.'}


def test_busy_body_is_retried_like_a_503(monkeypatch):
    session = _Session([_Response(_BUSY_BODY), _Response({'recordings': [{'id': 'rec-1'}]})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    response = client._get('/recording', params={'query': 'recording:\"Song\"'})

    assert response.json() == {'recordings': [{'id': 'rec-1'}]}
    assert len(session.calls) == 2


def test_busy_body_propagates_like_a_503_after_retries_exhausted(monkeypatch):
    session = _Session([_Response(_BUSY_BODY), _Response(_BUSY_BODY)])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    import core.musicbrainz_client as mbc
    with pytest.raises(mbc.MusicBrainzBusyError):
        client._get('/recording', params={'query': 'recording:\"Song\"'})
    assert len(session.calls) == 2


def test_a_non_busy_200_error_body_is_not_retried(monkeypatch):
    # MusicBrainz also reports genuine, non-transient failures (e.g. a
    # malformed Lucene query) as an `error` key at HTTP 200. Retrying one
    # would just repeat the same 200 across the whole retry budget for a
    # request that was never going to succeed — only "busy"/"try again"
    # wording should be treated as transient.
    session = _Session([_Response({'error': 'Invalid search syntax near: foo:('})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)

    response = client._get('/recording', params={'query': 'recording:\"Song\"'})

    assert response.json() == {'error': 'Invalid search syntax near: foo:('}
    assert len(session.calls) == 1


def test_search_recording_returns_empty_for_a_non_busy_200_error_body(monkeypatch):
    session = _Session([_Response({'error': 'Invalid search syntax near: foo:('})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)

    assert client.search_recording('Song', 'Artist') == []
    assert len(session.calls) == 1


def test_legit_empty_result_is_not_mistaken_for_a_busy_body(monkeypatch):
    session = _Session([_Response({'recordings': []})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)

    response = client._get('/recording', params={'query': 'recording:\"Nonexistent Song\"'})

    assert response.json() == {'recordings': []}
    assert len(session.calls) == 1


def test_search_recording_fails_soft_on_a_busy_body_by_default(monkeypatch):
    session = _Session([_Response(_BUSY_BODY), _Response(_BUSY_BODY)])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    assert client.search_recording('Song', 'Artist') == []
    assert len(session.calls) == 2


def test_search_recording_raises_on_a_busy_body_when_asked(monkeypatch):
    session = _Session([_Response(_BUSY_BODY), _Response(_BUSY_BODY)])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    import core.musicbrainz_client as mbc
    with pytest.raises(mbc.MusicBrainzBusyError):
        client.search_recording('Song', 'Artist', raise_on_error=True)
    assert len(session.calls) == 2
