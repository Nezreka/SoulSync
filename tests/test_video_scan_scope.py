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
