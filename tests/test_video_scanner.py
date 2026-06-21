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


def test_scan_pauses_and_resumes_enrichment_workers(db):
    events = []
    scanner = VideoLibraryScanner(
        db, pause_workers=lambda: events.append("pause"),
        resume_workers=lambda: events.append("resume"))
    scanner.scan_sync(lambda: FakeSource([{"server_id": "m1", "title": "A"}], []))
    assert events == ["pause", "resume"]             # paused first, resumed after


def test_scan_resumes_enrichment_even_on_error(db):
    events = []
    scanner = VideoLibraryScanner(
        db, pause_workers=lambda: events.append("pause"),
        resume_workers=lambda: events.append("resume"))

    def boom():
        raise RuntimeError("server blew up")

    scanner.scan_sync(boom)                           # source_factory raises
    assert scanner.get_status()["state"] == "error"
    assert events == ["pause", "resume"]              # resumed despite the failure


def test_scan_resumes_enrichment_on_cancel(db):
    events = []
    scanner = VideoLibraryScanner(
        db, pause_workers=lambda: events.append("pause"),
        resume_workers=lambda: events.append("resume"))

    class S:
        server_name = "plex"
        def counts(self, incremental=False):
            return {"movies": 5, "shows": 0}
        def iter_movies(self, incremental=False):
            for i in range(5):
                scanner._cancel = True               # cancel mid-iteration
                yield {"server_id": "m%d" % i, "title": "M%d" % i}
        def iter_shows(self, incremental=False):
            return iter([])

    scanner.scan_sync(lambda: S())
    assert scanner.get_status()["state"] == "cancelled"
    assert events == ["pause", "resume"]              # resumed after cancel too


def test_scanner_with_no_hooks_does_not_pause(db):
    # Default construction (as in tests / when no engine wired) must be inert.
    scanner = VideoLibraryScanner(db)
    st = scanner.scan_sync(lambda: FakeSource([{"server_id": "m1", "title": "A"}], []))
    assert st["state"] == "done"                      # scans fine without hooks


def test_deep_scan_prunes_removed_items(db):
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: FakeSource(
        [{"server_id": "m1", "title": "A"}, {"server_id": "m2", "title": "B"}], []), mode="deep")
    assert db.dashboard_stats()["library"]["movies"] == 2
    scanner.scan_sync(lambda: FakeSource([{"server_id": "m1", "title": "A"}], []), mode="deep")
    assert db.dashboard_stats()["library"]["movies"] == 1


def test_deep_scan_shows_cleanup_phase_during_prune(db):
    # On a deep scan the bar hits 100% before the (slow) prune runs — the scanner
    # must surface a "cleaning up" phase so the UI doesn't look stuck at 100%.
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: FakeSource(
        [{"server_id": "m1", "title": "A"}, {"server_id": "m2", "title": "B"}], []), mode="deep")
    # Spy the phase at the exact moment prune is called on the next deep scan.
    seen = {}
    real_prune = db.prune_missing

    def spy(table, server, ids):
        seen[table] = scanner.get_status().get("phase")
        return real_prune(table, server, ids)

    db.prune_missing = spy
    scanner.scan_sync(lambda: FakeSource([{"server_id": "m1", "title": "A"}], []), mode="deep")
    assert seen.get("movies") == "cleaning up removed movies"


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
    # Populate past the small-library fallback so incremental stays incremental.
    for i in range(60):
        db.upsert_movie("plex", {"server_id": "p%d" % i, "title": str(i)})
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


def test_incremental_skips_known_and_early_stops(db):
    # Past the small-library fallback, with everything already known.
    for i in range(60):
        db.upsert_movie("plex", {"server_id": "k%d" % i, "title": "K%d" % i})
    new_item = {"server_id": "new1", "title": "New"}
    known = [{"server_id": "k%d" % i, "title": "K%d" % i} for i in range(60)]

    class S:
        server_name = "plex"
        def counts(self, incremental=False):
            return {"movies": 61, "shows": 0}
        def iter_movies(self, incremental=False):
            assert incremental is True          # NOT fallen back (library big enough)
            return iter([new_item] + known)     # one new, then a long run of known
        def iter_shows(self, incremental=False):
            return iter([])

    st = VideoLibraryScanner(db).scan_sync(lambda: S(), mode="incremental")
    assert st["state"] == "done"
    assert st["movies"] == 1                     # only the new one; known skipped
    assert db.table_count("movies") == 61


