"""Seam tests for the video library scanner (experimental branch).

The scanner is server-agnostic: it consumes a media source that yields
normalized dicts. We drive it with a fake source so the scan/prune/error logic
is fully tested without a live Plex/Jellyfin. Also guards that core/video/
imports nothing from the music database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from database.video_database import VideoDatabase
from core.video.scanner import VideoLibraryScanner


class FakeSource:
    server_name = "plex"

    def __init__(self, movies, shows):
        self._movies, self._shows = movies, shows
        self.incremental_calls = []

    def iter_movies(self, incremental=False):
        self.incremental_calls.append(("movies", incremental))
        return iter(self._movies)

    def iter_shows(self, incremental=False):
        self.incremental_calls.append(("shows", incremental))
        return iter(self._shows)


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_scan_sync_populates_library(db):
    movies = [{"server_id": "m1", "title": "A", "file": {"relative_path": "a.mkv", "size_bytes": 5}}]
    shows = [{"server_id": "s1", "title": "Show", "seasons": [
        {"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "E1", "file": {"relative_path": "e1.mkv"}}]}]}]
    st = VideoLibraryScanner(db).scan_sync(lambda: FakeSource(movies, shows))
    assert st["state"] == "done"
    assert (st["movies"], st["shows"], st["episodes"]) == (1, 1, 1)
    lib = db.dashboard_stats()["library"]
    assert (lib["movies"], lib["shows"], lib["episodes"]) == (1, 1, 1)


def test_deep_scan_prunes_removed_items(db):
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: FakeSource(
        [{"server_id": "m1", "title": "A"}, {"server_id": "m2", "title": "B"}], []), mode="deep")
    assert db.dashboard_stats()["library"]["movies"] == 2
    scanner.scan_sync(lambda: FakeSource([{"server_id": "m1", "title": "A"}], []), mode="deep")
    assert db.dashboard_stats()["library"]["movies"] == 1


def test_full_refresh_does_not_prune(db):
    # 'full' refreshes/adds but never removes — only 'deep' prunes.
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: FakeSource(
        [{"server_id": "m1", "title": "A"}, {"server_id": "m2", "title": "B"}], []), mode="deep")
    scanner.scan_sync(lambda: FakeSource([{"server_id": "m1", "title": "A"}], []), mode="full")
    assert db.dashboard_stats()["library"]["movies"] == 2  # m2 NOT pruned


def test_empty_deep_scan_does_not_wipe_library(db):
    # Safety: a deep scan that returns nothing (transient failure) must NOT prune.
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: FakeSource([{"server_id": "m1", "title": "A"}], []), mode="deep")
    scanner.scan_sync(lambda: FakeSource([], []), mode="deep")
    assert db.dashboard_stats()["library"]["movies"] == 1


def test_incremental_mode_requests_incremental_from_source(db):
    src = FakeSource([{"server_id": "m1", "title": "A"}], [])
    VideoLibraryScanner(db).scan_sync(lambda: src, mode="incremental")
    assert ("movies", True) in src.incremental_calls
    assert ("shows", True) in src.incremental_calls


def test_scan_sync_no_source_reports_error(db):
    st = VideoLibraryScanner(db).scan_sync(lambda: None)
    assert st["state"] == "error" and "error" in st


def test_scan_reports_percent_from_counts(db):
    class S:
        server_name = "plex"
        def counts(self, incremental=False):
            return {"movies": 4, "shows": 0}
        def iter_movies(self, incremental=False):
            return iter([{"server_id": "m%d" % i, "title": str(i)} for i in range(4)])
        def iter_shows(self, incremental=False):
            return iter([])
    st = VideoLibraryScanner(db).scan_sync(lambda: S())
    assert st["state"] == "done"
    assert st["percent"] == 100


def test_scan_cancel_stops_midway(db):
    scanner = VideoLibraryScanner(db)

    def gen():
        yield {"server_id": "m1", "title": "A"}
        scanner.cancel()                 # request stop after the first item
        yield {"server_id": "m2", "title": "B"}

    class S:
        server_name = "plex"
        def counts(self, incremental=False):
            return {"movies": 2, "shows": 0}
        def iter_movies(self, incremental=False):
            return gen()
        def iter_shows(self, incremental=False):
            return iter([])

    st = scanner.scan_sync(lambda: S())
    assert st["state"] == "cancelled"
    assert db.dashboard_stats()["library"]["movies"] == 1   # only the first was saved


def test_core_video_imports_nothing_from_music():
    base = Path(__file__).resolve().parent.parent / "core" / "video"
    for py in base.glob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "music" not in s.lower(), f"{py.name}: music import leaked: {s!r}"
