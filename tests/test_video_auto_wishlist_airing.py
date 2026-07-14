"""Sonarr-style 'wishlist today's airings' automation handler — pure logic with the
calendar read + wishlist write injected, so it runs without a DB or media server."""

from __future__ import annotations

from core.automation.handlers.video_auto_wishlist_airing import auto_video_add_airing_episodes


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _row(tid, title, s, e, owned=False):
    return {"show_tmdb_id": tid, "show_id": tid * 100, "show_title": title, "season_number": s,
            "episode_number": e, "title": "Ep", "air_date": "2026-06-21", "has_file": owned}


def test_adds_unowned_airings_grouped_by_show():
    rows = [
        _row(1, "Widows Bay", 1, 1),
        _row(1, "Widows Bay", 1, 2),
        _row(2, "Another Show", 3, 5),
        _row(1, "Widows Bay", 1, 3, owned=True),   # already owned → skipped
        {"show_title": "No id", "season_number": 1, "episode_number": 1},   # no tmdb id → skipped
    ]
    added = []

    def add(tid, title, eps, library_id=None, poster_url=None):
        added.append((tid, title, len(eps), library_id, poster_url))
        return len(eps)

    res = auto_video_add_airing_episodes(
        {"_automation_id": "a1", "prune_ended": False}, _Deps(),
        fetch_airing=lambda today: rows, add_episodes=add, today_fn=lambda: "2026-06-21",
        season_meta=lambda *a: None)

    assert res["status"] == "completed"
    assert res["episodes_added"] == 3        # 2 of Widows Bay + 1 of Another Show
    assert res["shows"] == 2
    # the show's library_id (show_id) + poster proxy are carried so the wishlist
    # matches the show and the orb renders the show poster (like a manual add)
    assert (1, "Widows Bay", 2, 100, "/api/video/poster/show/100") in added
    assert (2, "Another Show", 1, 200, "/api/video/poster/show/200") in added


def test_uses_tmdb_season_metadata_like_a_manual_add():
    # the SAME TMDB source the manual 'add to wishlist' uses — absolute still + overview
    # + season poster — preferred over the patchy DB values.
    rows = [{"show_tmdb_id": 5, "show_title": "Y", "season_number": 2, "episode_number": 3,
             "title": "Ep", "air_date": "2026-06-21", "has_file": False,
             "overview": "db overview", "still_url": "/db/still"}]

    def season_meta(tid, sn):
        assert (tid, sn) == (5, 2)
        return {"poster_url": "https://img/tmdb/s2.jpg",
                "episodes": [{"episode_number": 3, "overview": "TMDB overview",
                              "still_url": "https://img/tmdb/s2e3.jpg"}]}

    captured = {}

    def add(tid, title, eps, library_id=None, poster_url=None):
        captured["eps"] = eps
        return len(eps)

    auto_video_add_airing_episodes({"_automation_id": "a", "prune_ended": False}, _Deps(),
                                   fetch_airing=lambda t: rows, add_episodes=add,
                                   today_fn=lambda: "2026-06-21", season_meta=season_meta)
    ep = captured["eps"][0]
    assert ep["overview"] == "TMDB overview"                 # TMDB preferred over DB
    assert ep["still_url"] == "https://img/tmdb/s2e3.jpg"
    assert ep["season_poster_url"] == "https://img/tmdb/s2.jpg"


def test_falls_back_to_db_values_when_tmdb_unavailable():
    # if the TMDB fetch returns nothing, still carry the calendar/DB overview + still
    rows = [{"show_tmdb_id": 1, "show_title": "X", "season_number": 1, "episode_number": 2,
             "has_file": False, "overview": "db synopsis", "still_url": "/library/metadata/9/thumb/1"}]
    captured = {}

    def add(tid, title, eps, library_id=None, poster_url=None):
        captured["eps"] = eps
        return len(eps)

    auto_video_add_airing_episodes({"_automation_id": "a", "prune_ended": False}, _Deps(),
                                   fetch_airing=lambda t: rows, add_episodes=add,
                                   today_fn=lambda: "2026-06-21", season_meta=lambda *a: None)
    ep = captured["eps"][0]
    assert ep["overview"] == "db synopsis"
    assert ep["still_url"] == "/library/metadata/9/thumb/1"


