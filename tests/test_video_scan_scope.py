"""Video scan SCOPE + worker-pause coupling.

1. The scan must read ONLY the user-mapped Movies/TV libraries — and when a kind
   isn't mapped, scan NOTHING (never silently fall back to all libraries, which is
   how YouTube/4K libraries leaked in).
2. A library scan (any mode) pauses EVERY enricher — including the YouTube date
   enricher, which is a separate singleton outside engine.workers.
"""

from __future__ import annotations

import pytest

from core.video.sources import PlexVideoSource
from database.video_database import VideoDatabase
from core.video.enrichment.engine import VideoEnrichmentEngine
import core.video.youtube_enrichment as yt_mod


# ── 1. scan scope ──────────────────────────────────────────────────────────

class _Sec:
    def __init__(self, type_, title):
        self.type = type_
        self.title = title


class _Lib:
    def __init__(self, secs):
        self._secs = secs

    def sections(self):
        return self._secs


class _Server:
    def __init__(self, secs):
        self.library = _Lib(secs)


_SECTIONS = [_Sec("movie", "Movies"), _Sec("movie", "4K Movies"),
             _Sec("show", "TV Shows"), _Sec("show", "YouTube")]


def test_scan_uses_only_the_mapped_libraries():
    src = PlexVideoSource(_Server(_SECTIONS), movies_lib="Movies", tv_lib="TV Shows")
    assert [s.title for s in src._scan_sections("movie", src._movies_lib)] == ["Movies"]
    assert [s.title for s in src._scan_sections("show", src._tv_lib)] == ["TV Shows"]
    # the 4K + YouTube libraries are NOT scanned…
    titles = [s.title for s in src._scan_sections("movie", src._movies_lib)] + \
             [s.title for s in src._scan_sections("show", src._tv_lib)]
    assert "4K Movies" not in titles and "YouTube" not in titles
    # …but available_libraries still LISTS them all (for the Settings dropdown).
    avail = src.available_libraries()
    assert {x["title"] for x in avail["movies"]} == {"Movies", "4K Movies"}
    assert {x["title"] for x in avail["tv"]} == {"TV Shows", "YouTube"}


def test_unmapped_kind_scans_nothing_not_everything():
    # The hardening: a missing selection must scan NOTHING, never fall back to all.
    src = PlexVideoSource(_Server(_SECTIONS), movies_lib=None, tv_lib=None)
    assert src._scan_sections("movie", src._movies_lib) == []
    assert src._scan_sections("show", src._tv_lib) == []
    # _sections (used for LISTING) still returns all — only the SCAN path is gated.
    assert len(src._sections("movie")) == 2


# ── 2. scan pauses every enricher (incl. the YouTube date singleton) ───────

class _FakeYT:
    def __init__(self, paused=False):
        self._paused = paused

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False


