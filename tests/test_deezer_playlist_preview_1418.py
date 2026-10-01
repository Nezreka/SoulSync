"""#1418: clicking a Deezer editorial card mirrored the playlist on the spot,
with no way to look at it first. the card now opens a preview, which needs
the track list fast: no per-album track-number pass (most of a full load's
time), which only a mirror needs for tagging."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.deezer_client as dz
from core.deezer_client import DeezerClient


def _client(monkeypatch):
    playlist = {
        'id': 777, 'title': 'Lofi Girl Beats', 'nb_tracks': 2,
        'picture_xl': 'https://cdn/p.jpg', 'creator': {'name': 'Deezer Editors'},
        'tracks': {'data': [
            {'id': 1, 'title': 'Snowfall', 'duration': 150,
             'artist': {'name': 'Oatmello'}, 'album': {'id': 10, 'title': 'Snow', 'cover_xl': 'c1'}},
            {'id': 2, 'title': 'Coffee Break', 'duration': 140,
             'artist': {'name': 'Kupla'}, 'album': {'id': 11, 'title': 'Cafe', 'cover_xl': 'c2'}},
        ]},
    }
    client = DeezerClient.__new__(DeezerClient)
    client.session = SimpleNamespace(get=lambda url, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: playlist))
    album_lookups = []
    monkeypatch.setattr(dz, 'resolve_album_track_positions',
                        lambda *a, **k: album_lookups.append(a[2]) or {'1': 7, '2': 3})
    return client, album_lookups


def test_the_preview_skips_the_per_album_pass(monkeypatch):
    client, album_lookups = _client(monkeypatch)
    out = client.get_playlist('777', resolve_track_numbers=False)
    assert album_lookups == []
    assert [t['name'] for t in out['tracks']] == ['Snowfall', 'Coffee Break']


def test_a_full_load_still_gets_real_track_numbers(monkeypatch):
    client, album_lookups = _client(monkeypatch)
    out = client.get_playlist('777')
    assert album_lookups and [t['track_number'] for t in out['tracks']] == [7, 3]


web_server = pytest.importorskip('web_server')


def test_the_preview_endpoint_returns_a_track_list(monkeypatch):
    import api.source_playlists as sp

    client, album_lookups = _client(monkeypatch)
    monkeypatch.setattr(sp, '_get_deezer_client', lambda: client)
    body = web_server.app.test_client().get('/api/discover/deezer/playlist/777/preview').get_json()
    assert body['success'] and body['name'] == 'Lofi Girl Beats'
    assert body['tracks'][0] == {
        'id': '1', 'name': 'Snowfall', 'artists': [{'name': 'Oatmello'}], 'album_name': 'Snow',
        'album_cover_url': 'c1', 'duration_ms': 150000, 'source': 'deezer'}
    assert album_lookups == []
