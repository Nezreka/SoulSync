from __future__ import annotations

import sqlite3

from database.music_database import MusicDatabase


class _InMemoryDB(MusicDatabase):
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def _get_connection(self):
        return _NonClosingConn(self._conn)


class _NonClosingConn:
    def __init__(self, real):
        self._real = real

    def cursor(self):
        return self._real.cursor()

    def commit(self):
        return self._real.commit()

    def close(self):
        pass


class _Album:
    ratingKey = "album-1"
    title = "Flower Boy"
    year = 2017
    leafCount = 15
    duration = 2940
    genres = []
    thumb = None


def _seed(db):
    """A catalogue that already holds the artist and the album, both stamped
    with the server's own ids — what the scan's earlier passes leave behind."""
    from core.library2.schema import ensure_library_v2_schema

    ensure_library_v2_schema(db._conn)
    cur = db._conn.cursor()
    artist = cur.execute(
        "INSERT INTO lib2_artists (name, name_key, server_source, server_id)"
        " VALUES ('Tyler', 'tyler', 'navidrome', 'artist-1')").lastrowid
    album = cur.execute(
        "INSERT INTO lib2_albums (primary_artist_id, title, year, image_url, origin,"
        "                         server_source, server_id)"
        " VALUES (?, 'Flower Boy', 2017, '/rest/getCoverArt?id=correct-cover',"
        "         'library', 'navidrome', 'album-1')", (artist,)).lastrowid
    track = cur.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Track')",
                        (album,)).lastrowid
    cur.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?, '/music/track.flac')",
                (track,))
    db._conn.commit()


def test_album_refresh_preserves_existing_thumb_when_incoming_thumb_missing():
    db = _InMemoryDB()
    _seed(db)

    assert db.insert_or_update_media_album(_Album(), "artist-1", server_source="navidrome") is True

    row = db._conn.execute(
        "SELECT image_url FROM lib2_albums WHERE server_id = 'album-1'").fetchone()
    assert row["image_url"] == "/rest/getCoverArt?id=correct-cover"


def test_album_refresh_does_not_replace_catalogue_thumb():
    db = _InMemoryDB()
    _seed(db)

    album = _Album()
    album.thumb = "/rest/getCoverArt?id=new-cover"

    assert db.insert_or_update_media_album(album, "artist-1", server_source="navidrome") is True

    row = db._conn.execute(
        "SELECT image_url FROM lib2_albums WHERE server_id = 'album-1'").fetchone()
    assert row["image_url"] == "/rest/getCoverArt?id=correct-cover"
