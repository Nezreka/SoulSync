"""Tests for core/search/library_check.py — library/wishlist presence + thumb resolution."""

from __future__ import annotations

import json

import pytest

from core.search import library_check
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


# ---------------------------------------------------------------------------
# Fakes for plex / config_manager
# ---------------------------------------------------------------------------

class _FakePlexServer:
    def __init__(self, base, token):
        self._baseurl = base
        self._token = token


class _FakePlexClient:
    def __init__(self, base='https://plex.local:32400', token='abc123'):
        self.server = _FakePlexServer(base, token)


class _NoServerPlexClient:
    """Plex client that hasn't connected yet."""
    server = None


class _FakeConfigManager:
    def __init__(self, plex_cfg=None):
        self._plex_cfg = plex_cfg or {}

    def get_plex_config(self):
        return dict(self._plex_cfg)

    def get(self, key, default=None):
        return default


# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------

_id_counter = {'n': 0}


def _next_id(prefix):
    _id_counter['n'] += 1
    return f"{prefix}-{_id_counter['n']}"


def _seed_artist(db, name):
    aid = _next_id('art')
    conn = db._get_connection()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO artists (id, name) VALUES (?, ?)", (aid, name))
        conn.commit()
        return aid
    finally:
        conn.close()


def _seed_album(db, artist_id, title, thumb=None):
    alb = _next_id('alb')
    conn = db._get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO albums (id, artist_id, title, thumb_url) VALUES (?, ?, ?, ?)",
            (alb, artist_id, title, thumb),
        )
        conn.commit()
        return alb
    finally:
        conn.close()


def _seed_track(db, album_id, artist_id, title, file_path=None):
    tid = _next_id('trk')
    conn = db._get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES (?, ?, ?, ?, ?)",
            (tid, album_id, artist_id, title, file_path),
        )
        conn.commit()
        return tid
    finally:
        conn.close()


def _seed_wishlist(db, profile_id, name, artist_name):
    spotify_data = {'name': name, 'artists': [{'name': artist_name}]}
    conn = db._get_connection()
    try:
        c = conn.cursor()
        c.execute("PRAGMA table_info(wishlist_tracks)")
        cols = [r[1] for r in c.fetchall()]
        if 'profile_id' in cols:
            c.execute(
                "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) VALUES (?, ?, ?)",
                (f"sp-{name}-{artist_name}", json.dumps(spotify_data), profile_id),
            )
        else:
            c.execute(
                "INSERT INTO wishlist_tracks (spotify_track_id, spotify_data) VALUES (?, ?)",
                (f"sp-{name}-{artist_name}", json.dumps(spotify_data)),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Plex thumb resolution
# ---------------------------------------------------------------------------

def test_resolve_plex_thumb_already_absolute_passes_through():
    assert library_check._resolve_plex_thumb('http://x/y.jpg', 'https://plex', 'tok') == 'http://x/y.jpg'


def test_resolve_plex_thumb_relative_gets_base_and_token():
    out = library_check._resolve_plex_thumb('/library/x.jpg', 'https://plex.local:32400', 'tok123')
    assert out == 'https://plex.local:32400/library/x.jpg?X-Plex-Token=tok123'


def test_resolve_plex_thumb_no_token_omits_query_string():
    out = library_check._resolve_plex_thumb('/library/x.jpg', 'https://plex.local:32400', '')
    assert out == 'https://plex.local:32400/library/x.jpg'


def test_resolve_plex_thumb_no_base_passes_through():
    assert library_check._resolve_plex_thumb('/library/x.jpg', '', 'tok') == '/library/x.jpg'


def test_resolve_plex_thumb_empty_passes_through():
    assert library_check._resolve_plex_thumb('', 'https://plex', 'tok') == ''


def test_resolve_plex_credentials_uses_live_client_first():
    cfg = _FakeConfigManager({'base_url': 'https://wrong', 'token': 'wrongtok'})
    base, token = library_check._resolve_plex_credentials(_FakePlexClient(), cfg)
    assert base == 'https://plex.local:32400'
    assert token == 'abc123'


def test_resolve_plex_credentials_falls_back_to_config():
    cfg = _FakeConfigManager({'base_url': 'https://configured/', 'token': 'cfgtok'})
    base, token = library_check._resolve_plex_credentials(_NoServerPlexClient(), cfg)
    assert base == 'https://configured'
    assert token == 'cfgtok'


def test_resolve_plex_credentials_handles_no_config():
    cfg = _FakeConfigManager({})
    base, token = library_check._resolve_plex_credentials(_NoServerPlexClient(), cfg)
    assert base == ''
    assert token == ''


# ---------------------------------------------------------------------------
# check_library_presence — albums
# ---------------------------------------------------------------------------

def test_album_in_library_returns_true(db):
    aid = _seed_artist(db, 'Pink Floyd')
    _seed_album(db, aid, 'DSOTM')
    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), cfg, profile_id=1,
        albums=[{'name': 'DSOTM', 'artist': 'Pink Floyd'}],
        tracks=[],
    )
    assert result['albums'] == [True]


