"""#1562 / Major C (round 3): the import stamps record_type at import time.

Before this, ``record_soulsync_library_entry`` wrote no release kind at
all — ``record_type`` was only backfilled later by the enrichment sweep,
so freshly imported releases were kind-blind (and the single/album gates
lenient-always) until the workers ran. The pipeline already carries the
kind (post_processing stamps ``album_info['record_type']``); the import
now persists it on both the insert and the re-import fill-empty path,
without ever overwriting a kind the enrichment workers already wrote.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from core.imports import side_effects


class _FakeDB:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


def _make_soulsync_db(with_record_type=True):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE artists (
            id TEXT PRIMARY KEY, name TEXT, genres TEXT, thumb_url TEXT,
            server_source TEXT, created_at TEXT, updated_at TEXT,
            spotify_artist_id TEXT
        )
        """
    )
    record_col = ", record_type TEXT" if with_record_type else ""
    conn.execute(
        f"""
        CREATE TABLE albums (
            id TEXT PRIMARY KEY, artist_id TEXT, title TEXT, year INTEGER,
            thumb_url TEXT, genres TEXT, track_count INTEGER, duration INTEGER,
            server_source TEXT, created_at TEXT, updated_at TEXT,
            spotify_album_id TEXT{record_col}
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, title TEXT,
            track_number INTEGER, duration INTEGER, file_path TEXT,
            bitrate INTEGER, file_size INTEGER, track_artist TEXT,
            musicbrainz_recording_id TEXT, isrc TEXT,
            quality_profile_id INTEGER, server_source TEXT,
            created_at TEXT, updated_at TEXT, spotify_track_id TEXT,
            deezer_id TEXT
        )
        """
    )
    return conn


def _track_context(final_path, track_id, track_name, track_number):
    return {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Test Artist"},
        "album": {
            "id": "sp-album",
            "name": "Some Album",
            "release_date": "2024-01-01",
            "total_tracks": 2,
        },
        "track_info": {
            "id": track_id,
            "name": track_name,
            "track_number": track_number,
            "duration_ms": 200000,
            "artists": [{"name": "Test Artist"}],
            "_source": "spotify",
        },
        "_final_processed_path": final_path,
    }


@pytest.fixture()
def soulsync_db(monkeypatch):
    conn = _make_soulsync_db()
    monkeypatch.setattr(side_effects, "get_database", lambda: _FakeDB(conn))
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)
    return conn


def _album_kind(conn):
    row = conn.execute("SELECT record_type FROM albums").fetchone()
    return row["record_type"] if row else None


def test_import_stamps_record_type_on_insert(soulsync_db):
    """New album row carries the pipeline's record_type immediately."""
    side_effects.record_soulsync_library_entry(
        _track_context("/music/Test Artist/Some Album/01 - A.flac", "sp-a", "A", 1),
        {"name": "Test Artist", "genres": []},
        {"album_name": "Some Album", "record_type": "single", "album_type": "single"},
    )
    assert _album_kind(soulsync_db) == "single"


def test_import_falls_back_to_album_type(soulsync_db):
    """When the pipeline only carries album_type, that is what lands."""
    side_effects.record_soulsync_library_entry(
        _track_context("/music/Test Artist/Some Album/01 - A.flac", "sp-a", "A", 1),
        {"name": "Test Artist", "genres": []},
        {"album_name": "Some Album", "album_type": "EP"},
    )
    assert _album_kind(soulsync_db) == "ep"


def test_import_fills_empty_record_type_on_reimport(soulsync_db):
    """First import without a kind leaves it empty; a re-import carrying
    the kind fills it (fill-empty, never overwrite)."""
    ctx = lambda n: _track_context(
        f"/music/Test Artist/Some Album/0{n} - T{n}.flac", f"sp-{n}", f"T{n}", n
    )
    side_effects.record_soulsync_library_entry(
        ctx(1), {"name": "Test Artist", "genres": []}, {"album_name": "Some Album"}
    )
    assert _album_kind(soulsync_db) is None
    side_effects.record_soulsync_library_entry(
        ctx(2),
        {"name": "Test Artist", "genres": []},
        {"album_name": "Some Album", "record_type": "single"},
    )
    assert _album_kind(soulsync_db) == "single"


def test_import_never_overwrites_existing_record_type(soulsync_db):
    """A kind the enrichment workers already wrote survives a re-import."""
    ctx = lambda n: _track_context(
        f"/music/Test Artist/Some Album/0{n} - T{n}.flac", f"sp-{n}", f"T{n}", n
    )
    side_effects.record_soulsync_library_entry(
        ctx(1),
        {"name": "Test Artist", "genres": []},
        {"album_name": "Some Album", "record_type": "single"},
    )
    soulsync_db.execute("UPDATE albums SET record_type = 'ep'")
    side_effects.record_soulsync_library_entry(
        ctx(2),
        {"name": "Test Artist", "genres": []},
        {"album_name": "Some Album", "record_type": "single"},
    )
    assert _album_kind(soulsync_db) == "ep"


def test_import_without_record_type_column_does_not_crash(monkeypatch, tmp_path):
    """Minimal/legacy schemas lacking the column keep importing — the
    PRAGMA guard skips the column instead of failing the insert."""
    conn = _make_soulsync_db(with_record_type=False)
    monkeypatch.setattr(side_effects, "get_database", lambda: _FakeDB(conn))
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)
    side_effects.record_soulsync_library_entry(
        _track_context("/music/Test Artist/Some Album/01 - A.flac", "sp-a", "A", 1),
        {"name": "Test Artist", "genres": []},
        {"album_name": "Some Album", "record_type": "single"},
    )
    row = conn.execute("SELECT title FROM albums").fetchone()
    assert row["title"] == "Some Album"
