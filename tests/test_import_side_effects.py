import sqlite3
from types import SimpleNamespace

from core import import_side_effects as side_effects


class _FakeDB:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


def _make_soulsync_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE artists (
            id TEXT PRIMARY KEY,
            name TEXT,
            genres TEXT,
            thumb_url TEXT,
            server_source TEXT,
            created_at TEXT,
            updated_at TEXT,
            spotify_artist_id TEXT
        )
        """
    )
    conn.execute(
        """
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
            updated_at TEXT,
            spotify_album_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY,
            album_id TEXT,
            artist_id TEXT,
            title TEXT,
            track_number INTEGER,
            duration INTEGER,
            file_path TEXT,
            bitrate INTEGER,
            track_artist TEXT,
            server_source TEXT,
            created_at TEXT,
            updated_at TEXT,
            spotify_track_id TEXT
        )
        """
    )
    return conn


def test_record_soulsync_library_entry_writes_artist_album_and_track(tmp_path, monkeypatch):
    conn = _make_soulsync_db()
    fake_db = _FakeDB(conn)
    final_path = tmp_path / "track.flac"
    final_path.write_bytes(b"audio")

    monkeypatch.setattr(side_effects, "get_database", lambda: fake_db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )

    import core.genre_filter as genre_filter

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: [genre.upper() for genre in genres])

    context = {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Artist One"},
        "album": {
            "id": "sp-album",
            "name": "Album One",
            "release_date": "2024-02-03",
            "total_tracks": 12,
            "image_url": "https://img.example/album.jpg",
        },
        "track_info": {
            "id": "sp-track",
            "name": "Song One",
            "track_number": 7,
            "duration_ms": 210000,
            "artists": [{"name": "Guest Artist"}],
            "_source": "spotify",
        },
        "original_search_result": {
            "title": "Song One",
            "artists": [{"name": "Guest Artist"}],
            "_source": "spotify",
        },
        "_final_processed_path": str(final_path),
    }

    artist_context = {"name": "Artist One", "genres": ["rock", "indie"]}
    album_info = {"is_album": True, "album_name": "Album One", "track_number": 7}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    artist_row = conn.execute("SELECT * FROM artists").fetchone()
    album_row = conn.execute("SELECT * FROM albums").fetchone()
    track_row = conn.execute("SELECT * FROM tracks").fetchone()

    assert artist_row["name"] == "Artist One"
    assert artist_row["server_source"] == "soulsync"
    assert artist_row["spotify_artist_id"] == "sp-artist"
    assert artist_row["genres"] == '["ROCK", "INDIE"]'

    assert album_row["title"] == "Album One"
    assert album_row["server_source"] == "soulsync"
    assert album_row["spotify_album_id"] == "sp-album"
    assert album_row["year"] == 2024
    assert album_row["track_count"] == 12
    assert album_row["duration"] == 210000
    assert album_row["artist_id"] == artist_row["id"]

    assert track_row["title"] == "Song One"
    assert track_row["server_source"] == "soulsync"
    assert track_row["spotify_track_id"] == "sp-track"
    assert track_row["track_number"] == 7
    assert track_row["duration"] == 210000
    assert track_row["track_artist"] == "Guest Artist"
    assert track_row["album_id"] == album_row["id"]
    assert track_row["file_path"] == str(final_path)