def test_album_not_in_library_returns_false(db):
    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), cfg, profile_id=1,
        albums=[{'name': 'Phantom', 'artist': 'Nobody'}],
        tracks=[],
    )
    assert result['albums'] == [False]


def test_album_lookup_uses_first_artist_in_csv(db):
    aid = _seed_artist(db, 'Pink Floyd')
    _seed_album(db, aid, 'DSOTM')
    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), cfg, profile_id=1,
        albums=[{'name': 'DSOTM', 'artist': 'Pink Floyd, Roger Waters'}],
        tracks=[],
    )
    assert result['albums'] == [True]


# ---------------------------------------------------------------------------
# check_library_presence — tracks
# ---------------------------------------------------------------------------

def test_track_in_library_returns_full_match_metadata(db):
    aid = _seed_artist(db, 'Pink Floyd')
    alb = _seed_album(db, aid, 'DSOTM', thumb='/library/dsotm.jpg')
    tid = _seed_track(db, alb, aid, 'Money', file_path='/m/money.flac')
    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _FakePlexClient(), cfg, profile_id=1,
        albums=[],
        tracks=[{'name': 'Money', 'artist': 'Pink Floyd'}],
    )
    track = result['tracks'][0]
    assert track['in_library'] is True
    assert track['track_id'] == tid
    assert track['file_path'] == '/m/money.flac'
    assert track['title'] == 'Money'
    assert track['artist_name'] == 'Pink Floyd'
    assert track['album_title'] == 'DSOTM'
    assert 'X-Plex-Token=abc123' in track['album_thumb_url']
    assert track['album_thumb_url'].startswith('https://plex.local:32400')


def test_track_not_in_library_returns_minimal_shape(db):
    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), cfg, profile_id=1,
        albums=[],
        tracks=[{'name': 'Phantom', 'artist': 'Nobody'}],
    )
    assert result['tracks'] == [{'in_library': False, 'in_wishlist': False}]


def test_track_in_wishlist_returns_in_wishlist_true(db):
    _seed_wishlist(db, profile_id=1, name='HUMBLE.', artist_name='Kendrick Lamar')
    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), cfg, profile_id=1,
        albums=[],
        tracks=[{'name': 'HUMBLE.', 'artist': 'Kendrick Lamar'}],
    )
    assert result['tracks'][0] == {'in_library': False, 'in_wishlist': True}


def test_track_in_library_and_wishlist_both_set(db):
    aid = _seed_artist(db, 'Kendrick Lamar')
    alb = _seed_album(db, aid, 'DAMN.')
    _seed_track(db, alb, aid, 'HUMBLE.')
    _seed_wishlist(db, profile_id=1, name='HUMBLE.', artist_name='Kendrick Lamar')

    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), cfg, profile_id=1,
        albums=[],
        tracks=[{'name': 'HUMBLE.', 'artist': 'Kendrick Lamar'}],
    )
    assert result['tracks'][0]['in_library'] is True
    assert result['tracks'][0]['in_wishlist'] is True


def test_track_artist_csv_uses_first_only(db):
    aid = _seed_artist(db, 'Kendrick Lamar')
    alb = _seed_album(db, aid, 'DAMN.')
    _seed_track(db, alb, aid, 'HUMBLE.', file_path='/x.flac')
    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), cfg, profile_id=1,
        albums=[],
        tracks=[{'name': 'HUMBLE.', 'artist': 'Kendrick Lamar, J. Cole'}],
    )
    assert result['tracks'][0]['in_library'] is True
