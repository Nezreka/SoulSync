"""DELETE /api/library/artist/<id> — clear an artist out of the library.

Drives the REAL endpoint. An earlier draft of this file reimplemented the
endpoint's body and asserted against the copy, which would have gone on passing
while the route drifted underneath it.

The endpoint deliberately has no `delete_files` switch, unlike the album and
track deletes: this can span a whole discography, and "clear this artist out of
my library" is a different intent from "erase these recordings". That the files
survive is pinned here, because it is the entire premise the confirm dialog
sells to the user.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-delart-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'd.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def library(tmp_path):
    """Two artists with albums and tracks, plus real files on disk."""
    files = []
    db = web_server.get_database()
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        for table in ('tracks', 'albums', 'artists'):
            cur.execute(f"DELETE FROM {table}")

        for aid, name, source in (('a1', 'Don Felder', 'soulsync'),
                                  ('a2', 'Hans Zimmer', 'soulsync'),
                                  ('a3', 'Plex Artist', 'plex'),
                                  ('a4', 'Keep Me', 'soulsync')):
            cur.execute("INSERT INTO artists (id, name, server_source) VALUES (?,?,?)",
                        (aid, name, source))
        for alid, aid, title in (('al1', 'a1', 'Airborne'),
                                 ('al2', 'a1', 'Heavy Metal OST'),
                                 ('al3', 'a2', 'Interstellar')):
            cur.execute("INSERT INTO albums (id, artist_id, title, server_source) "
                        "VALUES (?,?,?, 'soulsync')", (alid, aid, title))
        # 't5' is the one that matters: it sits on a1's album but is credited
        # to a2, so ONLY the album-based delete can reach it. Without it every
        # track is also caught by the artist_id sweep and the orphan check
        # passes even when the album sweep is removed entirely.
        for tid, alid, aid, title in (('t1', 'al1', 'a1', 'Bad Girls'),
                                      ('t2', 'al1', 'a1', 'Winners'),
                                      ('t3', 'al2', 'a1', 'Takin a Ride'),
                                      ('t5', 'al2', 'a2', 'Guest Spot'),
                                      ('t4', 'al3', 'a2', 'Cornfield Chase')):
            path = tmp_path / f'{title}.flac'
            path.write_bytes(b'fLaC-audio')
            files.append(path)
            cur.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, file_path, server_source) "
                "VALUES (?,?,?,?,?, 'soulsync')", (tid, alid, aid, title, str(path)))
        conn.commit()
    finally:
        conn.close()
    return files


def _rows(sql, params=()):
    conn = web_server.get_database()._get_connection()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def test_the_artist_its_albums_and_its_tracks_all_go(client, library):
    response = client.delete('/api/library/artist/a1')

    assert response.status_code == 200
    body = response.get_json()
    assert body['success'] is True
    assert body['artist_name'] == 'Don Felder'
    assert body['albums_deleted'] == 2
    assert body['tracks_deleted'] == 4

    assert _rows("SELECT COUNT(*) FROM artists WHERE id='a1'") == 0
    assert _rows("SELECT COUNT(*) FROM albums WHERE artist_id='a1'") == 0
    assert _rows("SELECT COUNT(*) FROM tracks WHERE artist_id='a1'") == 0
    # the guest track went with the album it lived on
    assert _rows("SELECT COUNT(*) FROM tracks WHERE id='t5'") == 0


def test_not_one_file_is_touched(client, library):
    """The promise the confirm dialog makes."""
    client.delete('/api/library/artist/a1')

    for path in library:
        assert path.exists(), f'{path.name} was deleted from disk'
        assert path.read_bytes() == b'fLaC-audio'


def test_other_artists_are_left_completely_alone(client, library):
    client.delete('/api/library/artist/a1')

    assert _rows("SELECT COUNT(*) FROM artists WHERE id='a2'") == 1
    assert _rows("SELECT COUNT(*) FROM albums WHERE artist_id='a2'") == 1
    assert _rows("SELECT COUNT(*) FROM tracks WHERE album_id='al3'") == 1
    # an artist with no albums at all must survive someone else's delete
    assert _rows("SELECT COUNT(*) FROM artists WHERE id='a4'") == 1


def test_no_track_outlives_the_album_it_sat_on(client, library):
    """A track left behind is unreachable — nothing can navigate to it."""
    client.delete('/api/library/artist/a1')

    assert _rows(
        "SELECT COUNT(*) FROM tracks WHERE album_id NOT IN (SELECT id FROM albums)"
    ) == 0


def test_a_guests_track_on_the_artists_album_goes_but_the_guest_stays(client, library):
    """A compilation carries other artists' tracks. Those tracks live on the
    album being removed, so they go — the guest ARTIST does not."""
    conn = web_server.get_database()._get_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO artists (id, name, server_source) VALUES ('va','Various Artists','soulsync')")
        cur.execute("INSERT INTO artists (id, name, server_source) VALUES ('gg','Guest','soulsync')")
        cur.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES ('c1','va','Shrek 2','soulsync')")
        cur.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source) "
                    "VALUES ('gt','c1','gg','Accidentally in Love','soulsync')")
        conn.commit()
    finally:
        conn.close()

    body = client.delete('/api/library/artist/va').get_json()

    assert body['tracks_deleted'] == 1
    assert _rows("SELECT COUNT(*) FROM tracks WHERE id='gt'") == 0
    assert _rows("SELECT COUNT(*) FROM artists WHERE id='gg'") == 1


def test_a_media_server_artist_is_flagged_as_coming_back(client, library):
    """It reappears on the next scan; the toast says so rather than letting it
    look like the delete failed."""
    assert client.delete('/api/library/artist/a3').get_json()['returns_on_rescan'] is True
    assert client.delete('/api/library/artist/a2').get_json()['returns_on_rescan'] is False


def test_an_unknown_artist_is_a_404_not_a_silent_success(client, library):
    response = client.delete('/api/library/artist/nope')

    assert response.status_code == 404
    assert response.get_json()['success'] is False
