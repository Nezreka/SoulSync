"""the play-now bridge: mix tracklists resolved against owned tracks."""

import pytest

from core.discovery.playable import resolve_playable_tracks
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'm.db'))
    conn = d._get_connection()
    cur = conn.cursor()
    # The native catalogue: a recording plus the FILE row that makes it owned.
    cur.execute("INSERT INTO lib2_artists (id, name, name_key) VALUES (1, 'Daft Punk', 'daft punk')")
    cur.execute("INSERT INTO lib2_artists (id, name, name_key) VALUES (2, 'Justice', 'justice')")
    cur.execute("INSERT INTO lib2_albums (id, title, primary_artist_id) VALUES (10, 'Discovery', 1)")
    cur.execute("INSERT INTO lib2_albums (id, title, primary_artist_id) VALUES (11, 'Cross', 2)")

    def _track(track_id, title, album_id, path):
        cur.execute("INSERT INTO lib2_tracks (id, title, album_id) VALUES (?, ?, ?)",
                    (track_id, title, album_id))
        if path:
            cur.execute(
                "INSERT INTO lib2_track_files (track_id, path, is_primary, file_state) "
                "VALUES (?, ?, 1, 'active')", (track_id, path))

    _track(100, 'One More Time', 10, '/m/omt.flac')
    _track(101, 'Aerodynamic', 10, '/m/aero.flac')
    # same title, DIFFERENT artist - must never match on title alone
    _track(102, 'One More Time', 11, '/m/justice-omt.flac')
    # catalogued recording with no file row - unplayable, must not match
    _track(103, 'Digital Love', 10, None)
    conn.commit()
    conn.close()
    return d


def test_resolves_owned_tracks_in_mix_order(db):
    result = resolve_playable_tracks(db, [
        {'artist': 'Daft Punk', 'title': 'Aerodynamic'},
        {'artist': 'daft punk', 'title': 'one more time'},   # case-insensitive
        {'artist': 'Daft Punk', 'title': 'Not Owned Song'},
    ])
    assert result['total'] == 3
    assert result['matched'] == 2
    assert [t['title'] for t in result['tracks']] == ['Aerodynamic', 'One More Time']
    assert [t['title'] for t in result['queue_tracks']] == [
        'Aerodynamic', 'One More Time', 'Not Owned Song'
    ]
    assert result['queue_tracks'][-1]['playback_status'] == 'missing'
    assert all(t['file_path'] for t in result['tracks'])
    assert result['tracks'][0]['artist'] == 'Daft Punk'
    assert result['tracks'][0]['album'] == 'Discovery'


def test_artist_disambiguates_shared_titles(db):
    result = resolve_playable_tracks(db, [{'artist': 'Justice', 'title': 'One More Time'}])
    assert result['matched'] == 1
    assert result['tracks'][0]['file_path'] == '/m/justice-omt.flac'


def test_pathless_rows_never_resolve(db):
    result = resolve_playable_tracks(db, [{'artist': 'Daft Punk', 'title': 'Digital Love'}])
    assert result['matched'] == 0


def test_repeated_tracks_resolve_once(db):
    result = resolve_playable_tracks(db, [
        {'artist': 'Daft Punk', 'title': 'One More Time'},
        {'artist': 'Daft Punk', 'title': 'One More Time'},
    ])
    assert result['matched'] == 1


def test_empty_and_nameless_input(db):
    assert resolve_playable_tracks(db, []) == {
        'tracks': [], 'queue_tracks': [], 'matched': 0, 'total': 0
    }
    result = resolve_playable_tracks(db, [{'artist': '', 'title': 'X'}, {'title': ''}])
    assert result['matched'] == 0

def test_mix_resolution_scans_candidate_titles_once(db):
    class TracedDB:
        def _get_connection(self):
            conn = db._get_connection()
            conn.set_trace_callback(statements.append)
            return conn

    statements = []
    result = resolve_playable_tracks(TracedDB(), [
        {'artist': 'Daft Punk', 'title': 'One More Time'},
        {'artist': 'Justice', 'title': 'One More Time'},
        {'artist': 'Daft Punk', 'title': 'Aerodynamic'},
        {'artist': 'Daft Punk', 'title': 'Missing Song'},
    ])
    # `FROM lib2_tracks t`, not upstream's `FROM tracks t`: the catalogue on
    # this branch is lib2, and the property under test — one scan for the whole
    # mix instead of one per entry — is the same either way.
    selects = [sql for sql in statements if 'FROM lib2_tracks t' in sql]
    assert len(selects) == 1
    assert result['matched'] == 3
    assert [track['file_path'] for track in result['tracks']] == [
        '/m/omt.flac', '/m/justice-omt.flac', '/m/aero.flac'
    ]
