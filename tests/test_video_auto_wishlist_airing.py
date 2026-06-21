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
    return {"show_tmdb_id": tid, "show_title": title, "season_number": s,
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

    def add(tid, title, eps):
        added.append((tid, title, len(eps)))
        return len(eps)

    res = auto_video_add_airing_episodes(
        {"_automation_id": "a1"}, _Deps(),
        fetch_airing=lambda today: rows, add_episodes=add, today_fn=lambda: "2026-06-21",
        season_meta=lambda *a: None)

    assert res["status"] == "completed"
    assert res["episodes_added"] == 3        # 2 of Widows Bay + 1 of Another Show
    assert res["shows"] == 2
    assert (1, "Widows Bay", 2) in added and (2, "Another Show", 1) in added


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

    def add(tid, title, eps):
        captured["eps"] = eps
        return len(eps)

    auto_video_add_airing_episodes({"_automation_id": "a"}, _Deps(),
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

    def add(tid, title, eps):
        captured["eps"] = eps
        return len(eps)

    auto_video_add_airing_episodes({"_automation_id": "a"}, _Deps(),
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

    auto_video_add_airing_episodes({"_automation_id": "a"}, _Deps(),
                                   fetch_airing=fetch, add_episodes=lambda *a: 0,
                                   today_fn=lambda: "2026-06-21")
    assert seen["today"] == "2026-06-21"     # start == end == today


def test_nothing_airing_is_a_clean_noop():
    res = auto_video_add_airing_episodes({"_automation_id": "a"}, _Deps(),
                                         fetch_airing=lambda t: [], add_episodes=lambda *a: 0,
                                         today_fn=lambda: "2026-06-21")
    assert res["status"] == "completed" and res["episodes_added"] == 0


def test_error_is_caught_and_reported():
    def boom(today):
        raise RuntimeError("calendar down")

    deps = _Deps()
    res = auto_video_add_airing_episodes({"_automation_id": "a"}, deps,
                                         fetch_airing=boom, add_episodes=lambda *a: 0)
    assert res["status"] == "error" and "calendar down" in res["error"]
    assert any(p.get("status") == "error" for p in deps.progress)
