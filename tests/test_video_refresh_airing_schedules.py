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


# ── wiring contract ───────────────────────────────────────────────────────────
def test_seeded_before_the_airing_automation():
    import core.automation_engine as ae
    order = [a.get("action_type") for a in ae.SYSTEM_AUTOMATIONS]
    assert "video_refresh_airing_schedules" in order
    # must run BEFORE the airing read so the calendar is fresh when it runs
    assert order.index("video_refresh_airing_schedules") < order.index("video_add_airing_episodes")
