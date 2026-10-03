"""Sample Studio import hook — core/imports/side_effects.py.

The hook fires at the end of record_soulsync_library_entry: a FRESH track
insert enqueues background analysis exactly once. It must never break the
import itself (lazy import, swallows everything) and must not re-enqueue
for rows that already exist (re-imports / resume writes).
"""

import sqlite3
from types import SimpleNamespace

from core.imports import side_effects


class _Db:
    """A real Library v2 database; ``conn`` reads it like the old fixture."""

    def __init__(self, tmp_path):
        from database.music_database import MusicDatabase
        self.db = MusicDatabase(str(tmp_path / "m.db"))
        self.conn = self.db._get_connection()
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)


def _make_soulsync_db(tmp_path):
    return _Db(tmp_path)


def _patch_common(monkeypatch, conn):
    monkeypatch.setattr(side_effects, "get_database", lambda: conn.db)
    monkeypatch.setattr(
        side_effects,
        "_get_config_manager",
        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"),
    )
    import core.genre_filter as genre_filter

    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)


def _context(tmp_path, name="hook.wav"):
    final_path = tmp_path / name
    final_path.write_bytes(b"audio")
    return {
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Hook Artist"},
        "album": {
            "id": "sp-album",
            "name": "Hook Album",
            "release_date": "2024-02-03",
            "total_tracks": 10,
            "image_url": "https://img.example/album.jpg",
        },
        "track_info": {
            "id": "sp-track",
            "name": "Hook Song",
            "track_number": 1,
            "duration_ms": 180000,
            "artists": [{"name": "Hook Artist"}],
            "_source": "spotify",
        },
        "original_search_result": {
            "title": "Hook Song",
            "artists": [{"name": "Hook Artist"}],
            "_source": "spotify",
        },
        "_final_processed_path": str(final_path),
    }


def _artist_album():
    return {"name": "Hook Artist", "genres": ["rock"]}, {
        "is_album": True,
        "album_name": "Hook Album",
        "track_number": 1,
    }


def test_fresh_import_enqueues_analysis_exactly_once(tmp_path, monkeypatch):
    conn = _make_soulsync_db(tmp_path)
    _patch_common(monkeypatch, conn)
    calls = []
    monkeypatch.setattr(
        "core.sample.worker.enqueue_analysis", lambda tid: calls.append(tid) or "pending"
    )

    context = _context(tmp_path)
    artist_context, album_info = _artist_album()
    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    assert len(calls) == 1
    row = conn.execute("SELECT id FROM lib2_tracks").fetchone()
    assert row is not None
    # The enqueued id is the track id the insert minted.
    assert str(calls[0]) == str(row["id"])


def test_existing_row_does_not_reenqueue(tmp_path, monkeypatch):
    conn = _make_soulsync_db(tmp_path)
    _patch_common(monkeypatch, conn)
    calls = []
    monkeypatch.setattr(
        "core.sample.worker.enqueue_analysis", lambda tid: calls.append(tid) or "pending"
    )

    context = _context(tmp_path)
    artist_context, album_info = _artist_album()
    side_effects.record_soulsync_library_entry(context, artist_context, album_info)
    assert len(calls) == 1

    # Same file imported again: the row exists -> no second enqueue.
    side_effects.record_soulsync_library_entry(context, artist_context, album_info)
    assert len(calls) == 1


def test_enqueue_failure_never_breaks_import(tmp_path, monkeypatch):
    conn = _make_soulsync_db(tmp_path)
    _patch_common(monkeypatch, conn)

    def _boom(tid):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr("core.sample.worker.enqueue_analysis", _boom)

    context = _context(tmp_path)
    artist_context, album_info = _artist_album()
    # Must not raise — the track row still lands.
    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    row = conn.execute("SELECT title FROM lib2_tracks").fetchone()
    assert row is not None and row["title"] == "Hook Song"


def test_hook_is_silent_when_sample_worker_unimportable(tmp_path, monkeypatch):
    """Even an ImportError inside the lazy import must not break imports."""
    conn = _make_soulsync_db(tmp_path)
    _patch_common(monkeypatch, conn)

    import builtins

    real_import = builtins.__import__

    def _failing_import(name, *args, **kwargs):
        if name == "core.sample.worker":
            raise ImportError("no sample worker here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _failing_import)

    context = _context(tmp_path)
    artist_context, album_info = _artist_album()
    side_effects.record_soulsync_library_entry(context, artist_context, album_info)

    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 1
