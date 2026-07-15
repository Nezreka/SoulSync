"""docs/library-v2.md §25.2 — the importer only seeds format/bitrate/size
from the legacy DB; it never opens a file, so ``tags_json`` stays at its
schema default and has_replaygain/has_lyrics read as False until a manual
"Refresh & Scan". ``precache_tag_cache`` runs that same tag read right after
import, bounded by a ThreadPoolExecutor (same pattern as
precache_all_artwork/precache_tracklists, see test_artwork_precache_parallel.py)
so a large library doesn't block import on a serial file-by-file loop.
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from core.library2.importer import import_legacy_library
from core.library2.tag_cache import precache_tag_cache


def _database(legacy_db):
    import_legacy_library(legacy_db)
    return legacy_db


def test_precache_tag_cache_populates_never_scanned_file(
        legacy_db_factory, tmp_path, monkeypatch):
    database = _database(legacy_db_factory(n_albums=1))
    real_file = tmp_path / "track.flac"
    real_file.write_bytes(b"not-real-audio")

    monkeypatch.setattr(
        "core.library2.paths.resolve_lib2_path", lambda _path: str(real_file)
    )
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: {
        "title": "Track 0",
        "artist": "Many",
        "album": "Album 0",
        "album_artist": "Many",
        "track_number": 1,
        "disc_number": 1,
        "year": "2020",
        "genre": "rap",
        "has_cover_art": True,
        "replaygain_track_gain": "-6.0 dB",
        "lyrics": "some lyrics",
        "error": None,
    })

    counts = precache_tag_cache(database, None)

    assert counts == {"scanned": 1, "updated": 1}
    conn = database._get_connection()
    try:
        row = conn.execute(
            "SELECT tags_json FROM lib2_track_files WHERE path='/m/0.flac'"
        ).fetchone()
    finally:
        conn.close()
    tags = json.loads(row["tags_json"])
    assert tags["replaygain_track_gain"] == "-6.0 dB"
    assert tags["lyrics"] == "some lyrics"


def test_precache_tag_cache_skips_already_scanned_files(
        legacy_db, tmp_path, monkeypatch):
    """A file whose tags_json is no longer the schema default must not be
    re-read — precache_tag_cache is for never-scanned imports only, a real
    scan/refresh already did the work."""
    database = _database(legacy_db)
    conn = database._get_connection()
    conn.execute(
        "UPDATE lib2_track_files SET tags_json=? WHERE path='/m/01.flac'",
        (json.dumps({"title": "One Dance"}),),
    )
    conn.commit()
    conn.close()

    real_file = tmp_path / "single.flac"
    real_file.write_bytes(b"not-real-audio")
    monkeypatch.setattr(
        "core.library2.paths.resolve_lib2_path", lambda _path: str(real_file)
    )

    def _read_tags(_path):
        return {
            "title": "One Dance", "artist": "Drake", "album": "One Dance",
            "album_artist": "Drake", "track_number": 1, "disc_number": 1,
            "year": "2016", "genre": None, "has_cover_art": False, "error": None,
        }

    monkeypatch.setattr("core.tag_writer.read_file_tags", _read_tags)

    counts = precache_tag_cache(database, None)

    # Only /m/single.flac (track 102) was ever un-scanned; /m/01.flac was
    # excluded by the tags_json != '{}' filter.
    assert counts == {"scanned": 1, "updated": 1}
    conn = database._get_connection()
    try:
        row = conn.execute(
            "SELECT tags_json FROM lib2_track_files WHERE path='/m/01.flac'"
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["tags_json"]) == {"title": "One Dance"}


def test_precache_tag_cache_runs_reads_concurrently(legacy_db_factory, monkeypatch):
    """6 never-scanned files, default pool size 3 — peak in-flight reads must
    reach 3, proving real concurrency rather than a serial loop."""
    database = _database(legacy_db_factory(n_albums=6))

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda p: p)

    in_flight = [0]
    peak = [0]
    lock = threading.Lock()
    proceed = threading.Event()

    def slow_read(_path):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        proceed.wait(timeout=2)
        with lock:
            in_flight[0] -= 1
        return {"error": "unreachable-in-test"}

    monkeypatch.setattr("core.tag_writer.read_file_tags", slow_read)

    result = {}

    def run():
        result["counts"] = precache_tag_cache(database, None)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)
    assert peak[0] == 3, (
        f"Expected 3 concurrent tag reads (default max_workers), "
        f"peaked at {peak[0]} — precache_tag_cache looks serial."
    )
    proceed.set()
    t.join(timeout=5)


def test_precache_tag_cache_max_workers_caps_concurrency(legacy_db_factory, monkeypatch):
    database = _database(legacy_db_factory(n_albums=6))

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda p: p)

    in_flight = [0]
    peak = [0]
    lock = threading.Lock()
    proceed = threading.Event()

    def slow_read(_path):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        proceed.wait(timeout=2)
        with lock:
            in_flight[0] -= 1
        return {"error": "unreachable-in-test"}

    monkeypatch.setattr("core.tag_writer.read_file_tags", slow_read)

    config = MagicMock()
    config.get = MagicMock(
        side_effect=lambda key, default: 2 if key == "auto_import.max_workers" else default
    )

    result = {}

    def run():
        result["counts"] = precache_tag_cache(database, config)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)
    assert peak[0] == 2, (
        f"auto_import.max_workers=2 should cap concurrency at 2, peaked at {peak[0]}"
    )
    proceed.set()
    t.join(timeout=5)


def test_precache_tag_cache_unresolvable_path_is_skipped(legacy_db, monkeypatch):
    """A file whose path can't be resolved (missing/mapped-away) must not
    count as updated, and must not crash the pass. Both never-scanned rows
    from the legacy_db fixture (/m/01.flac, /m/single.flac) are attempted
    ("scanned") but neither resolves, so neither is "updated"."""
    database = _database(legacy_db)
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: None)

    counts = precache_tag_cache(database, None)

    assert counts == {"scanned": 2, "updated": 0}
