"""docs/library-v2.md §17.6 — precache_all_artwork must dispatch its
per-entity ``build_artwork`` calls to a bounded ``ThreadPoolExecutor`` instead
of building one artist/album at a time. For a first-time migration of
thousands of tracks, each uncached album/artist can trigger a synchronous
provider network call, so a serial loop is the actual "import takes forever"
bottleneck (see docs §17.6). Mirrors the existing max_workers contract in
``core.auto_import_worker`` (default 3, config key ``auto_import.max_workers``).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from core.library2 import artwork
from core.library2.importer import import_legacy_library


def _database(legacy_db):
    import_legacy_library(legacy_db)
    return legacy_db


def test_precache_all_artwork_runs_builds_concurrently(legacy_db_factory, monkeypatch):
    """7 uncached entities (6 albums + 1 artist), default pool size 3 —
    peak in-flight builds must reach 3, proving real concurrency rather
    than a one-at-a-time loop."""
    database = _database(legacy_db_factory(n_albums=6))

    in_flight = [0]
    peak = [0]
    lock = threading.Lock()
    proceed = threading.Event()

    def slow_build(_database, _conn, _config_manager, _kind, _entity_id, **_kwargs):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        proceed.wait(timeout=2)
        with lock:
            in_flight[0] -= 1
        return None

    monkeypatch.setattr(artwork, "build_artwork", slow_build)

    result = {}

    def run():
        result["counts"] = artwork.precache_all_artwork(database, None)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)
    assert peak[0] == 3, (
        f"Expected 3 concurrent artwork builds (default max_workers), "
        f"peaked at {peak[0]} — precache_all_artwork looks serial."
    )
    proceed.set()
    t.join(timeout=5)


def test_precache_all_artwork_max_workers_caps_concurrency(legacy_db_factory, monkeypatch):
    """config auto_import.max_workers=2 must cap concurrent builds at 2,
    even with more than 2 pending entities."""
    database = _database(legacy_db_factory(n_albums=6))

    in_flight = [0]
    peak = [0]
    lock = threading.Lock()
    proceed = threading.Event()

    def slow_build(_database, _conn, _config_manager, _kind, _entity_id, **_kwargs):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        proceed.wait(timeout=2)
        with lock:
            in_flight[0] -= 1
        return None

    monkeypatch.setattr(artwork, "build_artwork", slow_build)

    config = MagicMock()
    config.get = MagicMock(
        side_effect=lambda key, default: 2 if key == "auto_import.max_workers" else default
    )

    result = {}

    def run():
        result["counts"] = artwork.precache_all_artwork(database, config)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)
    assert peak[0] == 2, (
        f"auto_import.max_workers=2 should cap concurrency at 2, peaked at {peak[0]}"
    )
    proceed.set()
    t.join(timeout=5)


def test_precache_all_artwork_skips_already_cached(legacy_db_factory, monkeypatch):
    """Correctness must survive the switch to a thread pool: an entity with
    an on-disk cached jpg is skipped (not rebuilt), others are built once
    each, and per-kind counts only reflect newly-built entries."""
    database = _database(legacy_db_factory(n_albums=2))

    calls = []
    calls_lock = threading.Lock()

    def fake_build(_database, _conn, _config_manager, kind, entity_id, **_kwargs):
        with calls_lock:
            calls.append((kind, entity_id))
        return "/fake/path.jpg"

    monkeypatch.setattr(artwork, "build_artwork", fake_build)

    conn = database._get_connection()
    album_ids = [r[0] for r in conn.execute("SELECT id FROM lib2_albums ORDER BY id")]
    conn.close()
    precached_album = album_ids[0]
    cached_file = artwork.artwork_file(database, "album", precached_album)
    cached_file.write_bytes(b"\xff\xd8\xff" + b"0" * 20)

    counts = artwork.precache_all_artwork(database, None)

    assert ("album", precached_album) not in calls, (
        "already-cached album must be skipped, not rebuilt"
    )
    assert counts["albums"] == len(album_ids) - 1
    assert counts["artists"] == 1


def test_precache_all_artwork_reports_small_library_progress(legacy_db_factory, monkeypatch):
    database = _database(legacy_db_factory(n_albums=2))
    monkeypatch.setattr(artwork, "build_artwork", lambda *_args, **_kwargs: None)
    events = []

    artwork.precache_all_artwork(
        database,
        None,
        progress=lambda stage, current, total: events.append((stage, current, total)),
    )

    assert events[0] == ("artwork", 0, 3)
    assert events[-1] == ("artwork", 3, 3)
