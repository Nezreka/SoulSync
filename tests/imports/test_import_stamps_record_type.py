"""#1562 / Major C (round 3): the import stamps the release kind at import time.

Before, the import wrote no kind at all and freshly imported releases were
kind-blind until an enrichment sweep ran. The pipeline already carries the
kind (post_processing stamps ``album_info['record_type']``).

Known kinds are protected; an explicitly unknown default may be filled later.
Legacy ambiguous defaults are protected during the additive migration.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

import core.imports.side_effects as side_effects
from core.library2.schema import ensure_library_v2_schema


class _FakeDB:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


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
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.commit()
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
    row = conn.execute("SELECT album_type FROM lib2_albums").fetchone()
    return row["album_type"] if row else None


def _import(n, album_info):
    side_effects.record_soulsync_library_entry(
        _track_context(f"/music/Test Artist/Some Album/0{n} - T{n}.flac", f"sp-{n}", f"T{n}", n),
        {"name": "Test Artist", "genres": []},
        album_info,
    )


def test_import_stamps_record_type_on_insert(soulsync_db):
    """New album row carries the pipeline's record_type immediately."""
    _import(1, {"album_name": "Some Album", "record_type": "single", "album_type": "single"})
    assert _album_kind(soulsync_db) == "single"


def test_import_falls_back_to_album_type(soulsync_db):
    """When the pipeline only carries album_type, that is what lands."""
    _import(1, {"album_name": "Some Album", "album_type": "EP"})
    assert _album_kind(soulsync_db) == "ep"


def test_import_without_a_kind_can_be_confirmed_later(soulsync_db):
    """A placeholder remains unknown until an import provides a real kind."""
    _import(1, {"album_name": "Some Album"})
    assert _album_kind(soulsync_db) == "album"
    _import(2, {"album_name": "Some Album", "record_type": "single"})
    assert _album_kind(soulsync_db) == "single"


def test_import_never_overwrites_existing_record_type(soulsync_db):
    """A kind the enrichment workers already wrote survives a re-import."""
    _import(1, {"album_name": "Some Album", "record_type": "single"})
    soulsync_db.execute("UPDATE lib2_albums SET album_type = 'ep'")
    _import(2, {"album_name": "Some Album", "record_type": "single"})
    assert _album_kind(soulsync_db) == "ep"


def test_confirmed_album_cannot_be_reclassified_by_later_import(soulsync_db):
    _import(1, {"album_name": "Some Album", "record_type": "album"})
    _import(2, {"album_name": "Some Album", "record_type": "single"})
    assert _album_kind(soulsync_db) == "album"
    assert soulsync_db.execute("SELECT album_type_known FROM lib2_albums").fetchone()[0] == 1
