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
        fetch_airing=lambda today: rows, add_episodes=add, today_fn=lambda: "2026-06-21")

    assert res["status"] == "completed"
    assert res["episodes_added"] == 3        # 2 of Widows Bay + 1 of Another Show
    assert res["shows"] == 2
    assert (1, "Widows Bay", 2) in added and (2, "Another Show", 1) in added


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