@pytest.fixture()
def engine(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    return VideoEnrichmentEngine(db, clients={})


def test_scan_pauses_and_resumes_youtube_date_enricher(engine, monkeypatch):
    fake = _FakeYT()
    monkeypatch.setattr(yt_mod, "get_youtube_date_enricher", lambda: fake)
    engine.pause_for_scan()
    assert fake._paused is True
    assert "youtube" in engine._scan_paused
    engine.resume_after_scan()
    assert fake._paused is False


def test_scan_never_resumes_a_manually_paused_youtube_enricher(engine, monkeypatch):
    fake = _FakeYT(paused=True)   # the user paused it themselves
    monkeypatch.setattr(yt_mod, "get_youtube_date_enricher", lambda: fake)
    engine.pause_for_scan()
    assert "youtube" not in engine._scan_paused   # we didn't touch it
    engine.resume_after_scan()
    assert fake._paused is True                    # still paused after the scan


# ── refresh_sections is scoped by media_type too (server nudge) ─────────────

def test_refresh_sections_scopes_by_media_type():
    class SecU:
        def __init__(self, type_, title):
            self.type, self.title, self.updated = type_, title, False

        def update(self):
            self.updated = True

    secs = [SecU("movie", "Movies"), SecU("show", "TV Shows")]
    src = PlexVideoSource(_Server(secs), movies_lib="Movies", tv_lib="TV Shows")

    src.refresh_sections("movie")
    assert secs[0].updated and not secs[1].updated       # only the Movie section nudged
    secs[0].updated = False

    src.refresh_sections("show")
    assert secs[1].updated and not secs[0].updated       # only the TV section nudged
    secs[1].updated = False

    res = src.refresh_sections("all")
    assert secs[0].updated and secs[1].updated and res["sections"] == 2   # both


# ── scan-status detection (poll-until-idle uses this) ───────────────────────

def test_plex_is_scanning_reads_section_refreshing_flag_scoped():
    class Sec:
        def __init__(self, type_, title, refreshing=False):
            self.type, self.title, self.refreshing = type_, title, refreshing

    class Srv:
        def __init__(self, secs): self.library = _Lib(secs)
        def activities(self): return []

    # TV section refreshing, movie idle
    secs = [Sec("movie", "Movies", False), Sec("show", "TV Shows", True)]
    src = PlexVideoSource(Srv(secs), movies_lib="Movies", tv_lib="TV Shows")
    assert src.is_scanning("show") is True        # TV is mid-scan
    assert src.is_scanning("movie") is False       # movie idle → not scanning for that scope
    assert src.is_scanning("all") is True          # either counts


def test_plex_is_scanning_falls_back_to_activity_feed():
    class Sec:
        def __init__(self, type_, title): self.type, self.title, self.refreshing = type_, title, False
    class Act:
        def __init__(self, type_, title): self.type, self.title = type_, title
    class Srv:
        def __init__(self, secs, acts): self.library, self._acts = _Lib(secs), acts
        def activities(self): return self._acts
    secs = [Sec("movie", "Movies")]
    src = PlexVideoSource(Srv(secs, [Act("library.refresh", "Scanning Movies…")]),
                          movies_lib="Movies", tv_lib="TV Shows")
    assert src.is_scanning("movie") is True        # no refreshing flag, but the feed shows a scan


def test_scan_status_helper_is_none_when_no_server(monkeypatch):
    import core.video.sources as srcmod
    monkeypatch.setattr(srcmod, "get_active_video_source", lambda: None)
    assert srcmod.video_server_scan_in_progress("all") is None   # caller falls back to fixed wait


# ── has_item probe (smart post-download scan) ───────────────────────────────

def test_plex_has_item_matches_movie_by_title_and_year():
    class Movie:
        def __init__(self, title, year): self.title, self.year = title, year
    class Sec:
        type = "movie"
        def __init__(self, title, results): self.title, self._r = title, results
        def search(self, title=None, maxresults=5): return self._r
    class Srv:
        def __init__(self, secs): self.library = _Lib(secs)
    secs = [Sec("Movies", [Movie("Dune", 2024)])]
    # _scan_sections filters by title==movies_lib; give the section that title
    secs[0].title = "Movies"
    src = PlexVideoSource(Srv(secs), movies_lib="Movies", tv_lib="TV Shows")
    assert src.has_item("movie", {"title": "Dune", "year": 2024}) is True
    assert src.has_item("movie", {"title": "Dune", "year": 1990}) is False   # year mismatch
    assert src.has_item("movie", {"title": "Nope", "year": 2024}) is True    # search returns the same stub; title checked server-side in real plex


def test_plex_has_item_checks_specific_episode():
    class Show:
        def __init__(self, has): self._has = has
        def episode(self, season=None, episode=None):
            if self._has == (season, episode): return object()
            raise Exception("no such episode")
    class Sec:
        type = "show"
        def __init__(self, title, results): self.title, self._r = title, results
        def search(self, title=None, maxresults=5): return self._r
    class Srv:
        def __init__(self, secs): self.library = _Lib(secs)
    secs = [Sec("TV Shows", [Show((2, 5))])]
    src = PlexVideoSource(Srv(secs), movies_lib="Movies", tv_lib="TV Shows")
    assert src.has_item("show", {"title": "Severance", "season_number": 2, "episode_number": 5}) is True
    assert src.has_item("show", {"title": "Severance", "season_number": 2, "episode_number": 9}) is False


def test_has_item_helper_false_when_no_server(monkeypatch):
    import core.video.sources as srcmod
    monkeypatch.setattr(srcmod, "get_active_video_source", lambda: None)
    assert srcmod.video_server_has_item("movie", {"title": "X"}) is False
