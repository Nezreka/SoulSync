"""Sonarr-style 'wishlist aired episodes' automation handler — pure logic with the
calendar read + wishlist write + offer receipts injected, so it runs without a
DB or media server.

Every run looks back LOOKBACK_DAYS and offers each episode once (a receipt per
episode). That replaced a once-a-day (bookmark + 1 .. today) window, which lost
episodes when the 01:00 slot was skipped by a restart and never offered one that
reached TMDB on or after its own air date.
"""

from __future__ import annotations

from core.automation.handlers.video_auto_wishlist_airing import (
    LOOKBACK_DAYS,
    auto_video_add_airing_episodes,
    lookback_start,
    seed_boundary,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _row(tid, title, s, e, owned=False, monitored=True):
    return {"show_tmdb_id": tid, "show_id": tid * 100, "show_title": title, "season_number": s,
            "episode_number": e, "title": "Ep", "air_date": "2026-06-21", "has_file": owned,
            "monitored": 1 if monitored else 0}


def _run(config=None, deps=None, **overrides):
    """Invoke the handler with hermetic defaults for every seam — the production
    defaults reach the real video DB (api.video), which tests must never touch."""
    kw = dict(
        fetch_airing=lambda start, end: [],
        add_episodes=lambda *a, **k: 0,
        today_fn=lambda: "2026-06-21",
        season_meta=lambda *a: None,
        get_bookmark=lambda: None,
        get_offered=lambda keys: set(),
        mark_offered=lambda keys: None,
        is_seeded=lambda: True,
        set_seeded=lambda: None,
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
             "title": "Ep", "air_date": "2026-06-21", "has_file": False, "monitored": 1,
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
             "has_file": False, "monitored": 1, "overview": "db synopsis", "still_url": "/library/metadata/9/thumb/1"}]
    captured = {}

    def add(tid, title, eps, library_id=None, poster_url=None):
        captured["eps"] = eps
        return len(eps)

    _run(fetch_airing=lambda s, e: rows, add_episodes=add)
    ep = captured["eps"][0]
    assert ep["overview"] == "db synopsis"
    assert ep["still_url"] == "/library/metadata/9/thumb/1"


def test_queries_the_calendar_for_the_last_week():
    seen = {}

    def fetch(start, end):
        seen["window"] = (start, end)
        return []

    _run(fetch_airing=fetch)
    assert seen["window"] == ("2026-06-15", "2026-06-21")     # a week ending today


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


# ── lookback + receipts ──────────────────────────────────────────────────


class _Receipts:
    """an in-memory video_airing_offers table, for runs that follow each other."""

    def __init__(self, seeded=True):
        self.keys = set()
        self.seeded = seeded

    def seams(self):
        return dict(get_offered=lambda keys: {k for k in keys if k in self.keys},
                    mark_offered=lambda keys: self.keys.update(keys),
                    is_seeded=lambda: self.seeded,
                    set_seeded=lambda: setattr(self, "seeded", True))


def _dated(tid, title, s, e, day):
    r = _row(tid, title, s, e)
    r["air_date"] = day
    return r


def test_lookback_is_a_week_ending_today():
    assert LOOKBACK_DAYS == 7
    assert lookback_start("2026-06-21") == "2026-06-15"
    assert lookback_start("2026-03-02") == "2026-02-24"


def test_an_offered_episode_is_never_added_twice():
    rec = _Receipts()
    added = []
    fetch = lambda s, e: [_row(1, "Widows Bay", 1, 1)]
    add = lambda tid, title, eps, *a: added.append([x["episode_number"] for x in eps]) or len(eps)
    _run(fetch_airing=fetch, add_episodes=add, **rec.seams())
    res = _run(fetch_airing=fetch, add_episodes=add, **rec.seams())
    assert added == [[1]]
    assert res["episodes_added"] == 0
    assert (1, 1, 1) in rec.keys


