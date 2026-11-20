"""#2214: an m3u export marked every "A & B" track missing.

the library was indexed by album artist only and the playlist credit looked up
whole, so "Artist A & Artist B" never equalled "Artist A" and the track was
missing before any title got compared. a track is now filed under every artist
it's credited to, and a playlist credit is split the importer's way.
"""

from __future__ import annotations

import json
import types

import pytest

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


def _credit(conn, track_id, artist_id, position=1, role='primary'):
    conn.execute("INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position)"
                 " VALUES(?,?,?,?)", (track_id, artist_id, role, position))


@pytest.fixture
def lib(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as c:
        a = seed_artist(c, server_id='a', name='Artist A')
        b = seed_artist(c, server_id='b', name='Artist B')
        cc = seed_artist(c, server_id='c', name='Artist C')
        d = seed_artist(c, server_id='d', name='Artist D')
        e = seed_artist(c, server_id='e', name='Artist E')
        va = seed_artist(c, server_id='va', name='Various Artists')
        ab = seed_artist(c, server_id='ab', name='Above & Beyond')
        beyond = seed_artist(c, server_id='by', name='Beyond')

        # the reporter's track: on A's album, credited A & B
        duets = seed_album(c, server_id='al-duets', title='Duets', artist_id=a)
        t = seed_track(c, server_id='t1', title='Together', album_id=duets, artist_id=a,
                       track_artist='Artist A & Artist B', file_path='/m/a/together.flac')
        _credit(c, t, b)
        # credit text only, no per-artist rows past the album artist
        seed_track(c, server_id='t2', title='Text Only', album_id=duets, artist_id=a,
                   track_artist='Artist A & Artist B', file_path='/m/a/text_only.flac')
        # a feature on C's album
        c_album = seed_album(c, server_id='al-c', title='C Record', artist_id=cc)
        t = seed_track(c, server_id='t3', title='Guest Spot', album_id=c_album, artist_id=cc,
                       track_artist='Artist C feat. Artist A', file_path='/m/c/guest.flac')
        _credit(c, t, a, role='featured')
        # a compilation
        comp = seed_album(c, server_id='al-va', title='Now 99', artist_id=va)
        t = seed_track(c, server_id='t4', title='Comp Cut', album_id=comp,
                       track_artist='Artist D', file_path='/m/va/comp.flac')
        _credit(c, t, d, position=0)
        # a band with & in its name, and a different artist with the same title
        ab_album = seed_album(c, server_id='al-ab', title='Group Therapy', artist_id=ab)
        seed_track(c, server_id='t5', title='Sun & Moon', album_id=ab_album, artist_id=ab,
                   file_path='/m/ab/sun.flac')
        by_album = seed_album(c, server_id='al-by', title='Other', artist_id=beyond)
        seed_track(c, server_id='t6', title='Sun & Moon', album_id=by_album, artist_id=beyond,
                   file_path='/m/beyond/sun.flac')
        # single artist
        e_album = seed_album(c, server_id='al-e', title='Solo', artist_id=e)
        seed_track(c, server_id='t7', title='Plain', album_id=e_album, artist_id=e,
                   file_path='/m/e/plain.flac')
        c.commit()
    return db


def _resolve(db, tracks):
    from core.library.m3u_resolve import resolve_m3u_paths
    return resolve_m3u_paths(db.get_tracks_for_m3u_resolution(), tracks)


def _one(db, name, artist):
    return _resolve(db, [{'name': name, 'artist': artist}])[0]


def test_rows_carry_every_credit(lib):
    row = next(r for r in lib.get_tracks_for_m3u_resolution() if r['title'] == 'Together')
    assert row['artist'] == 'Artist A'
    assert set(row['credits']) == {'Artist A', 'Artist B'}
    assert row['track_artist'] == 'Artist A & Artist B'


def test_reporters_case_a_and_b(lib):
    assert _one(lib, 'Together', 'Artist A & Artist B') == '/m/a/together.flac'
    assert _one(lib, 'Together', 'Artist A, Artist B') == '/m/a/together.flac'


def test_reverse_case_playlist_names_only_the_second_artist(lib):
    assert _one(lib, 'Together', 'Artist B') == '/m/a/together.flac'
    # no credit row for B, only the credit text
    assert _one(lib, 'Text Only', 'Artist B') == '/m/a/text_only.flac'


def test_featured_artist(lib):
    assert _one(lib, 'Guest Spot', 'Artist A') == '/m/c/guest.flac'
    assert _one(lib, 'Guest Spot', 'Artist A feat. Artist C') == '/m/c/guest.flac'


def test_compilation_track_artist(lib):
    assert _one(lib, 'Comp Cut', 'Artist D') == '/m/va/comp.flac'


def test_band_name_with_ampersand_stays_whole(lib):
    assert _one(lib, 'Sun & Moon', 'Above & Beyond') == '/m/ab/sun.flac'
    # the whole credit misses, its pieces are asked: "Above & Beyond" is one
    # artist the library knows, so "Beyond"'s song must not be picked
    assert _one(lib, 'Sun & Moon', 'Above & Beyond, Richard Bedford') == '/m/ab/sun.flac'


def test_single_artist_unchanged_and_other_artists_titles_stay_missing(lib):
    assert _one(lib, 'Plain', 'Artist E') == '/m/e/plain.flac'
    assert _one(lib, 'Together', 'Artist E') is None
    assert _one(lib, 'Nope', 'Artist A & Artist B') is None
    assert _one(lib, 'Together', '') is None


# ---- the two writers in web_server ----

class _Cfg:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_active_media_server(self):
        return None


@pytest.fixture
def ws(monkeypatch, tmp_path, lib):
    import web_server

    playlists = tmp_path / 'Playlists'
    cfg = _Cfg({'m3u_export.enabled': True, 'soulseek.transfer_path': str(tmp_path)})
    monkeypatch.setattr(web_server, 'config_manager', cfg)
    monkeypatch.setattr(web_server, 'docker_resolve_path', lambda p: p)
    monkeypatch.setattr(web_server, '_compute_m3u_folder', lambda *a, **k: str(playlists))
    real_db = web_server.get_database()

    class _Db:
        # the shared db for the profile gate, the tmp library for tracks
        def __getattr__(self, name):
            return getattr(real_db, name)

        def get_tracks_for_m3u_resolution(self, server_source=None):
            return lib.get_tracks_for_m3u_resolution(server_source)

    monkeypatch.setattr(web_server, 'get_database', lambda: _Db())
    return types.SimpleNamespace(mod=web_server, playlists=playlists)


def _entries(text):
    return [ln for ln in text.splitlines() if ln and not ln.startswith('#')]


def test_export_route_finds_multi_artist_tracks(ws):
    client = ws.mod.app.test_client()
    with client.session_transaction() as sess:
        sess.setdefault('profile_id', 1)
    body = client.post('/api/generate-playlist-m3u', json={
        'playlist_name': 'Mix',
        'tracks': [{'name': 'Together', 'artist': 'Artist A & Artist B', 'duration_ms': 1000},
                   {'name': 'Plain', 'artist': 'Artist E', 'duration_ms': 1000}],
    }).get_json()
    assert body.get('success'), body
    assert body['stats'] == {'found': 2, 'downloaded': 0, 'missing': 0}
    assert '#STATUS:MISSING' not in body['m3u_content']
    assert _entries(body['m3u_content']) == ['/m/a/together.flac', '/m/e/plain.flac']


def test_batch_complete_m3u_finds_multi_artist_tracks(ws, monkeypatch):
    monkeypatch.setattr('core.playlists.batch_m3u.playlist_m3u_tracks', lambda b, t, db: t)
    ws.mod._regenerate_batch_m3u(
        {'playlist_name': 'Mix'},
        [{'name': 'Together', 'artist': 'Artist A & Artist B', 'duration_ms': 1000}])
    saved = open(ws.playlists / 'Mix.m3u', encoding='utf-8').read()
    assert '/m/a/together.flac' in saved
    assert '# MISSING' not in saved


# ---- the mirror's "in library" check (#1910) ----

def test_mirror_check_asks_by_each_credited_artist(lib):
    from core.playlists.library_check import _credit_artists
    assert _credit_artists(lib, 'Artist A & Artist B') == ['Artist A & Artist B', 'Artist A', 'Artist B']
    assert _credit_artists(lib, 'Above & Beyond') == ['Above & Beyond']
    assert _credit_artists(lib, 'Artist E') == ['Artist E']


def test_mirror_check_finds_a_multi_artist_track(lib, monkeypatch):
    from core.playlists import library_check
    from core.playlists.track_status import IN_LIBRARY, track_status

    pk = lib.mirror_playlist(
        source='deezer', source_playlist_id='p', name='P',
        tracks=[{'track_name': 'Together', 'artist_name': 'Artist A & Artist B',
                 'source_track_id': 'dz-1'}], profile_id=1)
    monkeypatch.setattr('core.settings.config_manager.get_active_media_server', lambda: None)
    library_check.check_mirror_tracks(lib, lib.get_mirrored_playlist_tracks(pk),
                                      profile_id=1, source='deezer')
    row = lib.get_mirrored_playlist_tracks(pk)[0]
    assert track_status(row, set()) == IN_LIBRARY, json.loads(row['extra_data'] or '{}')
