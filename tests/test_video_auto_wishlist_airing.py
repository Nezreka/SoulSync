"""Sonarr-style 'wishlist today's airings' automation handler — pure logic with the
calendar read + wishlist write + catch-up bookmark injected, so it runs without a
DB or media server.

Catch-up (Boulder's Sunday gap): each run covers (bookmark+1 .. today), capped at
CATCHUP_MAX_DAYS, so days a slept-through 01:00 trigger skipped heal on the next
run — while each day is only ever offered ONCE (removals never boomerang).
"""

from __future__ import annotations

from core.automation.handlers.video_auto_wishlist_airing import (
    CATCHUP_MAX_DAYS,
    auto_video_add_airing_episodes,
    compute_catchup_window,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _row(tid, title, s, e, owned=False):
    return {"show_tmdb_id": tid, "show_id": tid * 100, "show_title": title, "season_number": s,
            "episode_number": e, "title": "Ep", "air_date": "2026-06-21", "has_file": owned}


def _run(config=None, deps=None, **overrides):
    """Invoke the handler with hermetic defaults for every seam — the production
    defaults reach the real video DB (api.video), which tests must never touch."""
    kw = dict(
        fetch_airing=lambda start, end: [],
        add_episodes=lambda *a, **k: 0,
        today_fn=lambda: "2026-06-21",
        season_meta=lambda *a: None,
        get_bookmark=lambda: None,
        set_bookmark=lambda d: None,
        # unowned-follow pass: hermetic by default, the production seams reach
        # the real video DB + TMDB
        unowned_follows=lambda: [],
        tmdb_airings=lambda tid, s, e: {'poster_url': None, 'episodes': []},
    )
    kw.update(overrides)
    return auto_video_add_airing_episodes(
        config or {"_automation_id": "a", "prune_ended": False}, deps or _Deps(), **kw)


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

    res = _run(fetch_airing=lambda s, e: rows, add_episodes=add)

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

    _run(fetch_airing=lambda s, e: rows, add_episodes=add, season_meta=season_meta)
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

    _run(fetch_airing=lambda s, e: rows, add_episodes=add)
    ep = captured["eps"][0]
    assert ep["overview"] == "db synopsis"
    assert ep["still_url"] == "/library/metadata/9/thumb/1"


def test_queries_the_calendar_for_today():
    seen = {}

    def fetch(start, end):
        seen["window"] = (start, end)
        return []

    _run(fetch_airing=fetch)
    assert seen["window"] == ("2026-06-21", "2026-06-21")     # start == end == today


def test_nothing_airing_is_a_clean_noop():
    res = _run()
    assert res["status"] == "completed" and res["episodes_added"] == 0


def test_error_is_caught_and_reported():
    def boom(start, end):
        raise RuntimeError("calendar down")

    deps = _Deps()
    res = _run(deps=deps, fetch_airing=boom)
    assert res["status"] == "error" and "calendar down" in res["error"]
    assert any(p.get("status") == "error" for p in deps.progress)


# ── catch-up window (Boulder's Sunday gap) ───────────────────────────────────


def test_window_no_bookmark_is_today_only():
    assert compute_catchup_window("2026-06-21", None) == "2026-06-21"
    assert compute_catchup_window("2026-06-21", "") == "2026-06-21"
    assert compute_catchup_window("2026-06-21", "junk") == "2026-06-21"


def test_window_normal_night_collapses_to_today():
    # bookmark = yesterday → window starts today: identical to pre-catch-up behavior
    assert compute_catchup_window("2026-06-21", "2026-06-20") == "2026-06-21"


def test_window_after_gap_covers_missed_days():
    # gone Thu–Sun (last covered Wed 17th) → window = Thu 18th .. today
    assert compute_catchup_window("2026-06-21", "2026-06-17") == "2026-06-18"


def test_window_is_capped():
    # bookmark a month old → clamp to (today - CATCHUP_MAX_DAYS + 1)
    assert CATCHUP_MAX_DAYS == 7
    assert compute_catchup_window("2026-06-21", "2026-05-01") == "2026-06-15"


def test_window_future_or_same_day_bookmark_clamps_to_today():
    assert compute_catchup_window("2026-06-21", "2026-06-21") == "2026-06-21"
    assert compute_catchup_window("2026-06-21", "2026-06-25") == "2026-06-21"


def test_gap_run_fetches_window_and_advances_bookmark():
    seen = {}
    marks = []

    def fetch(start, end):
        seen["window"] = (start, end)
        return [_row(1, "Widows Bay", 1, 1)]

    res = _run(fetch_airing=fetch, add_episodes=lambda *a, **k: 1,
               get_bookmark=lambda: "2026-06-17", set_bookmark=marks.append)
    assert seen["window"] == ("2026-06-18", "2026-06-21")   # the missed days + today
    assert marks == ["2026-06-21"]                          # bookmark advanced to today
    assert res["episodes_added"] == 1


def test_failed_run_does_not_advance_bookmark():
    marks = []

    def boom(start, end):
        raise RuntimeError("calendar down")

    res = _run(fetch_airing=boom, get_bookmark=lambda: "2026-06-17", set_bookmark=marks.append)
    assert res["status"] == "error"
    assert marks == []                       # next run re-covers the same window


def test_bookmark_read_failure_degrades_to_today_only():
    seen = {}

    def fetch(start, end):
        seen["window"] = (start, end)
        return []

    def bad_bookmark():
        raise RuntimeError("db hiccup")

    res = _run(fetch_airing=fetch, get_bookmark=bad_bookmark)
    assert res["status"] == "completed"
    assert seen["window"] == ("2026-06-21", "2026-06-21")


def test_bookmark_write_failure_does_not_fail_the_run():
    def bad_write(day):
        raise RuntimeError("db hiccup")

    res = _run(set_bookmark=bad_write)
    assert res["status"] == "completed"


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
    res = _run(config={"_automation_id": "a"},
               prune_follows=lambda: [{"tmdb_id": 7, "title": "Old", "status": "Ended"}],
               show_status=lambda t: None, remove_show=removed.append)
    assert res["status"] == "completed" and res["shows_pruned"] == 1 and removed == [7]


def test_airing_handler_can_disable_the_prune():
    called = {"n": 0}
    _run(prune_follows=lambda: called.update(n=called["n"] + 1) or [])
    assert called["n"] == 0                          # prune skipped entirely


# ── follows you don't own yet ────────────────────────────────────────────────
# The calendar is built off the episodes table, which only holds LIBRARY shows.
# So a show you follow from a TMDB page never produced a single wishlist row:
# you followed it, it aired, nothing happened. Boulder's live install had three
# (Elle, The Polygamist, The Smurfs). Sonarr monitors a series whether or not
# you have files yet, so we ask TMDB directly for those.
def test_a_followed_show_you_dont_own_still_gets_its_airings():
    added = []

    def add(tid, title, eps, library_id=None, poster_url=None):
        added.append((tid, title, len(eps), library_id, poster_url))
        return len(eps)

    res = _run(
        fetch_airing=lambda s, e: [],                       # calendar knows nothing about it
        unowned_follows=lambda: [{"tmdb_id": 42, "title": "Elle", "library_id": None}],
        tmdb_airings=lambda tid, s, e: {
            "poster_url": "https://image.tmdb.org/p/w500/elle.jpg",
            "episodes": [{"season_number": 1, "episode_number": 4, "title": "Ep 4",
                          "air_date": "2026-06-21"}]},
        add_episodes=add)

    assert res["episodes_added"] == 1 and res["shows"] == 1
    # No library_id (not owned) so no poster proxy — but TMDB's own poster MUST
    # come through. A null poster_url renders the wishlist row as an initials
    # orb that reads as "not matched" (the 94e06f2d symptom).
    assert added == [(42, "Elle", 1, None, "https://image.tmdb.org/p/w500/elle.jpg")]


def test_the_tmdb_pass_uses_the_same_catchup_window():
    seen = []
    _run(fetch_airing=lambda s, e: [],
         today_fn=lambda: "2026-06-21",
         get_bookmark=lambda: "2026-06-18",
         unowned_follows=lambda: [{"tmdb_id": 42, "title": "Elle"}],
         tmdb_airings=lambda tid, s, e: seen.append((s, e)) or {"episodes": []})
    assert seen == [("2026-06-19", "2026-06-21")]


def test_the_calendar_pass_wins_for_a_show_in_both():
    """A show can be owned AND explicitly followed. Don't look it up twice or
    overwrite the library_id the calendar gave us."""
    calls = []
    added = []
    _run(fetch_airing=lambda s, e: [_row(1, "Widows Bay", 1, 1)],
         unowned_follows=lambda: [{"tmdb_id": 1, "title": "Widows Bay"}],
         tmdb_airings=lambda tid, s, e: calls.append(tid) or {"episodes": []},
         add_episodes=lambda tid, t, eps, lib=None, p=None: added.append((tid, lib)) or len(eps))
    assert calls == []                       # never re-queried
    assert added == [(1, 100)]               # kept the library id


def test_one_bad_tmdb_lookup_does_not_sink_the_run():
    def boom(tid, s, e):
        if tid == 1:
            raise RuntimeError("tmdb down")
        return {"poster_url": None,
                "episodes": [{"season_number": 1, "episode_number": 1, "air_date": "2026-06-21"}]}

    res = _run(fetch_airing=lambda s, e: [],
               unowned_follows=lambda: [{"tmdb_id": 1, "title": "Bad"},
                                        {"tmdb_id": 2, "title": "Good"}],
               tmdb_airings=boom,
               add_episodes=lambda *a, **k: 1)
    assert res["status"] == "completed" and res["episodes_added"] == 1


def test_a_broken_follow_list_still_lets_the_calendar_pass_land():
    def boom():
        raise RuntimeError("db gone")

    res = _run(fetch_airing=lambda s, e: [_row(1, "Widows Bay", 1, 1)],
               unowned_follows=boom,
               add_episodes=lambda *a, **k: 1)
    assert res["status"] == "completed" and res["episodes_added"] == 1


# ── the TMDB window lookup itself ───────────────────────────────────────────
class _Engine:
    def __init__(self, seasons, episodes):
        self._seasons = seasons
        self._episodes = episodes
        self.season_calls = []

    def tmdb_detail(self, kind, tid):
        return {"poster_url": "show.jpg",
                "seasons": [{"season_number": n} for n in self._seasons]}

    def tmdb_season(self, tid, sn):
        self.season_calls.append(sn)
        return {"poster_url": "p%d" % sn, "episodes": self._episodes.get(sn, [])}


def test_airing_lookup_only_pulls_the_newest_seasons():
    """A show airing now is airing its newest season. Pulling ten seasons of
    history every night would be a TMDB call per season per show."""
    from core.video.monitor_policy import episodes_airing_between
    eng = _Engine([0, 1, 2, 3, 4], {})
    episodes_airing_between(eng, 5, "2026-06-19", "2026-06-21")
    assert eng.season_calls == [3, 4]          # specials excluded, newest two only


def test_airing_lookup_filters_to_the_window():
    from core.video.monitor_policy import episodes_airing_between
    eng = _Engine([1], {1: [
        {"episode_number": 1, "air_date": "2026-06-18"},      # before
        {"episode_number": 2, "air_date": "2026-06-20"},      # in
        {"episode_number": 3, "air_date": "2026-06-25"},      # after (unaired)
        {"episode_number": 4, "air_date": None},              # undated
        {"episode_number": None, "air_date": "2026-06-20"},   # junk
    ]})
    out = episodes_airing_between(eng, 5, "2026-06-19", "2026-06-21")["episodes"]
    assert [e["episode_number"] for e in out] == [2]
    assert out[0]["season_poster_url"] == "p1"


def test_airing_lookup_degrades_when_tmdb_is_down():
    from core.video.monitor_policy import episodes_airing_between

    class _Dead:
        def tmdb_detail(self, *a):
            raise RuntimeError("down")

    assert episodes_airing_between(_Dead(), 5, "2026-06-19", "2026-06-21") == {
        "poster_url": None, "episodes": []}


def test_a_specials_only_show_still_gets_looked_up():
    """Season 0 is excluded from "newest seasons" — but a show whose episodes
    are ALL specials (Critical Role) would then never be looked up at all. The
    engine's _latest_seasons falls back for exactly this; so must we."""
    from core.video.monitor_policy import episodes_airing_between, latest_season_numbers
    assert latest_season_numbers({"seasons": [{"season_number": 0}]}) == [0]
    eng = _Engine([0], {0: [{"episode_number": 221, "air_date": "2026-06-20"}]})
    out = episodes_airing_between(eng, 5, "2026-06-19", "2026-06-21")
    assert [e["episode_number"] for e in out["episodes"]] == [221]


def test_the_show_poster_comes_back_for_filing():
    from core.video.monitor_policy import episodes_airing_between
    eng = _Engine([1], {1: [{"episode_number": 1, "air_date": "2026-06-20"}]})
    assert episodes_airing_between(eng, 5, "2026-06-19", "2026-06-21")["poster_url"] == "show.jpg"


def test_dedup_survives_a_renamed_show():
    """The calendar keys off shows.title, this pass off video_watchlist.title.
    Rename a show in the Manage panel and those drift — keying the dedup on the
    title would look it up twice and add it twice."""
    calls = []
    added = []
    _run(fetch_airing=lambda s, e: [_row(1, "Widow's Bay", 1, 1)],
         unowned_follows=lambda: [{"tmdb_id": 1, "title": "Widows Bay (2026)"}],   # drifted
         tmdb_airings=lambda tid, s, e: calls.append(tid) or {"episodes": []},
         add_episodes=lambda tid, t, eps, lib=None, p=None: added.append((tid, lib)) or len(eps))
    assert calls == []                       # not re-queried despite the different title
    assert added == [(1, 100)]               # one add, keeping the library id
