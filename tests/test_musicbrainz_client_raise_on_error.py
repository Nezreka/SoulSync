"""the raise_on_error seam on the REAL MusicBrainzClient.

pr #1185's poisoned-cache fix hangs on two lines in this client: a transport
failure must be re-raisable so the alias service can tell "MusicBrainz never
answered" from "MusicBrainz knows nobody by that name". the resilience suite
proves the SERVICE handles a raising client - via a fake that already honors
the flag - so without this file the real client could silently lose the raise
and every test would stay green while production regressed straight back to
caching outages as no-alias answers.
"""

import pytest
import requests

from core.musicbrainz_client import MusicBrainzClient


@pytest.fixture
def client(monkeypatch):
    import core.musicbrainz_client as mbc
    # no rate-limit sleeping in a unit test
    monkeypatch.setattr(mbc, '_wait_for_musicbrainz_slot', lambda *args: None)
    c = MusicBrainzClient()

    def _boom(path, *, params=None):
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(c, '_get', _boom)
    return c


def test_search_artist_fail_soft_by_default(client):
    assert client.search_artist("Sawano Hiroyuki") == []


def test_search_artist_raises_when_asked(client):
    with pytest.raises(requests.Timeout):
        client.search_artist("Sawano Hiroyuki", raise_on_error=True)


def test_get_artist_fail_soft_by_default(client):
    assert client.get_artist("mbid-x", includes=['aliases']) is None


def test_get_artist_raises_when_asked(client):
    with pytest.raises(requests.Timeout):
        client.get_artist("mbid-x", includes=['aliases'], raise_on_error=True)


# ── get_recording (#903 export-verify follow-up, MAJOR-1): raise_on_error must propagate
# a TRANSPORT failure (so the caller can fail open) but a genuine 404 always returns None,
# even with raise_on_error=True — "MusicBrainz is down" and "this MBID doesn't exist" are
# different answers and must not collapse into the same one. ──

def test_get_recording_fail_soft_by_default(client):
    assert client.get_recording("some-mbid") is None


def test_get_recording_raises_transport_failure_when_asked(client):
    with pytest.raises(requests.Timeout):
        client.get_recording("some-mbid", raise_on_error=True)


def _http_error(status_code):
    resp = requests.Response()
    resp.status_code = status_code
    return requests.HTTPError(f"HTTP {status_code}", response=resp)


def test_get_recording_404_returns_none_even_with_raise_on_error(monkeypatch):
    import core.musicbrainz_client as mbc
    monkeypatch.setattr(mbc, '_wait_for_musicbrainz_slot', lambda *args: None)
    c = MusicBrainzClient()
    monkeypatch.setattr(c, '_get', lambda path, *, params=None: (_ for _ in ()).throw(_http_error(404)))
    assert c.get_recording("missing-mbid") is None
    assert c.get_recording("missing-mbid", raise_on_error=True) is None


def test_get_recording_503_raises_when_asked(monkeypatch):
    import core.musicbrainz_client as mbc
    monkeypatch.setattr(mbc, '_wait_for_musicbrainz_slot', lambda *args: None)
    c = MusicBrainzClient()
    monkeypatch.setattr(c, '_get', lambda path, *, params=None: (_ for _ in ()).throw(_http_error(503)))
    assert c.get_recording("some-mbid") is None            # fail-soft default
    with pytest.raises(requests.HTTPError):
        c.get_recording("some-mbid", raise_on_error=True)  # transport-ish failure -> raised
