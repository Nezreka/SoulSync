"""The calendar redesign's backend (Boulder, Oct 27 2026: "the calendar itself
feels weak. not very descriptive ... neither shows the state of the item").

Covers what the new page reads: live download progress on a card, movie states,
season 0 hidden unless asked for (Natural High's specials alone filled a week),
the automation health line, a six-week window for the month view, the one-click
"wishlist the missing ones", and an iCal feed calendar apps can actually fetch
(the old url 401'd on any install with profiles, a login or a PIN).
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")
    # the health line reads the app db; tests never touch the real one
    monkeypatch.setattr("core.automation.system_status.system_automation_status",
                        lambda actions, database=None: {a: {"enabled": True, "last_run": "x"}
                                                        for a in actions})
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    yield app.test_client(), db
    videoapi._video_db = None


D = lambda n: (date.today() + timedelta(days=n)).isoformat()   # noqa: E731


def _show(db, tmdb_id=1396, title="Show", episodes=None, season=1, server_id="s1"):
    return db.upsert_show_tree("plex", {
        "server_id": server_id, "title": title, "tmdb_id": tmdb_id,
        "seasons": [{"season_number": season, "episodes": episodes or []}]})


def _cal(c, **q):
    q.setdefault("start", D(-10))
    q.setdefault("days", 30)
    q.setdefault("scope", "all")
    return c.get("/api/video/calendar?" + "&".join("%s=%s" % kv for kv in q.items())).get_json()


def _sql(db, *stmts):
    conn = db._get_connection()
    for s in stmts:
        conn.execute(*s) if isinstance(s, tuple) else conn.execute(s)
    conn.commit()
    conn.close()


# ── the window ──────────────────────────────────────────────────────────────


def test_specials_are_hidden_unless_asked_for(env):
    c, db = env
    db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Show", "tmdb_id": 1396, "seasons": [
            {"season_number": 1, "episodes": [{"episode_number": 1, "title": "Real", "air_date": D(-1)}]},
            {"season_number": 0, "episodes": [
                {"episode_number": 151, "title": "Special", "air_date": D(-1)}]}]})
    body = _cal(c)
    assert [(e["season_number"], e["episode_number"]) for e in body["episodes"]] == [(1, 1)]
    assert body["total"] == 1 and body["specials"] is False
    both = _cal(c, specials=1)
    assert {(e["season_number"], e["episode_number"]) for e in both["episodes"]} == {(1, 1), (0, 151)}
    # the counts follow what is drawn, so the chips never disagree with the grid
    assert sum(x["count"] for x in both["acq_counts"]) == 2


def test_a_six_week_window_is_allowed_for_the_month_view(env):
    c, db = env
    _show(db, episodes=[{"episode_number": 1, "title": "Late", "air_date": D(40)}])
    body = _cal(c, start=D(0), days=42)
    assert body["days"] == 42 and body["total"] == 1
    assert _cal(c, start=D(0), days=90)["days"] == 42


def test_a_downloading_episode_carries_its_progress(env):
    c, db = env
    _show(db, episodes=[{"episode_number": 1, "title": "E1", "air_date": D(-1)}])
    _sql(db, ("INSERT INTO video_downloads (kind, media_source, media_id, status, progress, "
              "eta_seconds, search_ctx) VALUES ('episode','tmdb','1396','downloading',41.6,360,?)",
              (json.dumps({"season": 1, "episode": 1}),)))
    ep = _cal(c)["episodes"][0]
    assert ep["acq"] == "downloading" and ep["progress"] == 42 and ep["eta_seconds"] == 360


def test_the_health_line_rides_the_payload(env):
    c, _db = env
    body = _cal(c)
    assert body["automations"]["airing"]["enabled"] is True
    assert "refresh" in body["automations"]


def test_movies_carry_a_state(env):
    c, db = env
    for tid, title in ((11, "Wanted"), (12, "Failing"), (13, "Fetching")):
        assert db.add_movie_to_wishlist(tid, title)
        db.set_wishlist_release_date(tid, D(1))
    _sql(db, "UPDATE video_wishlist SET status='failed' WHERE tmdb_id=12",
         "INSERT INTO video_downloads (kind, media_source, media_id, status, progress) "
         "VALUES ('movie','tmdb','13','downloading',10)")
    states = {m["tmdb_id"]: m["acq"] for m in _cal(c)["movies"]}
    assert states == {11: "wanted", 12: "failed", 13: "downloading"}


# ── wishlist the missing ones ───────────────────────────────────────────────


def test_wishlist_missing_takes_only_what_nothing_is_handling(env):
    c, db = env
    _show(db, episodes=[
        {"episode_number": 1, "title": "Missing", "air_date": D(-3)},
        {"episode_number": 2, "title": "Owned", "air_date": D(-3),
         "file": {"relative_path": "e2.mkv", "size_bytes": 1}},
        {"episode_number": 3, "title": "Ignored", "air_date": D(-2)},
        {"episode_number": 4, "title": "Future", "air_date": D(2)},
        {"episode_number": 5, "title": "Also missing", "air_date": D(-1)},
    ])
    _sql(db, "UPDATE episodes SET monitored=0 WHERE episode_number=3")
    r = c.post("/api/video/calendar/wishlist-missing",
               json={"start": D(-7), "end": D(6), "scope": "all"}).get_json()
    assert r == {"success": True, "added": 2, "shows": 1}
    wished = db.wishlist_episode_statuses(1, [1396])
    assert set(wished) == {(1396, 1, 1), (1396, 1, 5)}
    # the airing automation won't offer them a second time
    assert db.airing_offered_keys(1, [(1396, 1, 1), (1396, 1, 5)]) == {(1396, 1, 1), (1396, 1, 5)}
    # and the page now counts nothing missing
    assert _cal(c)["needs_action"] == 0


def test_wishlist_missing_refuses_a_bad_window(env):
    c, _db = env
    assert c.post("/api/video/calendar/wishlist-missing", json={}).status_code == 400
    assert c.post("/api/video/calendar/wishlist-missing",
                  json={"start": D(0), "end": D(-5)}).status_code == 400
    # a window wholly in the future has nothing that could be missing
    r = c.post("/api/video/calendar/wishlist-missing", json={"start": D(3), "end": D(9)})
    assert r.get_json() == {"success": True, "added": 0, "shows": 0}


def test_wishlist_missing_needs_download_rights():
    import api.video as videoapi
    from flask import g
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _no_downloads():
        g.profile_id, g.is_admin, g.can_download, g.allowed_sides = 2, False, False, "both"

    r = app.test_client().post("/api/video/calendar/wishlist-missing",
                               json={"start": D(-1), "end": D(0)})
    assert r.status_code == 403


# ── the iCal feed ───────────────────────────────────────────────────────────


def test_feed_url_is_stable_until_rotated(env):
    c, db = env
    a = c.get("/api/video/calendar/feed-url").get_json()["path"]
    assert a == c.get("/api/video/calendar/feed-url").get_json()["path"]
    b = c.post("/api/video/calendar/feed-url").get_json()["path"]
    assert a != b and b.endswith(".ics")
    old_token = a.rsplit("/", 1)[1][:-4]
    assert db.calendar_feed_profile(old_token) is None


def test_the_tokened_feed_serves_that_profiles_calendar(env, monkeypatch):
    c, db = env
    _show(db, title="Feed Show", episodes=[{"episode_number": 1, "title": "E1", "air_date": D(1)}])
    monkeypatch.setattr("core.content_filter._load_profile",
                        lambda pid: {"id": pid, "is_admin": pid == 1, "allowed_sides": "both"})
    path = c.get("/api/video/calendar/feed-url").get_json()["path"]
    r = c.get(path + "?scope=all")
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    assert "SUMMARY:Feed Show S01E01" in r.get_data(as_text=True)


def test_an_unknown_or_malformed_token_gets_nothing(env):
    c, _db = env
    assert c.get("/api/video/calendar/feed/%s.ics" % ("a" * 43)).status_code == 404
    assert c.get("/api/video/calendar/feed/short.ics").status_code == 404


def test_a_profile_that_lost_the_video_side_loses_its_feed(env, monkeypatch):
    c, db = env
    token = db.calendar_feed_token(2)
    monkeypatch.setattr("core.content_filter._load_profile",
                        lambda pid: {"id": pid, "is_admin": False, "allowed_sides": "music"})
    assert c.get("/api/video/calendar/feed/%s.ics" % token).status_code == 404
    monkeypatch.setattr("core.content_filter._load_profile", lambda pid: None)
    assert c.get("/api/video/calendar/feed/%s.ics" % token).status_code == 404


def test_a_kids_feed_keeps_its_rating_cap(env, monkeypatch):
    """There is no session on a feed request, so the request-scoped kids filter
    would read 'no profile' and apply nothing. The token's profile cap must."""
    c, db = env
    _show(db, title="Grown-up Show", episodes=[{"episode_number": 1, "title": "E1", "air_date": D(1)}])
    _sql(db, "UPDATE shows SET content_rating='TV-MA' WHERE tmdb_id=1396")
    assert db.add_movie_to_wishlist(77, "Grown-up Movie")
    db.set_wishlist_release_date(77, D(2))
    token = db.calendar_feed_token(3)
    monkeypatch.setattr("core.content_filter._load_profile",
                        lambda pid: {"id": pid, "is_admin": False, "allowed_sides": "both",
                                     "max_rating": "TV-Y7"})
    body = c.get("/api/video/calendar/feed/%s.ics?scope=all" % token).get_data(as_text=True)
    assert "Grown-up Show" not in body
    assert "Grown-up Movie" not in body


