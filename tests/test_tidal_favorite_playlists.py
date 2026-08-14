"""Tidal followed-playlist parsing (Specialmed's report: the V2 owners.id
filter lists only playlists the user CREATED, so followed/editorial
playlists never appeared). The v1 favorites rows are parsed by
TidalClient._favorite_playlist_from_item — pinned here hermetically, no
network."""

from __future__ import annotations

from core.tidal_client import TidalClient


def test_wrapped_favorites_row_parses():
    playlist = TidalClient._favorite_playlist_from_item({
        'created': '2026-08-01T00:00:00.000+0000',
        'item': {
            'uuid': 'aaaa-bbbb',
            'title': 'TOP 100 Germany',
            'description': 'The hits',
            'numberOfTracks': 100,
            'squareImage': '12ab-34cd-56ef',
            'publicPlaylist': True,
            'creator': {'id': 0},
        },
    })
    assert playlist is not None
    assert playlist.id == 'aaaa-bbbb'
    assert playlist.name == 'TOP 100 Germany'
    assert playlist.track_count == 100
    # Editorial rows (creator id 0 / nameless) are labeled Tidal.
    assert playlist.owner == 'Tidal'
    assert playlist.image_url == 'https://resources.tidal.com/images/12ab/34cd/56ef/640x640.jpg'
    assert playlist.tracks == []  # on-demand, like the owned listing


def test_flat_row_and_named_creator():
    playlist = TidalClient._favorite_playlist_from_item({
        'uuid': 'cccc-dddd',
        'title': 'Friend Mix',
        'creator': {'id': 777, 'name': 'somefriend'},
    })
    assert playlist is not None
    assert playlist.owner == 'somefriend'
    assert playlist.track_count == 0
    # No image GUID → the attribute is never set (the endpoint reads it
    # via getattr(p, 'image_url', None), like the owned listing's rows).
    assert getattr(playlist, 'image_url', None) is None


def test_row_without_uuid_is_skipped():
    assert TidalClient._favorite_playlist_from_item({'item': {'title': 'ghost'}}) is None


# ── the V2 user-collection path (the ACTUAL fix) ─────────────────────────────
#
# Specialmed installed the dev build carrying the v1-favorites merge and
# reported no change. Root cause: v1 `/users/<id>/favorites/playlists` returns
# 403 for modern OAuth tokens (documented on `get_favorite_albums`, which was
# rewritten off v1 for exactly that reason), and the merge logged at INFO and
# degraded silently — so a broken fix and no fix looked identical.
#
# The V2 `userCollectionPlaylists` endpoint is the same family that already
# works for favorited albums/artists/tracks here, so it now runs FIRST.

import pytest


class _Resp:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _client():
    """A client with no __init__ side effects (no network, no token load)."""
    client = TidalClient.__new__(TidalClient)
    client.base_url = 'https://openapi.tidal.com/v2'
    client.alt_base_url = 'https://api.tidal.com/v1'
    client.access_token = 'tok'

    class _Session:
        headers = {}
    client.session = _Session()
    return client


_V2_PLAYLIST_PAGE = {
    'data': [{
        'id': 'ed-1',
        'type': 'playlists',
        'attributes': {'name': 'TOP 100 Germany', 'description': 'The hits',
                       'numberOfItems': 100, 'accessType': 'PUBLIC'},
        'relationships': {},
    }]
}


def test_followed_playlists_come_from_the_v2_collection(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, '_iter_collection_resource_ids',
                        lambda path, kind, *a, **k: ['ed-1'])
    monkeypatch.setattr('core.tidal_client.requests.get',
                        lambda *a, **k: _Resp(200, _V2_PLAYLIST_PAGE))

    rows = client._get_collection_playlists()

    assert [p.id for p in rows] == ['ed-1']
    assert rows[0].name == 'TOP 100 Germany'
    assert rows[0].track_count == 100
    # Followed rows say where they came from; the owned listing leaves it unset.
    assert rows[0].owner == 'Tidal'


def test_the_collection_endpoint_is_the_playlists_one(monkeypatch):
    """Guards against pointing the walk at the albums/artists path — it would
    return ids that hydrate to nothing, i.e. the same invisible failure."""
    seen = {}
    client = _client()
    monkeypatch.setattr(client, '_iter_collection_resource_ids',
                        lambda path, kind, *a, **k: seen.update(path=path, kind=kind) or [])
    monkeypatch.setattr(client, 'collection_needs_reconnect', lambda: False)

    client._get_collection_playlists()

    assert seen['path'] == 'userCollectionPlaylists/me/relationships/items'
    assert seen['kind'] == 'playlists'


def test_v1_is_not_called_when_the_collection_answers(monkeypatch):
    """v1 403s for modern tokens — it must not be the thing we rely on."""
    client = _client()
    playlist = TidalClient._favorite_playlist_from_item({'uuid': 'x', 'title': 'y'})
    monkeypatch.setattr(client, '_get_collection_playlists', lambda: [playlist])

    def _boom(*a, **k):
        raise AssertionError("v1 favorites must not run when V2 succeeded")
    monkeypatch.setattr('core.tidal_client.requests.get', _boom)

    assert client._get_favorite_playlists('user-1') == [playlist]


def test_v1_still_runs_when_the_collection_is_empty(monkeypatch):
    """A token old enough to carry `r_usr` is still served by v1, so the
    fallback stays — it just is not the primary any more."""
    client = _client()
    monkeypatch.setattr(client, '_get_collection_playlists', lambda: [])
    calls = []

    def _v1(url, *a, **k):
        calls.append(url)
        return _Resp(200, {'items': [{'item': {'uuid': 'v1-1', 'title': 'Legacy'}}]})
    monkeypatch.setattr('core.tidal_client.requests.get', _v1)

    rows = client._get_favorite_playlists('user-1')

    assert [p.id for p in rows] == ['v1-1']
    assert 'favorites/playlists' in calls[0]


def test_a_scopeless_token_is_reported_not_swallowed(monkeypatch, caplog):
    """THE bug behind the round trip: the failure was invisible. A token
    without collection scope must say so, and say what to do about it."""
    client = _client()
    monkeypatch.setattr(client, '_iter_collection_resource_ids', lambda *a, **k: [])
    monkeypatch.setattr(client, 'collection_needs_reconnect', lambda: True)

    with caplog.at_level('WARNING'):
        assert client._get_collection_playlists() == []

    assert any('Reconnect Tidal' in r.message for r in caplog.records), caplog.text


def test_a_failed_hydrate_does_not_take_the_listing_down(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, '_iter_collection_resource_ids', lambda *a, **k: ['ed-1'])
    monkeypatch.setattr('core.tidal_client.requests.get',
                        lambda *a, **k: _Resp(403, text='forbidden'))

    assert client._get_collection_playlists() == []


def test_hydration_is_batched_within_the_filter_id_cap(monkeypatch):
    """`filter[id]` is capped at _COLLECTION_BATCH_SIZE ids per request — asking
    for more in one URL silently truncates the response."""
    client = _client()
    ids = [f'p{i}' for i in range(45)]
    monkeypatch.setattr(client, '_iter_collection_resource_ids', lambda *a, **k: ids)
    batches = []

    def _capture(url, params=None, **k):
        batches.append(params['filter[id]'].split(','))
        return _Resp(200, {'data': []})
    monkeypatch.setattr('core.tidal_client.requests.get', _capture)

    client._get_collection_playlists()

    assert len(batches) == 3                       # 45 ids / 20 per request
    assert all(len(b) <= TidalClient._COLLECTION_BATCH_SIZE for b in batches)
    assert sorted(i for b in batches for i in b) == sorted(ids)