def test_queries_the_calendar_for_today():
    seen = {}

    def fetch(today):
        seen["today"] = today
        return []

    auto_video_add_airing_episodes({"_automation_id": "a", "prune_ended": False}, _Deps(),
                                   fetch_airing=fetch, add_episodes=lambda *a: 0,
                                   today_fn=lambda: "2026-06-21")
    assert seen["today"] == "2026-06-21"     # start == end == today


def test_nothing_airing_is_a_clean_noop():
    res = auto_video_add_airing_episodes({"_automation_id": "a", "prune_ended": False}, _Deps(),
                                         fetch_airing=lambda t: [], add_episodes=lambda *a: 0,
                                         today_fn=lambda: "2026-06-21")
    assert res["status"] == "completed" and res["episodes_added"] == 0


def test_error_is_caught_and_reported():
    def boom(today):
        raise RuntimeError("calendar down")

    deps = _Deps()
    res = auto_video_add_airing_episodes({"_automation_id": "a", "prune_ended": False}, deps,
                                         fetch_airing=boom, add_episodes=lambda *a: 0)
    assert res["status"] == "error" and "calendar down" in res["error"]
    assert any(p.get("status") == "error" for p in deps.progress)


# ── watchlist hygiene: prune ended/canceled follows ─────────────────────────
from core.automation.handlers.video_auto_wishlist_airing import prune_ended_show_follows  # noqa: E402


def test_prune_removes_ended_and_canceled_follows():
    follows = [
        {"tmdb_id": 1, "title": "Returning Show", "status": "Returning Series"},
        {"tmdb_id": 2, "title": "Done Show", "status": "Ended"},
        {"tmdb_id": 3, "title": "Axed Show", "status": "Canceled"},
        {"tmdb_id": 4, "title": "Live Show", "status": "In Production"},
    ]
    removed = []
    n = prune_ended_show_follows(_Deps(), "a", fetch_follows=lambda: follows,
                                 show_status=lambda t: None, remove_show=removed.append)
    assert n == 2 and removed == [2, 3]            # only Ended + Canceled


def test_prune_looks_up_status_for_tmdb_only_follows():
    # no local status → fetch from TMDB; ended → prune
    follows = [{"tmdb_id": 9, "title": "TMDB-only", "status": None}]
    removed = []
    prune_ended_show_follows(_Deps(), "a", fetch_follows=lambda: follows,
                             show_status=lambda t: "Ended", remove_show=removed.append)
    assert removed == [9]


def test_prune_never_removes_on_unknown_status_or_lookup_error():
    def _boom(t):
        raise RuntimeError("tmdb down")
    follows = [{"tmdb_id": 1, "title": "Unknown", "status": None},   # lookup returns None
               {"tmdb_id": 2, "title": "Errored", "status": None}]   # lookup raises
    removed = []
    n = prune_ended_show_follows(
        _Deps(), "a", fetch_follows=lambda: follows,
        show_status=lambda t: (_boom(t) if t == 2 else None), remove_show=removed.append)
    assert n == 0 and removed == []                # uncertainty → keep


def test_airing_handler_runs_the_prune_pass():
    removed = []
    res = auto_video_add_airing_episodes(
        {"_automation_id": "a"}, _Deps(),
        fetch_airing=lambda t: [], add_episodes=lambda *a, **k: 0, today_fn=lambda: "2026-06-21",
        prune_follows=lambda: [{"tmdb_id": 7, "title": "Old", "status": "Ended"}],
        show_status=lambda t: None, remove_show=removed.append)
    assert res["status"] == "completed" and res["shows_pruned"] == 1 and removed == [7]


def test_airing_handler_can_disable_the_prune():
    called = {"n": 0}
    auto_video_add_airing_episodes(
        {"_automation_id": "a", "prune_ended": False}, _Deps(),
        fetch_airing=lambda t: [], add_episodes=lambda *a, **k: 0, today_fn=lambda: "2026-06-21",
        prune_follows=lambda: called.update(n=called["n"] + 1) or [])
    assert called["n"] == 0                          # prune skipped entirely