def test_an_episode_removed_from_the_wishlist_does_not_come_back():
    # the old window re-offered a whole week whenever its bookmark write failed,
    # and a deleted episode came back. the receipt outlives the wishlist row.
    rec = _Receipts()
    rec.keys.add((1, 1, 1))
    added = []
    _run(fetch_airing=lambda s, e: [_row(1, "Widows Bay", 1, 1)],
         add_episodes=lambda *a: added.append(a) or 1, **rec.seams())
    assert added == []


def test_an_episode_tmdb_learned_about_late_is_still_offered():
    """Boulder, Oct 27 2026: 41 aired episodes sat missing. The refresh runs at
    23:00 and the airing run at 01:00 covered only (bookmark + 1 .. today), so an
    episode that reached the episodes table on or after its own air date was
    behind the bookmark before anything could read it. A week of lookback with
    receipts offers it on the next run."""
    rec = _Receipts()
    added = []
    rows = [_dated(1, "Lupin", 4, 1, "2026-06-19")]   # aired two days ago, never offered
    res = _run(fetch_airing=lambda s, e: [r for r in rows if s <= r["air_date"] <= e],
               add_episodes=lambda tid, t, eps, *a: added.extend(eps) or len(eps), **rec.seams())
    assert [e["episode_number"] for e in added] == [1]
    assert res["episodes_added"] == 1


def test_a_failed_write_leaves_the_show_for_the_next_run():
    rec = _Receipts()

    def add(tid, *a):
        if tid == 2:
            raise RuntimeError("db hiccup")
        return 1

    res = _run(fetch_airing=lambda s, e: [_row(1, "A", 1, 1), _row(2, "B", 1, 1)],
               add_episodes=add, **rec.seams())
    assert res["status"] == "error"
    assert (1, 1, 1) in rec.keys and (2, 1, 1) not in rec.keys


def test_seed_boundary():
    assert seed_boundary("2026-06-21", "2026-06-20") == "2026-06-20"
    assert seed_boundary("2026-06-21", "2026-06-17") == "2026-06-17"
    # no usable bookmark = fresh install, which never backfilled history either
    assert seed_boundary("2026-06-21", None) == "2026-06-20"
    assert seed_boundary("2026-06-21", "junk") == "2026-06-20"
    # a bookmark at or past today still leaves today to be offered
    assert seed_boundary("2026-06-21", "2026-06-21") == "2026-06-20"


def test_the_first_run_does_not_re_offer_what_the_bookmark_covered():
    # the old rules already offered up to the bookmark, and the user may have
    # deleted some of those since. the first receipt run marks them, adds the rest.
    rec = _Receipts(seeded=False)
    added = []
    rows = [_dated(1, "A", 1, 1, "2026-06-16"), _dated(1, "A", 1, 2, "2026-06-18"),
            _dated(1, "A", 1, 3, "2026-06-21")]
    _run(fetch_airing=lambda s, e: rows, get_bookmark=lambda: "2026-06-18",
         add_episodes=lambda tid, t, eps, *a: added.extend(eps) or len(eps), **rec.seams())
    assert [e["episode_number"] for e in added] == [3]
    assert {(1, 1, 1), (1, 1, 2), (1, 1, 3)} <= rec.keys
    assert rec.seeded


def test_the_first_run_on_a_fresh_install_does_not_backfill_history():
    rec = _Receipts(seeded=False)
    added = []
    rows = [_dated(1, "A", 1, 1, "2026-06-16"), _dated(1, "A", 1, 2, "2026-06-21")]
    _run(fetch_airing=lambda s, e: rows,
         add_episodes=lambda tid, t, eps, *a: added.extend(eps) or len(eps), **rec.seams())
    assert [e["episode_number"] for e in added] == [2]


def test_seeding_happens_once():
    rec = _Receipts(seeded=False)
    _run(get_bookmark=lambda: "2026-06-18", **rec.seams())
    assert rec.seeded
    rows = [_dated(1, "A", 1, 1, "2026-06-16")]       # learned about late, after the seed
    added = []
    _run(fetch_airing=lambda s, e: rows, get_bookmark=lambda: "2026-06-18",
         add_episodes=lambda tid, t, eps, *a: added.extend(eps) or len(eps), **rec.seams())
    assert [e["episode_number"] for e in added] == [1]


