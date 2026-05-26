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
    cur = db._conn.cursor()
    cur.execute("""
        CREATE TABLE albums (
            id TEXT PRIMARY KEY,
            artist_id TEXT,
            title TEXT,
            year INTEGER,
            thumb_url TEXT,
            genres TEXT,
            track_count INTEGER,
            duration INTEGER,
            server_source TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        INSERT INTO albums
            (id, artist_id, title, year, thumb_url, server_source)
        VALUES
            ('album-1', 'artist-1', 'Flower Boy', 2017, '/rest/getCoverArt?id=correct-cover', 'navidrome')
    """)
    db._conn.commit()


def test_album_refresh_preserves_existing_thumb_when_incoming_thumb_missing():
    db = _InMemoryDB()
    _seed(db)

    assert db.insert_or_update_media_album(_Album(), "artist-1", server_source="navidrome") is True

    row = db._conn.execute("SELECT thumb_url FROM albums WHERE id = 'album-1'").fetchone()
    assert row["thumb_url"] == "/rest/getCoverArt?id=correct-cover"


def test_album_refresh_updates_existing_thumb_when_incoming_thumb_present():
    db = _InMemoryDB()
    _seed(db)

    album = _Album()
    album.thumb = "/rest/getCoverArt?id=new-cover"

    assert db.insert_or_update_media_album(album, "artist-1", server_source="navidrome") is True

    row = db._conn.execute("SELECT thumb_url FROM albums WHERE id = 'album-1'").fetchone()
    assert row["thumb_url"] == "/rest/getCoverArt?id=new-cover"
