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


def _next_id():
    """A legacy id — what ``track_id`` in the response still means (§50.4.4.14)."""
    _id_counter['n'] += 1
    return 2000 + _id_counter['n']


def _lib2(db, sql, params=()):
    conn = db._get_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _seed_artist(db, name):
    from core.library2.importer import normalize_name

    legacy = _next_id()
    _lib2(
        db,
        "INSERT INTO lib2_artists (name, name_key, sort_name, legacy_artist_id) "
        "VALUES (?, ?, ?, ?)",
        (name, normalize_name(name), name, legacy),
    )
    return legacy


def _seed_album(db, artist_legacy_id, title, thumb=None, origin='library'):
    legacy = _next_id()
    _lib2(
        db,
        "INSERT INTO lib2_albums (primary_artist_id, title, image_url, origin, "
        "legacy_album_id) SELECT id, ?, ?, ?, ? FROM lib2_artists "
        "WHERE legacy_artist_id = ?",
        (title, thumb, origin, legacy, artist_legacy_id),
    )
    if origin == 'library':
        _lib2(db, "INSERT INTO lib2_tracks(album_id,title) SELECT id,? FROM lib2_albums "
                  "WHERE legacy_album_id=?", (f'owned-{legacy}', legacy))
        _lib2(db, "INSERT INTO lib2_track_files(track_id,path) SELECT id,? FROM lib2_tracks "
                  "WHERE title=?", (f'/seed/{legacy}.flac', f'owned-{legacy}'))
    return legacy


def _seed_track(db, album_legacy_id, artist_legacy_id, title, file_path=None):
    legacy = _next_id()
    _lib2(
        db,
        "INSERT INTO lib2_tracks (album_id, title, legacy_track_id) "
        "SELECT id, ?, ? FROM lib2_albums WHERE legacy_album_id = ?",
        (title, legacy, album_legacy_id),
    )
    if file_path is None:
        file_path = f'/seed/track-{legacy}.flac'
    if file_path:
        _lib2(
            db,
            "INSERT INTO lib2_track_files (track_id, path, is_primary) "
            "SELECT id, ?, 1 FROM lib2_tracks WHERE legacy_track_id = ?",
            (file_path, legacy),
        )
    return legacy


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


def test_a_provider_only_release_is_not_owned(db):
    """A discography row is a release we know about, not one we have. Badging it
    "in your library" is the exact claim this endpoint exists to refuse."""
    aid = _seed_artist(db, 'Pink Floyd')
    _seed_album(db, aid, 'The Wall', origin='discography')
    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), cfg, profile_id=1,
        albums=[{'name': 'The Wall', 'artist': 'Pink Floyd'}],
        tracks=[],
    )
    assert result['albums'] == [False]


def test_library_provenance_without_a_live_file_is_not_owned(db):
    aid = _seed_artist(db, 'Pink Floyd')
    album = _seed_album(db, aid, 'Missing')
    _lib2(db, "DELETE FROM lib2_track_files WHERE track_id IN "
              "(SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id "
              "WHERE al.legacy_album_id=?)", (album,))
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), _FakeConfigManager({}), profile_id=1,
        albums=[{'name': 'Missing', 'artist': 'Pink Floyd'}], tracks=[])
    assert result['albums'] == [False]


def test_a_non_ascii_name_folds_on_both_sides(db):
    """SQLite's ``LOWER()`` is ASCII-only, so the old key left ``Björk``
    capitalized on the catalogue side and never matched a searched ``BJÖRK``."""
    aid = _seed_artist(db, 'Björk')
    _seed_album(db, aid, 'Homogenic')
    cfg = _FakeConfigManager({})
    result = library_check.check_library_presence(
        db, _NoServerPlexClient(), cfg, profile_id=1,
        albums=[{'name': 'HOMOGENIC', 'artist': 'BJÖRK'}],
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


def test_track_presence_projects_the_active_servers_mapping(db):
    aid = _seed_artist(db, 'Mapped Artist')
    alb = _seed_album(db, aid, 'Mapped Album')
    legacy_track_id = _seed_track(db, alb, aid, 'Mapped Song')
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM lib2_tracks WHERE legacy_track_id=?", (legacy_track_id,),
        ).fetchone()
        conn.execute(
            "UPDATE lib2_tracks SET server_source='jellyfin',server_id='j-track' "
            "WHERE id=?", (row['id'],),
        )
        conn.execute(
            "INSERT INTO lib2_media_server_mappings "
            "(entity_type,entity_id,server_source,server_id) "
            "VALUES('track',?,'plex','p-track')", (row['id'],),
        )

    result = library_check.check_library_presence(
        db, _FakePlexClient(), _FakeConfigManager({}), profile_id=1,
        albums=[], tracks=[{'name': 'Mapped Song', 'artist': 'Mapped Artist'}],
    )

    assert result['tracks'][0]['track_id'] == 'p-track'


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