def test_every_gate_leaves_a_well_formed_feed_path_open():
    from core.security.launch_lock import request_is_locked
    from core.security.login_gate import login_request_is_blocked
    from core.security.session_profile import no_profile_request_is_blocked
    good = "/api/video/calendar/feed/%s.ics" % ("A" * 43)
    assert not request_is_locked(good, "GET", require_pin=True, pin_verified=False)
    assert not login_request_is_blocked(good, "GET", require_login=True, authenticated=False)
    assert not no_profile_request_is_blocked(good, "GET")
    # the rest of the calendar stays behind every gate
    for bad in ("/api/video/calendar", "/api/video/calendar/feed-url",
                "/api/video/calendar/feed/short.ics", "/api/video/calendar.ics"):
        assert request_is_locked(bad, "GET", require_pin=True, pin_verified=False), bad
        assert login_request_is_blocked(bad, "GET", require_login=True, authenticated=False), bad
        assert no_profile_request_is_blocked(bad, "GET"), bad
    assert request_is_locked(good, "POST", require_pin=True, pin_verified=False)


# ── pure pieces ─────────────────────────────────────────────────────────────


def test_live_index_keeps_status_percent_and_eta():
    from core.video.calendar_state import live_index
    rows = [{"kind": "episode", "media_id": "5", "status": "downloading", "progress": 12.4,
             "eta_seconds": 90, "search_ctx": json.dumps({"season": 1, "episode": 2})},
            {"kind": "movie", "media_id": "9", "status": "queued", "progress": None}]
    idx = live_index(rows)
    assert idx[("episode", "5", 1, 2)] == {"status": "downloading", "progress": 12, "eta_seconds": 90}
    assert idx[("movie", "9")]["progress"] == 0