def test_incremental_falls_back_to_full_on_small_library(db):
    captured = {}

    class S:
        server_name = "plex"
        def counts(self, incremental=False):
            return {"movies": 3, "shows": 0}
        def iter_movies(self, incremental=False):
            captured["incremental"] = incremental
            return iter([{"server_id": "m%d" % i, "title": str(i)} for i in range(3)])
        def iter_shows(self, incremental=False):
            return iter([])

    st = VideoLibraryScanner(db).scan_sync(lambda: S(), mode="incremental")
    assert captured["incremental"] is False      # empty DB (<50) -> full pass
    assert st["movies"] == 3


def test_parse_plex_guids():
    from core.video.sources import _parse_plex_guids

    class _G:
        def __init__(self, gid): self.id = gid

    class _Obj:
        def __init__(self, guids): self.guids = guids

    got = _parse_plex_guids(_Obj([_G("imdb://tt1375666"), _G("tmdb://27205"), _G("tvdb://121361")]))
    assert got == {"tmdb_id": 27205, "imdb_id": "tt1375666", "tvdb_id": 121361}
    assert _parse_plex_guids(_Obj([])) == {"tmdb_id": None, "imdb_id": None, "tvdb_id": None}


def test_parse_jellyfin_providers():
    from core.video.sources import _parse_jf_providers
    got = _parse_jf_providers({"ProviderIds": {"Imdb": "tt123", "Tmdb": "27205", "Tvdb": "121361"}})
    assert got == {"imdb_id": "tt123", "tmdb_id": 27205, "tvdb_id": 121361}
    assert _parse_jf_providers({}) == {"imdb_id": None, "tmdb_id": None, "tvdb_id": None}


def test_core_video_imports_nothing_from_music():
    base = Path(__file__).resolve().parent.parent / "core" / "video"
    for py in base.glob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "music" not in s.lower(), f"{py.name}: music import leaked: {s!r}"


# ── media_type scope: Movies and TV are independent libraries ───────────────

_MOVIES = [{"server_id": "m1", "title": "A", "file": {"relative_path": "a.mkv", "size_bytes": 5}}]
_SHOWS = [{"server_id": "s1", "title": "Show", "seasons": [
    {"season_number": 1, "episodes": [
        {"episode_number": 1, "title": "E1", "file": {"relative_path": "e1.mkv"}}]}]}]


def test_movie_media_type_scans_only_movies(db):
    src = FakeSource(_MOVIES, _SHOWS)
    st = VideoLibraryScanner(db).scan_sync(lambda: src, "full", "movie")
    assert st["state"] == "done"
    assert (st["movies"], st["shows"], st["episodes"]) == (1, 0, 0)
    assert [k for k, _ in src.incremental_calls] == ["movies"]   # shows iterator never touched
    lib = db.dashboard_stats()["library"]
    assert (lib["movies"], lib["shows"]) == (1, 0)


def test_tv_media_type_scans_only_shows(db):
    # 'tv' is a friendly alias normalised to 'show'
    src = FakeSource(_MOVIES, _SHOWS)
    st = VideoLibraryScanner(db).scan_sync(lambda: src, "full", "tv")
    assert (st["movies"], st["shows"], st["episodes"]) == (0, 1, 1)
    assert [k for k, _ in src.incremental_calls] == ["shows"]    # movies iterator never touched
    lib = db.dashboard_stats()["library"]
    assert (lib["movies"], lib["shows"]) == (0, 1)


def test_all_media_type_scans_both(db):
    src = FakeSource(_MOVIES, _SHOWS)
    st = VideoLibraryScanner(db).scan_sync(lambda: src, "full", "all")
    assert (st["movies"], st["shows"], st["episodes"]) == (1, 1, 1)
    assert {k for k, _ in src.incremental_calls} == {"movies", "shows"}


def test_concurrent_scan_reports_in_progress_without_running(db):
    scanner = VideoLibraryScanner(db)
    scanner._status = {"state": "scanning"}        # a scan is already running
    src = FakeSource(_MOVIES, _SHOWS)
    st = scanner.scan_sync(lambda: src, "full", "movie")
    assert st["state"] == "in_progress"
    assert src.incremental_calls == []             # nothing scanned — didn't stomp the live run
