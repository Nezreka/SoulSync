"""the play-now bridge: mix tracklists resolved against owned tracks."""

import pytest

from core.discovery.playable import resolve_playable_tracks
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'm.db'))
    conn = d._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES (1, 'Daft Punk')")
    cur.execute("INSERT INTO artists (id, name) VALUES (2, 'Justice')")
    cur.execute("INSERT INTO albums (id, title, artist_id) VALUES (10, 'Discovery', 1)")
    cur.execute("INSERT INTO albums (id, title, artist_id) VALUES (11, 'Cross', 2)")
    cur.execute(
        "INSERT INTO tracks (id, title, artist_id, album_id, file_path) "
        "VALUES (100, 'One More Time', 1, 10, '/m/omt.flac')")
    cur.execute(
        "INSERT INTO tracks (id, title, artist_id, album_id, file_path) "
        "VALUES (101, 'Aerodynamic', 1, 10, '/m/aero.flac')")
    # same title, DIFFERENT artist - must never match on title alone
    cur.execute(
        "INSERT INTO tracks (id, title, artist_id, album_id, file_path) "
        "VALUES (102, 'One More Time', 2, 11, '/m/justice-omt.flac')")
    # owned row with no file on disk recorded - unplayable, must not match
    cur.execute(
        "INSERT INTO tracks (id, title, artist_id, album_id, file_path) "
        "VALUES (103, 'Digital Love', 1, 10, '')")
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
    assert resolve_playable_tracks(db, []) == {'tracks': [], 'matched': 0, 'total': 0}
    result = resolve_playable_tracks(db, [{'artist': '', 'title': 'X'}, {'title': ''}])
    assert result['matched'] == 0