def test_a_natively_imported_track_still_carries_an_identity(db):
    """After the legacy cutover all three id arms can be NULL at once.

    A track imported by SoulSync has no media-server mapping, carries
    ``server_source='soulsync'`` (so the active server's CASE yields NULL) and
    has no ``legacy_track_id`` — the player was handed ``''`` as the track id and
    recorded the play against nothing. The lib2 id is always there.
    """
    aid = _seed_artist(db, 'Native Artist')
    alb = _seed_album(db, aid, 'Native Album')
    legacy_track_id = _seed_track(db, alb, aid, 'Native Song')
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE lib2_tracks SET server_source='soulsync',server_id='ss-1',"
            "                       legacy_track_id=NULL "
            "WHERE legacy_track_id=?", (legacy_track_id,))
        lib2_id = conn.execute(
            "SELECT id FROM lib2_tracks WHERE server_id='ss-1'").fetchone()['id']

    result = library_check.check_library_presence(
        db, _FakePlexClient(), _FakeConfigManager({}), profile_id=1,
        albums=[], tracks=[{'name': 'Native Song', 'artist': 'Native Artist'}],
    )

    assert result['tracks'][0]['in_library'] is True
    assert result['tracks'][0]['track_id'] == lib2_id


def test_a_compilation_track_is_found_by_its_own_artist(db):
    """INT-03: ownership was keyed on the ALBUM's primary artist alone. A Muse
    track on a Various Artists compilation was therefore only ever keyed
    (title, "Various Artists"), so a search that correctly named Muse reported
    a file we already own as missing — one click from downloading it twice."""
    from core.library2.importer import normalize_name

    va = _seed_artist(db, 'Various Artists')
    muse = _seed_artist(db, 'Muse')
    album = _seed_album(db, va, 'Now Thats What I Call Music', origin='library')
    track = _seed_track(db, album, va, 'Uprising')
    _lib2(db,
          "UPDATE lib2_tracks SET track_artist='Muse' WHERE legacy_track_id=?",
          (track,))
    _lib2(db,
          "INSERT INTO lib2_track_artists(track_id, artist_id, position) "
          "SELECT t.id, ar.id, 0 FROM lib2_tracks t, lib2_artists ar "
          " WHERE t.legacy_track_id=? AND ar.legacy_artist_id=?",
          (track, muse))
    assert normalize_name('Muse')  # the key both sides fold through

    out = library_check.check_library_presence(
        db, _NoServerPlexClient(), _FakeConfigManager(), 1,
        albums=[],
        tracks=[{'name': 'Uprising', 'artist': 'Muse'}],
    )

    assert out['tracks'][0]['in_library'] is True
    assert out['tracks'][0]['file_path']


def test_the_album_artist_key_still_resolves(db):
    """The album artist stays a valid key — it is a fallback now, not the only
    credit consulted."""
    va = _seed_artist(db, 'Various Artists')
    muse = _seed_artist(db, 'Muse')
    album = _seed_album(db, va, 'Compilation Two', origin='library')
    track = _seed_track(db, album, va, 'Starlight')
    _lib2(db,
          "INSERT INTO lib2_track_artists(track_id, artist_id, position) "
          "SELECT t.id, ar.id, 0 FROM lib2_tracks t, lib2_artists ar "
          " WHERE t.legacy_track_id=? AND ar.legacy_artist_id=?",
          (track, muse))

    out = library_check.check_library_presence(
        db, _NoServerPlexClient(), _FakeConfigManager(), 1,
        albums=[],
        tracks=[{'name': 'Starlight', 'artist': 'Various Artists'},
                {'name': 'Starlight', 'artist': 'Muse'}],
    )

    assert [t['in_library'] for t in out['tracks']] == [True, True]


def test_an_unrelated_artist_is_still_not_a_match(db):
    """Widening the key must not make everything match everything."""
    va = _seed_artist(db, 'Various Artists')
    muse = _seed_artist(db, 'Muse')
    album = _seed_album(db, va, 'Compilation Three', origin='library')
    track = _seed_track(db, album, va, 'Hysteria')
    _lib2(db,
          "INSERT INTO lib2_track_artists(track_id, artist_id, position) "
          "SELECT t.id, ar.id, 0 FROM lib2_tracks t, lib2_artists ar "
          " WHERE t.legacy_track_id=? AND ar.legacy_artist_id=?",
          (track, muse))

    out = library_check.check_library_presence(
        db, _NoServerPlexClient(), _FakeConfigManager(), 1,
        albums=[],
        tracks=[{'name': 'Hysteria', 'artist': 'Def Leppard'}],
    )

    assert out['tracks'][0]['in_library'] is False
