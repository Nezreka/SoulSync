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

    def iter_movies(self):
        return iter(self._movies)

    def iter_shows(self):
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


def test_scan_sync_prunes_removed_items(db):
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: FakeSource(
        [{"server_id": "m1", "title": "A"}, {"server_id": "m2", "title": "B"}], []))
    assert db.dashboard_stats()["library"]["movies"] == 2
    scanner.scan_sync(lambda: FakeSource([{"server_id": "m1", "title": "A"}], []))
    assert db.dashboard_stats()["library"]["movies"] == 1


def test_empty_scan_does_not_wipe_library(db):
    # Safety: a scan that returns nothing (transient failure) must NOT prune.
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: FakeSource([{"server_id": "m1", "title": "A"}], []))
    scanner.scan_sync(lambda: FakeSource([], []))
    assert db.dashboard_stats()["library"]["movies"] == 1


def test_scan_sync_no_source_reports_error(db):
    st = VideoLibraryScanner(db).scan_sync(lambda: None)
    assert st["state"] == "error" and "error" in st


def test_core_video_imports_nothing_from_music():
    base = Path(__file__).resolve().parent.parent / "core" / "video"
    for py in base.glob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "music" not in s.lower(), f"{py.name}: music import leaked: {s!r}"
