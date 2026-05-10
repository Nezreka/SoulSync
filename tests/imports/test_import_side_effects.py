import os
import sqlite3
from types import SimpleNamespace

from core.imports import side_effects


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
            file_size INTEGER,
            track_artist TEXT,
            musicbrainz_recording_id TEXT,
            isrc TEXT,
            server_source TEXT,
            created_at TEXT,
            updated_at TEXT,
            spotify_track_id TEXT,
            deezer_id TEXT
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
    # File size in bytes — populates the Library Disk Usage card on Stats.
    # Read via os.path.getsize at insert time since SoulSync standalone is
    # the only flow where the file is local at the moment we write the row.
    assert track_row["file_size"] == os.path.getsize(str(final_path))


def test_record_soulsync_library_entry_ignores_numeric_spotify_ids(tmp_path, monkeypatch):
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

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    context = {
        "source": "spotify",
        "artist": {"id": "396753", "name": "Artist One"},
        "album": {
            "id": "284076172",
            "name": "Album One",
            "release_date": "2024-02-03",
            "total_tracks": 12,
        },
        "track_info": {
            "id": "1607091752",
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

    artist_context = {"name": "Artist One", "genres": ["rock"]}
    album_info = {"is_album": True, "album_name": "Album One", "track_number": 7}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    artist_row = conn.execute("SELECT * FROM artists").fetchone()
    album_row = conn.execute("SELECT * FROM albums").fetchone()
    track_row = conn.execute("SELECT * FROM tracks").fetchone()

    assert artist_row["spotify_artist_id"] is None
    assert album_row["spotify_album_id"] is None
    assert track_row["spotify_track_id"] is None


# ---------------------------------------------------------------------------
# SoulSync standalone parity — auto-import / direct download must write the
# same field richness a Plex/Jellyfin/Navidrome scan would write. Pin the
# per-recording identifier columns (`musicbrainz_recording_id`, `isrc`)
# AND the source-aware ID columns (`deezer_id`, etc.) for non-Spotify
# sources so dev work can't silently drop them.
# ---------------------------------------------------------------------------


def test_record_soulsync_library_entry_writes_mbid_and_isrc(tmp_path, monkeypatch):
    """Per-recording IDs land on the tracks row when the metadata source
    provides them (Picard-tagged libraries, MusicBrainz-enriched
    Spotify, etc.). Without this, watchlist re-download checks fall
    back to fuzzy name matching and re-download tracks the user
    already has."""
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
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    context = {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Picard Artist"},
        "album": {
            "id": "sp-album", "name": "Tagged Album",
            "release_date": "2022-01-01", "total_tracks": 10,
        },
        "track_info": {
            "id": "sp-track", "name": "Tagged Track",
            "track_number": 3, "duration_ms": 195000,
            "artists": [{"name": "Picard Artist"}],
            # Per-recording IDs — read by Mutagen from MUSICBRAINZ_TRACKID
            # tag (Picard) or surfaced from the metadata source's response.
            "musicbrainz_recording_id": "abcd1234-mbid-uuid-form",
            "isrc": "USABC1234567",
        },
        "original_search_result": {"title": "Tagged Track"},
        "_final_processed_path": str(final_path),
    }
    artist_context = {"name": "Picard Artist", "genres": []}
    album_info = {"is_album": True, "album_name": "Tagged Album", "track_number": 3}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    row = conn.execute("SELECT musicbrainz_recording_id, isrc FROM tracks").fetchone()
    assert row["musicbrainz_recording_id"] == "abcd1234-mbid-uuid-form"
    assert row["isrc"] == "USABC1234567"


def test_record_soulsync_library_entry_handles_deezer_source(tmp_path, monkeypatch):
    """Deezer source maps all three (artist/album/track) IDs onto the
    `deezer_id` column. Verify the source-aware column resolver routes
    correctly — a regression here means deezer-primary users get
    soulsync rows with no source ID at all."""
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
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)

    context = {
        "source": "deezer",
        "artist": {"id": "12345", "name": "DZ Artist"},
        "album": {"id": "67890", "name": "DZ Album", "total_tracks": 5},
        "track_info": {
            "id": "111213",
            "name": "DZ Track",
            "track_number": 1,
            "duration_ms": 180000,
            "artists": [{"name": "DZ Artist"}],
        },
        "original_search_result": {"title": "DZ Track"},
        "_final_processed_path": str(final_path),
    }
    artist_context = {"name": "DZ Artist", "genres": []}
    album_info = {"is_album": True, "album_name": "DZ Album", "track_number": 1}

    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    track_row = conn.execute("SELECT deezer_id FROM tracks").fetchone()
    # Deezer source map writes the track's source-id onto the deezer_id
    # column (same column name the artist + album use; deezer doesn't
    # split per-entity-type ID columns the way Spotify / iTunes do).
    assert track_row["deezer_id"] == "111213"


# ---------------------------------------------------------------------------
# Auto-import labelling — library history + provenance must show
# "Auto-Import" / "auto_import" instead of falling back to "Soulseek".
# ---------------------------------------------------------------------------


def _make_history_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE library_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT, title TEXT, artist_name TEXT, album_name TEXT,
            quality TEXT, file_path TEXT, thumb_url TEXT, download_source TEXT,
            source_track_id TEXT, source_track_title TEXT, source_filename TEXT,
            acoustid_result TEXT, source_artist TEXT, created_at TEXT
        )
        """
    )
    return conn


def test_library_history_labels_auto_import(monkeypatch):
    """Auto-import sets `_download_username='auto_import'`; history row
    must read 'Auto-Import' instead of falling back to 'Soulseek'."""
    conn = _make_history_db()

    captured = {}

    class _DBStub:
        def add_library_history_entry(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(side_effects, "get_database", lambda: _DBStub())

    context = {
        "_download_username": "auto_import",
        "track_info": {
            "name": "Auto-Imported Track",
            "artists": [{"name": "Some Artist"}],
            "album": "Some Album",
            "id": "abc",
        },
        "original_search_result": {},
        "_final_processed_path": "/library/some-album/01.flac",
    }
    side_effects.record_library_history_download(context)
    assert captured["download_source"] == "Auto-Import"
    assert captured["title"] == "Auto-Imported Track"


def test_provenance_labels_auto_import(monkeypatch):
    """Same gate for provenance: `_download_username='auto_import'`
    must register the provenance row as `auto_import` (lowercase /
    canonical), not the `soulseek` fallback default."""
    captured = {}

    class _DBStub:
        def record_track_download(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(side_effects, "get_database", lambda: _DBStub())

    context = {
        "_download_username": "auto_import",
        "track_info": {
            "name": "Auto-Imported Track",
            "artists": [{"name": "Some Artist"}],
            "album": "Some Album",
            "id": "abc",
        },
        "original_search_result": {},
        "_final_processed_path": "/library/some-album/01.flac",
    }
    side_effects.record_download_provenance(context)
    assert captured.get("source_service") == "auto_import"
