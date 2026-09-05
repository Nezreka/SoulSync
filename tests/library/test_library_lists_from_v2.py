"""The library-wide lists the export, ownership and taste paths ask for.

Four readers that each hand out "everything the library has" in one shape:
paths for M3U export, owned (artist, album) pairs, owned Spotify album ids, and
the genres of named artists. All four are now v2 reads, and all four had the
same two hazards: v2 also stores releases nobody owns, and SQLite's ``LOWER()``
only folds ASCII (§50.4.4.21).
"""

from __future__ import annotations

import json

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path) -> MusicDatabase:
    return MusicDatabase(database_path=str(tmp_path / "lists.db"))


def _artist(db, name, *, genres=None) -> int:
    from core.library2.importer import normalize_name
    conn = db._get_connection()
    try:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, name_key, genres) VALUES(?,?,?)",
            (name, normalize_name(name), json.dumps(genres or []))).lastrowid
        conn.commit()
        return int(artist_id)
    finally:
        conn.close()


def _album(db, artist_id, title, *, origin='library', spotify_id=None) -> int:
    conn = db._get_connection()
    try:
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin, spotify_id)"
            " VALUES(?,?,?,?)", (artist_id, title, origin, spotify_id)).lastrowid
        conn.commit()
        return int(album_id)
    finally:
        conn.close()


def _track(db, album_id, title, *, path=None, duration=None, track_number=None,
           file_state='active') -> int:
    conn = db._get_connection()
    try:
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, duration, track_number)"
            " VALUES(?,?,?,?)", (album_id, title, duration, track_number)).lastrowid
        if path is not None:
            conn.execute(
                "INSERT INTO lib2_track_files(track_id, path, is_primary, file_state)"
                " VALUES(?,?,1,?)", (track_id, path, file_state))
        conn.commit()
        return int(track_id)
    finally:
        conn.close()


# ── export ─────────────────────────────────────────────────────────────────

def test_export_lists_only_tracks_that_have_a_file(db):
    artist = _artist(db, 'Muse')
    album = _album(db, artist, 'The Resistance')
    _track(db, album, 'Uprising', path='/m/uprising.flac', duration=305000,
           track_number=1)
    _track(db, album, 'Never Got It', track_number=2)

    assert db.get_all_library_tracks_for_export() == [
        {'path': '/m/uprising.flac', 'title': 'Uprising', 'artist': 'Muse',
         'duration': 305}]


def test_export_skips_a_file_that_is_only_history(db):
    artist = _artist(db, 'Muse')
    album = _album(db, artist, 'The Resistance')
    _track(db, album, 'Gone', path='/m/gone.flac', file_state='deleted')

    assert db.get_all_library_tracks_for_export() == []


def test_export_is_ordered_by_artist_album_track(db):
    b_artist = _artist(db, 'Beck')
    m_artist = _artist(db, 'Muse')
    odelay = _album(db, b_artist, 'Odelay')
    absolution = _album(db, m_artist, 'Absolution')
    _track(db, absolution, 'Second', path='/m/2.flac', track_number=2)
    _track(db, absolution, 'First', path='/m/1.flac', track_number=1)
    _track(db, odelay, 'Devils Haircut', path='/b/1.flac', track_number=1)

    assert [t['title'] for t in db.get_all_library_tracks_for_export()] == [
        'Devils Haircut', 'First', 'Second']


# ── ownership pairs ────────────────────────────────────────────────────────

def test_album_names_are_the_owned_pairs(db):
    artist = _artist(db, 'Muse')
    owned = _album(db, artist, 'The Resistance')
    _track(db, owned, 'Uprising', path='/m/uprising.flac')
    _album(db, artist, 'Listed Only', origin='discography')

    assert db.get_library_album_names() == {('muse', 'the resistance')}


def test_album_names_fold_beyond_ascii(db):
    """The caller compares these pairs against provider names it lowercased in
    Python. SQLite's LOWER() stops at ASCII, so a stored 'Björk' folded to
    'Björk' and never matched the 'björk' on the other side."""
    artist = _artist(db, 'BJÖRK')
    owned = _album(db, artist, 'HOMOGENIC')
    _track(db, owned, 'Jóga', path='/m/joga.flac')

    assert db.get_library_album_names() == {('björk', 'homogenic')}


def test_spotify_album_ids_are_the_owned_ones(db):
    artist = _artist(db, 'Muse')
    owned = _album(db, artist, 'The Resistance', spotify_id='SP-OWNED')
    _track(db, owned, 'Uprising', path='/m/uprising.flac')
    _album(db, artist, 'Listed Only', origin='discography', spotify_id='SP-LISTED')

    assert db.get_library_spotify_album_ids() == {'SP-OWNED'}


# ── genres by name ─────────────────────────────────────────────────────────

def test_genres_by_name_returns_the_stored_list(db):
    _artist(db, 'Muse', genres=['Rock', 'Alternative'])

    assert db.get_artist_genres_by_name(['muse']) == {'muse': ['Rock', 'Alternative']}


def test_genres_by_name_matches_beyond_ascii(db):
    _artist(db, 'Björk', genres=['Art Pop'])

    assert db.get_artist_genres_by_name(['BJÖRK']) == {'björk': ['Art Pop']}


def test_genres_by_name_skips_an_artist_without_genres(db):
    _artist(db, 'Unenriched')

    assert db.get_artist_genres_by_name(['unenriched', 'never heard of them']) == {}
