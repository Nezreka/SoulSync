"""The media-server scan writing into the catalogue.

The scan is the one path on which rows come into existence. What has to hold:
it finds the row it already wrote (even after the server re-keyed it), it never
clears what a provider enriched, and a file is a row of its own.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.media_server_sync import (
    resolve_album, resolve_artist, upsert_album, upsert_artist, upsert_track,
)
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.commit()
    yield conn.cursor()
    conn.close()


# ── artists ────────────────────────────────────────────────────────────────

def test_the_same_artist_twice_is_one_row(cur):
    first = upsert_artist(cur, server_source='plex', server_id='7', name='Muse')
    again = upsert_artist(cur, server_source='plex', server_id='7', name='Muse')

    assert first == again
    assert cur.execute("SELECT COUNT(*) FROM lib2_artists").fetchone()[0] == 1


def test_a_rekeyed_artist_keeps_its_row(cur):
    """A rescan hands out new rating keys. The legacy scan answered that by
    building a new row and copying enrichment across; the catalogue row simply
    takes the new stamp."""
    artist_id = upsert_artist(cur, server_source='plex', server_id='7', name='Muse')
    cur.execute("UPDATE lib2_artists SET spotify_id='SP1' WHERE id=?", (artist_id,))

    again = upsert_artist(cur, server_source='plex', server_id='999', name='Muse')

    assert again == artist_id
    row = cur.execute("SELECT server_id, spotify_id FROM lib2_artists").fetchone()
    assert (row['server_id'], row['spotify_id']) == ('999', 'SP1')


def test_a_scan_never_clears_enrichment(cur):
    """The server knows the name. It does not know the artwork a provider
    resolved, and a scan that sent none must not take it away."""
    artist_id = upsert_artist(cur, server_source='plex', server_id='7', name='Muse',
                              image_url='provider.jpg', genres_json='["Rock"]')

    upsert_artist(cur, server_source='plex', server_id='7', name='Muse',
                  image_url=None, genres_json=None)

    row = cur.execute("SELECT image_url, genres FROM lib2_artists WHERE id=?",
                      (artist_id,)).fetchone()
    assert (row['image_url'], row['genres']) == ('provider.jpg', '["Rock"]')


def test_an_artist_that_arrived_by_download_is_adopted(cur):
    """A download or an import may have created the artist first. The scan takes
    that row over instead of forking a second one under the same name."""
    cur.execute("INSERT INTO lib2_artists(name, name_key) VALUES('Muse','muse')")
    existing = cur.lastrowid

    adopted = upsert_artist(cur, server_source='plex', server_id='7', name='Muse')

    assert adopted == existing
    assert cur.execute("SELECT COUNT(*) FROM lib2_artists").fetchone()[0] == 1


# ── albums ─────────────────────────────────────────────────────────────────

def test_an_album_from_the_server_is_owned(cur):
    """`origin='library'` is the whole point: the server has the files."""
    artist_id = upsert_artist(cur, server_source='plex', server_id='7', name='Muse')

    album_id = upsert_album(cur, server_source='plex', server_id='70',
                            artist_id=artist_id, title='Absolution')

    row = cur.execute("SELECT origin, primary_artist_id FROM lib2_albums WHERE id=?",
                      (album_id,)).fetchone()
    assert (row['origin'], row['primary_artist_id']) == ('library', artist_id)
    assert cur.execute(
        "SELECT COUNT(*) FROM lib2_album_artists WHERE album_id=?",
        (album_id,)).fetchone()[0] == 1


def test_an_album_the_catalogue_already_listed_becomes_owned(cur):
    """v2 keeps a followed artist's discography. When the files show up, that
    row is the one to fill in — a second row would list the album twice, once
    as wanted and once as owned."""
    artist_id = upsert_artist(cur, server_source='plex', server_id='7', name='Muse')
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin) "
        "VALUES(?, 'Absolution', 'discography')", (artist_id,))
    listed = cur.lastrowid

    album_id = upsert_album(cur, server_source='plex', server_id='70',
                            artist_id=artist_id, title='absolution')

    assert album_id == listed
    assert cur.execute("SELECT origin FROM lib2_albums WHERE id=?",
                       (album_id,)).fetchone()['origin'] == 'library'


# ── tracks and their files ─────────────────────────────────────────────────

def test_a_track_puts_its_path_on_a_file_row(cur):
    artist_id = upsert_artist(cur, server_source='plex', server_id='7', name='Muse')
    album_id = upsert_album(cur, server_source='plex', server_id='70',
                            artist_id=artist_id, title='Absolution')

    track_id = upsert_track(cur, server_source='plex', server_id='700',
                            album_id=album_id, artist_id=artist_id,
                            title='Time Is Running Out', track_number=4,
                            file_path='/m/04 - time.flac', file_size=4096,
                            bitrate=1411)

    row = cur.execute(
        "SELECT path, size, bitrate, format, is_primary FROM lib2_track_files "
        " WHERE track_id=?", (track_id,)).fetchone()
    assert row['path'] == '/m/04 - time.flac'
    assert (row['size'], row['bitrate'], row['format'], row['is_primary']) == (
        4096, 1411, 'flac', 1)
    assert cur.execute(
        "SELECT COUNT(*) FROM lib2_track_artists WHERE track_id=?",
        (track_id,)).fetchone()[0] == 1


def test_a_rescan_without_a_size_keeps_the_one_it_had(cur):
    artist_id = upsert_artist(cur, server_source='plex', server_id='7', name='Muse')
    album_id = upsert_album(cur, server_source='plex', server_id='70',
                            artist_id=artist_id, title='Absolution')
    common = dict(server_source='plex', server_id='700', album_id=album_id,
                  artist_id=artist_id, title='Time', file_path='/m/t.flac')
    upsert_track(cur, **common, file_size=4096, bitrate=1411)

    upsert_track(cur, **common, file_size=None, bitrate=None)

    row = cur.execute("SELECT size, bitrate FROM lib2_track_files").fetchone()
    assert (row['size'], row['bitrate']) == (4096, 1411)


def test_a_moved_file_leaves_exactly_one_primary(cur):
    artist_id = upsert_artist(cur, server_source='plex', server_id='7', name='Muse')
    album_id = upsert_album(cur, server_source='plex', server_id='70',
                            artist_id=artist_id, title='Absolution')
    common = dict(server_source='plex', server_id='700', album_id=album_id,
                  artist_id=artist_id, title='Time')
    track_id = upsert_track(cur, **common, file_path='/old/t.flac')

    upsert_track(cur, **common, file_path='/new/t.flac')

    primaries = cur.execute(
        "SELECT path FROM lib2_track_files WHERE track_id=? AND is_primary=1",
        (track_id,)).fetchall()
    assert [r['path'] for r in primaries] == ['/new/t.flac']


# ── resolving the server's ids ─────────────────────────────────────────────

def test_ids_are_scoped_to_their_server(cur):
    """Two servers hand out the same small numbers."""
    upsert_artist(cur, server_source='plex', server_id='7', name='Muse')

    assert resolve_artist(cur, 'jellyfin', '7') is None
    assert resolve_artist(cur, 'plex', '7') is not None
    assert resolve_album(cur, 'plex', 'nope') is None