def test_an_unreadable_bookmark_seeds_like_a_fresh_install():
    rec = _Receipts(seeded=False)

    def bad():
        raise RuntimeError("db hiccup")

    res = _run(get_bookmark=bad, fetch_airing=lambda s, e: [_dated(1, "A", 1, 1, "2026-06-16")],
               **rec.seams())
    assert res["status"] == "completed" and res["episodes_added"] == 0
    assert (1, 1, 1) in rec.keys


def test_tmdb_pass_episodes_get_receipts_too():
    rec = _Receipts()
    eps = [{"season_number": 2, "episode_number": 4, "air_date": "2026-06-21"}]
    kw = dict(unowned_follows=lambda: [{"tmdb_id": 7, "title": "Unowned"}],
              tmdb_airings=lambda tid, s, e: {"poster_url": "p", "episodes": eps},
              add_episodes=lambda tid, t, e, *a: len(e))
    first = _run(**kw, **rec.seams())
    second = _run(**kw, **rec.seams())
    assert first["episodes_added"] == 1 and second["episodes_added"] == 0
    assert (7, 2, 4) in rec.keys


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


def test_the_tmdb_pass_uses_the_same_lookback_window():
    seen = []
    _run(fetch_airing=lambda s, e: [],
         today_fn=lambda: "2026-06-21",
         unowned_follows=lambda: [{"tmdb_id": 42, "title": "Elle"}],
         tmdb_airings=lambda tid, s, e: seen.append((s, e)) or {"episodes": []})
    assert seen == [("2026-06-15", "2026-06-21")]


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
    assert eng.season_calls == [0, 3, 4]       # newest two + specials (new specials must be picked up)


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
    """A show whose episodes are ALL specials (Critical Role) falls back to
    season 0 — otherwise it would never be looked up at all. The engine's
    _latest_seasons falls back for exactly this; so must we."""
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


def test_unmonitored_episodes_are_not_wishlisted():
    # unmonitored is a deliberate "stop hunting this" — the calendar treats it
    # as a decision, not a gap. the airing run must not re-create the work.
    rows = [
        _row(1, "Widows Bay", 1, 1),
        _row(1, "Widows Bay", 1, 2, monitored=False),   # user unmonitored → skipped
        _row(1, "Widows Bay", 1, 3, monitored=False),   # user unmonitored → skipped
    ]
    added = []

    def add(tid, title, eps, library_id=None, poster_url=None):
        added.append((tid, len(eps)))
        return len(eps)

    res = _run(fetch_airing=lambda s, e: rows, add_episodes=add)
    assert res["status"] == "completed"
    assert res["episodes_added"] == 1
    assert added == [(1, 1)]


# ── the real receipt table ──────────────────────────────────────────────────


def test_receipts_round_trip_on_a_real_db(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.mark_airing_offered(1, [(10, 1, 1), (10, 1, 2), (11, 3, 4)])
    db.mark_airing_offered(1, [(10, 1, 1)])                       # idempotent
    assert db.airing_offered_keys(1, [(10, 1, 1), (10, 1, 3), (11, 3, 4)]) == {(10, 1, 1), (11, 3, 4)}
    # per profile: another profile's offers are not this one's
    assert db.airing_offered_keys(2, [(10, 1, 1)]) == set()
    assert db.airing_offered_keys(1, []) == set()


def test_old_receipts_age_out(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.mark_airing_offered(1, [(10, 1, 1), (10, 1, 2)])
    conn = db._get_connection()
    conn.execute("UPDATE video_airing_offers SET offered_at=datetime('now', '-90 days') "
                 "WHERE episode_number=1")
    conn.commit()
    conn.close()
    db.mark_airing_offered(1, [])
    assert db.airing_offered_keys(1, [(10, 1, 1), (10, 1, 2)]) == {(10, 1, 2)}
