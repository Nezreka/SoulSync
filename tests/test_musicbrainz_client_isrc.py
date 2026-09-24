"""`MusicBrainzClient.lookup_recordings_by_isrc` (#903 export ISRC rung).

Same fail-soft contract as `search_recording`/`get_recording`: a malformed ISRC never
reaches the network, an unknown one (404) or any transport error both come back as `[]`.
Uses the same no-network `_Session`/`_client` harness as
`tests/test_musicbrainz_client_resilience.py` so this doesn't pay the real
`_wait_for_musicbrainz_slot` pacing delay.
"""

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


ISRC = "USUM71703861"


def test_valid_isrc_parses_recordings(monkeypatch):
    payload = {"isrc": ISRC, "recordings": [{"id": "mbid-1", "title": "Shape of You"}]}
    session = _Session([_Response(payload)])
    client = _client(session)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *a: None)

    recordings = client.lookup_recordings_by_isrc(ISRC)

    assert recordings == [{"id": "mbid-1", "title": "Shape of You"}]
    assert len(session.calls) == 1
    assert session.calls[0]['url'].endswith(f"/isrc/{ISRC}")
    assert session.calls[0]['params']['inc'] == 'artist-credits'


def test_isrc_is_normalized_hyphens_and_case(monkeypatch):
    session = _Session([_Response({"recordings": []})])
    client = _client(session)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *a: None)

    client.lookup_recordings_by_isrc("us-um7-1703861".upper())

    assert session.calls[0]['url'].endswith(f"/isrc/{ISRC}")


def test_invalid_isrc_returns_empty_without_calling_get(monkeypatch):
    session = _Session([])   # any .get call would pop from an empty list and raise
    client = _client(session)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *a: None)

    assert client.lookup_recordings_by_isrc("too-short") == []
    assert client.lookup_recordings_by_isrc("") == []
    assert client.lookup_recordings_by_isrc(None) == []
    assert session.calls == []


def test_404_returns_empty(monkeypatch):
    session = _Session([_Response(status_code=404)])
    client = _client(session)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *a: None)

    assert client.lookup_recordings_by_isrc(ISRC) == []


def test_transport_exception_returns_empty(monkeypatch):
    session = _Session([requests.exceptions.ConnectionError("dns blew up")])
    client = _client(session)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *a: None)

    assert client.lookup_recordings_by_isrc(ISRC) == []


def test_rate_limiting_is_honoured(monkeypatch):
    """`lookup_recordings_by_isrc` goes through the shared `_get`, so it waits for a
    pacing slot exactly like every other MusicBrainz call."""
    session = _Session([_Response({"recordings": []})])
    client = _client(session)
    waited = []
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot',
                        lambda *a: waited.append(True))

    client.lookup_recordings_by_isrc(ISRC)

    assert waited == [True]
