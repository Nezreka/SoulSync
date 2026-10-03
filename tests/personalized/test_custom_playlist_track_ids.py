"""S8: build_custom_playlist must label track ids with the provider-correct
key — an iTunes id must not be returned under ``spotify_track_id``."""

from __future__ import annotations

import random
import sqlite3

import pytest

import core.metadata_service as ms
from core.personalized_playlists import PersonalizedPlaylistsService


class _FakeConn:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *a):
        return False


class _FakeDB:
    def __init__(self):
        self._conn = sqlite3.connect(':memory:')
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""CREATE TABLE similar_artists (
            source_artist_id TEXT, similar_artist_spotify_id TEXT,
            similar_artist_itunes_id TEXT, similar_artist_deezer_id TEXT,
            similar_artist_musicbrainz_id TEXT, similar_artist_name TEXT,
            similarity_rank INTEGER)""")
        self._conn.execute("""INSERT INTO similar_artists VALUES
            ('seed1', NULL, 'itunes-artist-9', 'deezer-artist-9', NULL, 'Similar One', 1)""")
        self._conn.commit()

    def _get_connection(self):
        return _FakeConn(self._conn)


class _FakeAlbumObj:
    def __init__(self, id):
        self.id = id


class _FakePrimaryClient:
    """Primary (non-Spotify) client: get_artist_albums/get_album shape."""

    def __init__(self, track_id):
        self._track_id = track_id

    def get_artist_albums(self, artist_id, limit=10):
        return [_FakeAlbumObj('album-7')]

    def get_album(self, album_id, include_tracks=False):
        return {
            'name': 'Fake Album', 'images': [],
            'tracks': {'items': [
                {'id': self._track_id, 'name': 'Fake Track',
                 'artists': [{'name': 'Similar One'}], 'duration_ms': 200000},
            ]},
        }


def _build(track_id, active_source, monkeypatch):
    monkeypatch.setattr(ms, 'get_primary_client', lambda: _FakePrimaryClient(track_id))
    monkeypatch.setattr(random, 'shuffle', lambda x: None)  # deterministic
    svc = PersonalizedPlaylistsService(_FakeDB(), spotify_client=None)
    svc._get_active_source = lambda: active_source
    result = svc.build_custom_playlist(['seed1'], playlist_size=5)
    assert result.get('tracks'), result.get('error')
    return result['tracks'][0]


def test_itunes_track_id_used_for_itunes_source(monkeypatch):
    t = _build('itunes-track-123', 'itunes', monkeypatch)
    assert t['itunes_track_id'] == 'itunes-track-123'
    assert t.get('spotify_track_id') is None
    assert t['id'] == 'itunes-track-123'


def test_deezer_track_id_used_for_deezer_source(monkeypatch):
    t = _build('deezer-track-456', 'deezer', monkeypatch)
    assert t['deezer_track_id'] == 'deezer-track-456'
    assert t.get('spotify_track_id') is None
    assert t['id'] == 'deezer-track-456'


def test_spotify_branch_still_uses_spotify_track_id(monkeypatch):
    """The genuine Spotify path is untouched by the S8 fix."""
    class _SpotAlbum:
        id = 'sp-album-1'

    class _SpotClient:
        sp = True

        def get_artist_albums(self, artist_id, album_type='album,single', limit=10):
            return [_SpotAlbum()]

        def get_album(self, album_id):
            return {
                'name': 'Spot Album', 'images': [], 'popularity': 80,
                'tracks': {'items': [
                    {'id': 'spotify-track-789', 'name': 'Spot Track',
                     'artists': [{'name': 'Similar One'}], 'duration_ms': 200000},
                ]},
            }

    monkeypatch.setattr(random, 'shuffle', lambda x: None)
    svc = PersonalizedPlaylistsService(_FakeDB(), spotify_client=_SpotClient())
    svc._get_active_source = lambda: 'spotify'
    result = svc.build_custom_playlist(['seed1'], playlist_size=5)
    assert result.get('tracks'), result.get('error')
    t = result['tracks'][0]
    assert t['spotify_track_id'] == 'spotify-track-789'
    assert t['id'] == 'spotify-track-789'
