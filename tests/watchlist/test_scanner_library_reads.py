"""What the watchlist scanner asks the library for.

Three reads feed discovery: a handful of albums for pool variety, the artist
genre map behind genre-affinity scoring, and the owned-artist set plus the
seeds' provider ids behind the listening recommendations. They live on their
own so they can be tested against a real Library v2 schema — the failure mode
that matters is a column or join that does not exist, and that answers the
whole feature with a silent "nothing" (§50.4.4.20).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core import watchlist_scanner
from core.library2.importer import normalize_name
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture()
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_library_v2_schema(connection)
    connection.commit()
    yield connection
    connection.close()


def _artist(conn, name, *, genres=None, spotify_id=None, musicbrainz_id=None,
            external_ids=None) -> int:
    return int(conn.execute(
        "INSERT INTO lib2_artists(name, name_key, genres, spotify_id,"
        "                         musicbrainz_id, external_ids)"
        " VALUES(?,?,?,?,?,?)",
        (name, normalize_name(name), json.dumps(genres or []), spotify_id,
         musicbrainz_id, json.dumps(external_ids or {}))).lastrowid)


def _album(conn, artist_id, title, *, origin='library') -> int:
    album_id = int(conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin) VALUES(?,?,?)",
        (artist_id, title, origin)).lastrowid)
    if origin == 'library':
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)", (album_id, title)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)",
                     (track_id, f'/music/{album_id}.flac'))
    return album_id


# ── albums for pool variety ────────────────────────────────────────────────

def test_random_albums_are_albums_the_user_owns(conn):
    """v2 also stores releases it merely knows about (a tracked artist's
    discography). Seeding the discovery pool with one would ask a provider for
    a record the user was never given."""
    owned = _artist(conn, 'Muse')
    _album(conn, owned, 'The Resistance')
    _album(conn, owned, 'Only Listed', origin='discography')

    rows = watchlist_scanner.library_random_albums(conn, limit=5)

    assert [(r['title'], r['artist_name']) for r in rows] == [
        ('The Resistance', 'Muse')]


def test_random_albums_respect_the_limit(conn):
    artist = _artist(conn, 'Muse')
    for n in range(6):
        _album(conn, artist, f'Album {n}')

    assert len(watchlist_scanner.library_random_albums(conn, limit=2)) == 2


def test_library_provenance_without_a_file_is_not_owned(conn):
    artist = _artist(conn, 'Muse')
    album = _album(conn, artist, 'Missing')
    conn.execute("DELETE FROM lib2_track_files WHERE track_id IN "
                 "(SELECT id FROM lib2_tracks WHERE album_id=?)", (album,))
    assert watchlist_scanner.library_random_albums(conn) == []


# ── the genre map behind affinity scoring ──────────────────────────────────

def test_artist_genres_are_keyed_and_folded_for_lookup(conn):
    _artist(conn, 'Muse', genres=['Rock', 'Alternative'])

    assert watchlist_scanner.library_artist_genres(conn) == {
        'muse': {'rock', 'alternative'}}


def test_artist_without_genres_is_not_in_the_map(conn):
    """v2 stores an empty list, not NULL — a membership test on the map is how
    the caller asks "do we know this artist's genres", so an empty entry would
    answer yes."""
    _artist(conn, 'Unenriched')

    assert watchlist_scanner.library_artist_genres(conn) == {}


def test_a_broken_genre_value_does_not_take_the_map_down(conn):
    conn.execute("INSERT INTO lib2_artists(name, name_key, genres)"
                 " VALUES('Broken','broken','not json')")
    _artist(conn, 'Muse', genres=['Rock'])

    assert watchlist_scanner.library_artist_genres(conn) == {'muse': {'rock'}}


# ── owned artists + the seeds' provider ids ────────────────────────────────

def test_seed_ids_come_from_columns_and_from_external_ids(conn):
    """`similar_artists.source_artist_id` is whichever PROVIDER id the scan ran
    with. v2 promotes only Spotify and MusicBrainz to columns, so a seed whose
    graph was built on its Deezer id is only reachable through
    ``external_ids``."""
    _artist(conn, 'Muse', spotify_id='SP1', musicbrainz_id='MB1',
            external_ids={'deezer': 'DZ1'})
    _artist(conn, 'Not A Seed', spotify_id='SP2')

    owned, seed_ids, id_to_name = watchlist_scanner.library_owned_and_seed_ids(
        conn, {'muse'})

    assert owned == {'muse', 'not a seed'}
    assert set(seed_ids) == {'SP1', 'MB1', 'DZ1'}
    assert id_to_name == {'SP1': 'Muse', 'MB1': 'Muse', 'DZ1': 'Muse'}


def test_owned_includes_every_artist_the_library_holds(conn):
    """Owned is the exclusion set for recommendations. A v2 artist row exists
    because the user has the artist, downloaded them, or asked to follow them —
    all three are reasons not to recommend them back."""
    _artist(conn, 'Muse')
    _artist(conn, 'Björk')

    owned, _seed_ids, _names = watchlist_scanner.library_owned_and_seed_ids(
        conn, set())

    assert owned == {'muse', 'björk'}


def test_an_unnamed_row_is_not_an_owned_artist(conn):
    conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES('','')")

    owned, _seed_ids, _names = watchlist_scanner.library_owned_and_seed_ids(
        conn, set())

    assert owned == set()
