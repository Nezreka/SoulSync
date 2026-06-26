"""Refresh-airing-schedules automation: re-pull TMDB episode schedules for still-airing
watchlist shows so the airing automation's LOCAL calendar read is current. Pure handler with
the show fetch + per-show refresh injected, plus the DB scoping query + the wiring contract."""

from __future__ import annotations

import pytest

from core.automation.handlers.video_refresh_airing_schedules import (
    auto_video_refresh_airing_schedules,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _logs(deps):
    return " ".join(p.get("log_line") or "" for p in deps.progress)


# ── handler ───────────────────────────────────────────────────────────────────
def test_refreshes_each_show_and_tallies():
    shows = [{"library_id": 1, "title": "A"}, {"library_id": 2, "title": "B"}, {"library_id": 3, "title": "C"}]
    seen = []

    def refresh(lib):
        seen.append(lib)
        return {"ok": lib != 2}                       # show 2 fails to match

    deps = _Deps()
    res = auto_video_refresh_airing_schedules({"_automation_id": "a"}, deps,
                                              fetch_shows=lambda: shows, refresh_show=refresh)
    assert res["status"] == "completed" and res["shows"] == 3
    assert res["refreshed"] == 2 and res["failed"] == 1
    assert seen == [1, 2, 3]                          # every show attempted
    assert "Refreshed 2 show schedule(s)" in _logs(deps) and "1 failed" in _logs(deps)


def test_empty_watchlist_is_a_clean_noop():
    deps = _Deps()
    res = auto_video_refresh_airing_schedules({"_automation_id": "a"}, deps, fetch_shows=lambda: [])
    assert res["status"] == "completed" and res["shows"] == 0 and res["refreshed"] == 0
    assert not any(p.get("status") == "error" for p in deps.progress)


def test_one_show_raising_does_not_stop_the_rest():
    def refresh(lib):
        if lib == 1:
            raise RuntimeError("tmdb timeout")
        return {"ok": True}

    res = auto_video_refresh_airing_schedules(
        {"_automation_id": "a"}, _Deps(),
        fetch_shows=lambda: [{"library_id": 1, "title": "A"}, {"library_id": 2, "title": "B"}],
        refresh_show=refresh)
    assert res["status"] == "completed" and res["refreshed"] == 1 and res["failed"] == 1


def test_top_level_error_is_caught():
    def boom():
        raise RuntimeError("db down")

    res = auto_video_refresh_airing_schedules({"_automation_id": "x"}, _Deps(), fetch_shows=boom)
    assert res["status"] == "error" and "db down" in res["error"]


# ── DB scoping ────────────────────────────────────────────────────────────────
from database.video_database import VideoDatabase  # noqa: E402


def test_watchlist_continuing_shows_skips_ended_tmdbonly_and_dupes(tmp_path, monkeypatch):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    rows = [
        {"library_id": 1, "tmdb_id": 10, "title": "Airing", "status": "Returning Series"},
        {"library_id": 2, "tmdb_id": 20, "title": "Done", "status": "Ended"},          # terminal → skip
        {"library_id": None, "tmdb_id": 30, "title": "Tmdb-only", "status": None},      # no episodes → skip
        {"library_id": 1, "tmdb_id": 10, "title": "Dup", "status": "Returning Series"}, # dup lib → once
        {"library_id": 4, "tmdb_id": 40, "title": "Unknown", "status": None},           # unknown → keep
    ]
    monkeypatch.setattr(db, "_effective_shows", lambda conn, ss: rows)
    out = db.watchlist_continuing_shows("plex")
    assert [s["library_id"] for s in out] == [1, 4]


# ── OMDb quota safety ─────────────────────────────────────────────────────────
def test_refresh_skips_omdb_ratings(monkeypatch):
    # the bulk refresh must NOT do the per-show OMDb ratings call (it'd burn the daily quota)
    import core.automation.handlers.video_refresh_airing_schedules as mod
    seen = {}

    class _Eng:
        def refresh_show_art(self, lib, *, with_ratings=True):
            seen["lib"], seen["with_ratings"] = lib, with_ratings
            return {"ok": True}

    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine", lambda: _Eng())
    assert mod._default_refresh_show(7) == {"ok": True}
    assert seen == {"lib": 7, "with_ratings": False}


def test_omdb_limit_latches_off_and_stops_hammering():
    from core.video.enrichment.engine import VideoEnrichmentEngine
    from core.video.enrichment.clients import OMDbAuthError

    class _Row:                                        # truthy row carrying an imdb id
        def __getitem__(self, k):
            return "tt123"

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            return type("C", (), {"fetchone": lambda s: _Row()})()

    class _DB:
        def show_match_info(self, i):
            return {"title": "X"}

        def connect(self):
            return _Conn()

        def apply_ratings(self, *a):
            pass

    class _RC:
        enabled = True

        def __init__(self):
            self.n = 0

        def ratings(self, imdb):
            self.n += 1
            raise OMDbAuthError("Request limit reached!")

    eng = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    eng.db, eng.ratings_client = _DB(), None
    rc = _RC()
    eng.workers = {"omdb": type("W", (), {"client": rc})()}
    eng._backfill_ratings("show", 1)               # hits the limit → latches off (no raise)
    assert getattr(eng, "_omdb_blocked", False) is True
    eng._backfill_ratings("show", 2)               # now short-circuits before calling OMDb
    assert rc.n == 1                               # only the first attempt ever reached OMDb


def test_tmdb_detail_ratings_share_the_same_latch():
    # the detail/drawer path (_fill_tmdb_ratings) must honour + set the SAME latch — it was
    # the source of the per-title traceback spam on the download drawer / detail pages.
    from core.video.enrichment.engine import VideoEnrichmentEngine
    from core.video.enrichment.clients import OMDbAuthError

    class _RC:
        enabled = True

        def __init__(self):
            self.n = 0

        def ratings(self, imdb):
            self.n += 1
            raise OMDbAuthError("Request limit reached!")

    eng = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    rc = _RC()
    eng.workers = {"omdb": type("W", (), {"client": rc})()}
    eng._fill_tmdb_ratings({"imdb_id": "tt1"})     # hits the limit → latches (no raise out)
    assert getattr(eng, "_omdb_blocked", False) is True
    eng._fill_tmdb_ratings({"imdb_id": "tt2"})     # short-circuits before calling OMDb
    assert rc.n == 1


# ── wiring contract ───────────────────────────────────────────────────────────
def test_seeded_before_the_airing_automation():
    import core.automation_engine as ae
    order = [a.get("action_type") for a in ae.SYSTEM_AUTOMATIONS]
    assert "video_refresh_airing_schedules" in order
    # must run BEFORE the airing read so the calendar is fresh when it runs
    assert order.index("video_refresh_airing_schedules") < order.index("video_add_airing_episodes")