def test_movie_state_floor_is_wanted():
    from core.video.calendar_state import movie_state
    assert movie_state({"tmdb_id": 1}) == "wanted"
    assert movie_state({"tmdb_id": 1, "owned": True}) == "owned"
    assert movie_state({"tmdb_id": 1, "status": "failed"}) == "failed"
    assert movie_state({"tmdb_id": 1}, active={("movie", "1"): {"status": "queued"}}) == "queued"


def test_system_status_reads_one_row_per_action():
    from core.automation.system_status import system_automation_status

    class Db:
        def get_system_automation_by_action(self, a):
            if a == "gone":
                return None
            return {"enabled": 1, "trigger_type": "schedule",
                    "trigger_config": '{"interval": 4, "unit": "hours"}',
                    "last_run": "2026-10-28 04:00:00", "next_run": "2026-10-28 08:00:00",
                    "last_error": None, "last_result": '{"status": "completed"}'}

    out = system_automation_status(["video_add_airing_episodes", "gone"], database=Db())
    assert out["gone"] is None
    assert out["video_add_airing_episodes"] == {
        "enabled": True, "trigger_type": "schedule", "interval_hours": 4,
        "last_run": "2026-10-28 04:00:00", "next_run": "2026-10-28 08:00:00",
        "last_error": None, "last_status": "completed"}


