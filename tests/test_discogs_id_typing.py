"""Tests for Discogs master-vs-release ID disambiguation.

Discogs has two album object types — masters (``/masters/{id}``) and releases
(``/releases/{id}``) — whose numeric IDs share ONE namespace, so release N and
master N are different albums. The old fetch code tried ``/masters/{id}`` first
and fell back to ``/releases/{id}``; because ``search_albums`` only ever yields
RELEASE ids, any release id that happened to collide with a real master id
returned a valid-but-WRONG album (the fallback never fired). See
``core.discogs_client`` ID-typing helpers.

The fix tags the type into the id string ('r12345' / 'm12345') at parse time and
routes each fetch to the matching endpoint, with legacy bare ids tried
release-first (and master only as a fallback).
"""

import pytest

from core.discogs_client import (
    Album,
    DiscogsClient,
    _discogs_album_endpoints,
    _discogs_album_kind,
    _tag_discogs_album_id,
)


# ---------------------------------------------------------------------------
# _discogs_album_endpoints — the routing table
# ---------------------------------------------------------------------------

def test_release_tagged_id_hits_only_releases():
    assert _discogs_album_endpoints('r12345') == ['/releases/12345']


def test_master_tagged_id_hits_only_masters():
    assert _discogs_album_endpoints('m12345') == ['/masters/12345']


def test_legacy_bare_id_tries_release_first_then_master():
    # The crux of the fix: bare ids are release-first, NOT master-first.
    assert _discogs_album_endpoints('12345') == ['/releases/12345', '/masters/12345']


def test_unusable_ids_return_empty():
    assert _discogs_album_endpoints('') == []
    assert _discogs_album_endpoints(None) == []
    assert _discogs_album_endpoints('not-an-id') == []
    # A lone letter prefix with no digits is not a valid tagged id.
    assert _discogs_album_endpoints('r') == []


# ---------------------------------------------------------------------------
# _discogs_album_kind / _tag_discogs_album_id — classification + tagging
# ---------------------------------------------------------------------------

def test_kind_from_explicit_type():
    assert _discogs_album_kind({'type': 'master'}) == 'master'
    assert _discogs_album_kind({'type': 'release'}) == 'release'


def test_kind_full_master_detail_has_main_release():
    # Full /masters/{id} responses carry no `type`, but do carry `main_release`.
    assert _discogs_album_kind({'id': 1, 'main_release': 42, 'title': 'X'}) == 'master'


def test_kind_full_release_detail_defaults_release():
    # Full /releases/{id} responses carry no `type` and no `main_release`.
    assert _discogs_album_kind({'id': 1, 'title': 'X', 'master_id': 42}) == 'release'


def test_tagging():
    assert _tag_discogs_album_id('123', 'master') == 'm123'
    assert _tag_discogs_album_id('123', 'release') == 'r123'
    assert _tag_discogs_album_id('', 'release') == ''
    assert _tag_discogs_album_id(None, 'master') == ''


# ---------------------------------------------------------------------------
# Album.from_discogs_release — the single tagging point
# ---------------------------------------------------------------------------

def test_search_result_tagged_as_release():
    # search_albums uses type=release; results carry type='release'.
    album = Album.from_discogs_release({'id': 999, 'title': 'Radiohead - OK Computer',
                                        'type': 'release'})
    assert album.id == 'r999'


def test_discography_master_tagged_as_master():
    album = Album.from_discogs_release({'id': 777, 'title': 'OK Computer', 'type': 'master'})
    assert album.id == 'm777'


def test_full_master_detail_tagged_as_master():
    album = Album.from_discogs_release({'id': 777, 'title': 'OK Computer', 'main_release': 12})
    assert album.id == 'm777'


# ---------------------------------------------------------------------------
# Fetch routing — the regression lock on the original bug
# ---------------------------------------------------------------------------

class _FakeCache:
    """Always-miss metadata cache."""
    def get_entity(self, *a, **k):
        return None

    def store_entity(self, *a, **k):
        pass

    def get_search_results(self, *a, **k):
        return None

    def store_search_results(self, *a, **k):
        pass

    def store_entities_bulk(self, *a, **k):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr('core.discogs_client.get_metadata_cache', lambda: _FakeCache())
    return DiscogsClient(token='test-token')


def _record(client, monkeypatch, responses):
    """Replace _api_get with a recorder that returns `responses[path]`."""
    calls = []

    def fake_api_get(endpoint, params=None):
        calls.append(endpoint)
        return responses.get(endpoint)

    monkeypatch.setattr(client, '_api_get', fake_api_get)
    return calls


def test_get_album_release_id_never_hits_masters(client, monkeypatch):
    """A release-tagged id must NOT touch /masters — that was the bug."""
    calls = _record(client, monkeypatch, {
        '/releases/249504': {'id': 249504, 'title': 'The Real Album', 'artists': [{'name': 'A'}]},
        '/masters/249504': {'id': 249504, 'title': 'A DIFFERENT Album', 'artists': [{'name': 'B'}]},
    })
    result = client.get_album('r249504', include_tracks=False)
    assert calls == ['/releases/249504']            # master endpoint never consulted
    assert result['name'] == 'The Real Album'


def test_get_album_master_id_hits_masters_only(client, monkeypatch):
    calls = _record(client, monkeypatch, {
        '/masters/777': {'id': 777, 'title': 'Master Album', 'main_release': 1},
    })
    result = client.get_album('m777', include_tracks=False)
    assert calls == ['/masters/777']
    assert result['name'] == 'Master Album'


def test_legacy_bare_id_release_first(client, monkeypatch):
    """Legacy untagged id resolves as a release without ever hitting /masters
    when the release lookup succeeds."""
    calls = _record(client, monkeypatch, {
        '/releases/249504': {'id': 249504, 'title': 'The Real Album'},
        '/masters/249504': {'id': 249504, 'title': 'A DIFFERENT Album'},
    })
    result = client.get_album('249504', include_tracks=False)
    assert calls == ['/releases/249504']
    assert result['name'] == 'The Real Album'


def test_legacy_bare_id_falls_back_to_master(client, monkeypatch):
    """If the release lookup yields nothing, the bare id still tries master."""
    calls = _record(client, monkeypatch, {
        '/releases/777': None,
        '/masters/777': {'id': 777, 'title': 'Master Only', 'main_release': 1},
    })
    result = client.get_album('777', include_tracks=False)
    assert calls == ['/releases/777', '/masters/777']
    assert result['name'] == 'Master Only'


def test_fetch_and_cache_release_id_never_hits_masters(client, monkeypatch):
    calls = _record(client, monkeypatch, {
        '/releases/249504': {'id': 249504, 'title': 'The Real Album'},
        '/masters/249504': {'id': 249504, 'title': 'A DIFFERENT Album'},
    })
    data = client._fetch_and_cache_album('r249504')
    assert calls == ['/releases/249504']
    assert data['title'] == 'The Real Album'