def test_the_video_gate_lets_a_sessionless_feed_through(tmp_path, monkeypatch):
    """What web_server sets for a request with no profile: no admin, no sides.
    Every other video route 403s that; the feed answers for its token."""
    import api.video as videoapi
    from flask import g
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")
    monkeypatch.setattr("core.content_filter._load_profile",
                        lambda pid: {"id": pid, "is_admin": True})
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _no_profile():
        g.profile_id, g.is_admin, g.can_download, g.allowed_sides = None, False, False, "none"

    c = app.test_client()
    try:
        token = db.calendar_feed_token(1)
        assert c.get("/api/video/calendar/feed/%s.ics" % token).status_code == 200
        assert c.get("/api/video/calendar").status_code == 403
        assert c.get("/api/video/calendar/feed-url").status_code == 403
    finally:
        videoapi._video_db = None


def test_the_window_is_read_off_the_air_date_index(tmp_path):
    """Boulder's 220k-episode library: with no planner stats SQLite walked every
    show's every episode for one week (6.1s, and the page's 10s fetch timed out
    on a busy box). The window must drive the query through idx_episodes_air."""
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    seen = []
    real = db._get_connection

    class Spy:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *args):
            if "FROM episodes e" in sql:
                seen.append((sql, args[0] if args else ()))
            return self._c.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._c, name)

    db._get_connection = lambda: Spy(real())
    db.calendar_upcoming("2026-10-25", "2026-10-31", server_source="plex",
                         watchlist_only=True, profile_id=1)
    assert seen
    sql, args = seen[-1]
    conn = real()
    plan = " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, args))
    conn.close()
    assert "idx_episodes_air" in plan, plan


# ── unmatched shows (Boulder, Oct 28: "Wishlist 16" answered "Nothing new to add") ──


def test_an_unmatched_shows_episode_is_unmatched_not_missing(env):
    """All library showed 16 missing and a Wishlist 16 button; every one was on
    a show row with no TMDB id, which the wishlist can't key, so the button
    could only ever add nothing. Those rows read Unmatched and stay out of the
    missing count and the bulk add."""
    c, db = env
    db.upsert_show_tree("plex", {
        "server_id": "u1", "title": "Copy", "tmdb_id": None,
        "seasons": [{"season_number": 8, "episodes": [{"episode_number": 4, "title": "E4", "air_date": D(-1)}]}]})
    body = _cal(c)
    assert [e["acq"] for e in body["episodes"]] == ["unmatched"]
    assert body["needs_action"] == 0
    r = c.post("/api/video/calendar/wishlist-missing",
               json={"start": D(-7), "end": D(0), "scope": "all"}).get_json()
    assert r["added"] == 0 and r["shows"] == 0


def test_an_unmatched_upcoming_episode_does_not_promise_auto(env):
    c, db = env
    db.upsert_show_tree("plex", {
        "server_id": "u1", "title": "Copy", "tmdb_id": None,
        "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1, "title": "E1", "air_date": D(2)}]}]})
    assert [e["acq"] for e in _cal(c)["episodes"]] == ["unmatched"]


def test_twelve_unmatched_copies_of_one_episode_are_one_card(env):
    c, db = env
    for n in range(12):
        db.upsert_show_tree("plex", {
            "server_id": "u%d" % n, "title": "90 Day Fiancé: The Other Way", "tmdb_id": None,
            "seasons": [{"season_number": 8, "episodes": [
                {"episode_number": 4, "title": "Episode 4", "air_date": D(-2)}]}]})
    _show(db, tmdb_id=555, title="Matched", server_id="m1",
          episodes=[{"episode_number": 1, "title": "E1", "air_date": D(-2)}])
    eps = _cal(c)["episodes"]
    assert sorted(e["show_title"] for e in eps) == ["90 Day Fiancé: The Other Way", "Matched"]


def test_copies_of_one_matched_show_are_one_card_and_the_owned_copy_wins(env):
    """Once the split copies share a TMDB id, each still holds its own copy of
    the episode. One card, and the copy with the file decides the state."""
    c, db = env
    db.upsert_show_tree("plex", {"server_id": "a", "title": "Split", "tmdb_id": 90046, "seasons": [
        {"season_number": 8, "episodes": [{"episode_number": 4, "title": "E4", "air_date": D(-1)}]}]})
    db.upsert_show_tree("plex", {"server_id": "b", "title": "Split", "tmdb_id": 90046, "seasons": [
        {"season_number": 8, "episodes": [{"episode_number": 4, "title": "E4", "air_date": D(-1),
                                           "file": {"relative_path": "e.mkv", "size_bytes": 1}}]}]})
    eps = _cal(c)["episodes"]
    assert [(e["season_number"], e["episode_number"], e["acq"]) for e in eps] == [(8, 4, "owned")]
