"""player notifications: delivery rules, prefs, the generator's kinds, routes"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest
from flask import Flask

import api.player as player_api
from core.player import notifications as N
from core.player import notify_gen as G
from core.player import progress, watchlist

DAY = 86400
# a fixed noon, utc, so quiet hours and days are plain to read
NOON = dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.timezone.utc).timestamp()
UTC = {"timezone": "UTC"}


@pytest.fixture
def db(tmp_path):
    from database.player_database import PlayerDatabase
    return PlayerDatabase(str(tmp_path / "player.db"))


class VideoDB:
    """the video tables the generator reads, and nothing else"""

    def __init__(self, path):
        self.path = str(path)
        conn = self._get_connection()
        conn.executescript("""
            CREATE TABLE movies (id INTEGER PRIMARY KEY, title TEXT, year INTEGER, rating REAL,
                imdb_rating REAL, rt_rating INTEGER, metacritic INTEGER, rating_critic REAL, added_at TEXT);
            CREATE TABLE shows (id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE episodes (id INTEGER PRIMARY KEY, show_id INTEGER, season_number INTEGER,
                episode_number INTEGER, title TEXT, added_at TEXT, air_date TEXT, has_file INTEGER);
            CREATE TABLE media_files (id INTEGER PRIMARY KEY, movie_id INTEGER, episode_id INTEGER,
                video_id INTEGER);
        """)
        conn.commit()
        conn.close()
        self.next_file = 1

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def run(self, sql, *args):
        conn = self._get_connection()
        conn.execute(sql, args)
        conn.commit()
        conn.close()

    def movie(self, mid, title, rating=8.0, added="2026-11-26", **kw):
        self.run("INSERT INTO movies (id, title, year, rating, imdb_rating, rt_rating, metacritic, added_at) "
                 "VALUES (?, ?, 2026, ?, ?, ?, ?, ?)", mid, title, rating, kw.get("imdb"), kw.get("rt"),
                 kw.get("mc"), added)
        self.file(movie_id=mid)

    def episode(self, eid, show, season, number, title="", added="2026-11-26", air=None, file=True):
        self.run("INSERT OR IGNORE INTO shows (id, title) VALUES (?, ?)", show, f"Show {show}")
        self.run("INSERT INTO episodes (id, show_id, season_number, episode_number, title, added_at, air_date, "
                 "has_file) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", eid, show, season, number, title, added, air,
                 1 if file else 0)
        if file:
            self.file(episode_id=eid)

    def file(self, movie_id=None, episode_id=None):
        self.run("INSERT INTO media_files (id, movie_id, episode_id) VALUES (?, ?, ?)",
                 self.next_file, movie_id, episode_id)
        self.next_file += 1


@pytest.fixture
def vdb(tmp_path):
    return VideoDB(tmp_path / "video.db")


PROFILES = [{"id": 1, "is_admin": True}, {"id": 2}, {"id": 3}]


def ctx(db, vdb, now=NOON, hidden=None, **kw):
    """profile 3 sees nothing in ``hidden`` (its hidden libraries, its cap)"""
    hidden = hidden or {}

    def allow(p, kind, ids):
        return {int(i) for i in ids} - (hidden.get(kind, set()) if p["id"] == 3 else set())

    return G.Ctx(db, vdb, None, profiles=[dict(p) for p in PROFILES], allow=allow, now=now, **kw)


def prime(db, vdb, now=NOON - 600):
    """the first run only sets the marks"""
    for p in PROFILES:
        N.put_prefs(db, p["id"], UTC, now=now)
    G.run(ctx(db, vdb, now=now))


def notes(db, pid):
    return N.list_for(db, pid, limit=200)["notifications"]


def watched(db, pid, eid, show, at):
    progress.save(db, pid, "episode", str(eid), 100, 1000, meta={"parent_id": str(show)}, now=at)


# ── delivery: quiet hours, digest, dedupe ────────────────────────────────

def test_a_group_key_is_told_once_and_replace_moves_it_to_the_top(db):
    assert N.deliver(db, 2, "new_episode", "one", group_key="g", prefs=N.clean_prefs(UTC), now=NOON) == "sent"
    assert N.deliver(db, 2, "new_episode", "again", group_key="g", prefs=N.clean_prefs(UTC), now=NOON) is None
    first = notes(db, 2)[0]["id"]
    N.deliver(db, 2, "new_episode", "two", group_key="g", replace=True, prefs=N.clean_prefs(UTC), now=NOON)
    got = notes(db, 2)
    assert [n["title"] for n in got] == ["two"] and got[0]["id"] > first


def test_a_kind_turned_off_is_never_sent_and_admin_messages_always_are(db):
    N.put_prefs(db, 2, {"kinds": {"featured_movie": False}})
    assert N.deliver(db, 2, "featured_movie", "x") is None
    with pytest.raises(N.PrefsError):
        N.put_prefs(db, 2, {"kinds": {"admin_message": False}})
    assert N.deliver(db, 2, "admin_message", "hello") == "sent"


def test_quiet_hours_hold_until_they_end_then_ids_still_grow(db):
    N.put_prefs(db, 2, {**UTC, "quiet_hours": True, "quiet_start": "11:00", "quiet_end": "13:00"})
    N.deliver(db, 2, "admin_message", "now", now=NOON)
    assert N.deliver(db, 2, "new_episode", "later", group_key="e", now=NOON) == "held"
    assert [n["title"] for n in notes(db, 2)] == ["now"]
    assert N.release_due(db, now=NOON + 1800) == 0           # still quiet
    assert N.release_due(db, now=NOON + 3600 + 60) == 1
    got = notes(db, 2)
    assert got[0]["title"] == "later" and got[0]["id"] > got[1]["id"]
    # a held group still dedupes
    assert N.deliver(db, 2, "new_episode", "later", group_key="e", now=NOON + 3700) is None


def test_quiet_hours_across_midnight():
    p = N.clean_prefs({**UTC, "quiet_hours": True, "quiet_start": "22:00", "quiet_end": "07:00"})
    midnight = dt.datetime(2026, 11, 27, 0, 30, tzinfo=dt.timezone.utc).timestamp()
    assert N.in_quiet(p, midnight) and not N.in_quiet(p, NOON)


def test_the_digest_is_one_note_a_day_and_invites_skip_it(db):
    N.put_prefs(db, 2, {**UTC, "digest": True, "digest_time": "18:00"})
    for i in range(3):
        assert N.deliver(db, 2, "new_episode", f"show {i}", group_key=f"s{i}", now=NOON) == "held"
    assert N.deliver(db, 2, "party_invite", "come watch", now=NOON) == "sent"
    assert N.release_due(db, now=NOON + 3600) == 0
    assert N.release_due(db, now=NOON + 6 * 3600 + 60) == 1
    top = notes(db, 2)[0]
    assert top["kind"] == "digest" and top["title"] == "3 things new for you today"
    assert "show 0" in top["body"]


def test_turning_the_digest_off_lets_held_ones_out_next_tick(db):
    N.put_prefs(db, 2, {**UTC, "digest": True})
    N.deliver(db, 2, "new_episode", "held one", now=NOON)
    N.put_prefs(db, 2, {"digest": False}, now=NOON + 10)
    assert N.release_due(db, now=NOON + 20) == 1


def test_prefs_validation():
    for bad in ({"kinds": {"nope": True}}, {"kinds": {"new_episode": "yes"}}, {"quiet_start": "25:00"},
                {"timezone": "Mars/Base"}, {"surprise": 1}, {}, None):
        with pytest.raises(N.PrefsError):
            N.clean_prefs(bad)
    p = N.clean_prefs({"kinds": {"new_music": True}, "timezone": "America/Chicago", "digest_time": "07:15"})
    assert p["kinds"]["new_music"] and p["kinds"]["new_episode"] and p["digest_time"] == "07:15"


def test_reads_never_make_tables(db):
    assert N.list_for(db, 2) == {"notifications": [], "unread": 0, "latest_id": 0}
    assert N.get_prefs(db, 2) == N.defaults()
    with db._get_connection() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (N.TABLE,)).fetchone()


def test_profiles_only_see_their_own(db):
    N.deliver(db, 2, "admin_message", "for two")
    assert notes(db, 3) == []
    nid = notes(db, 2)[0]["id"]
    assert not N.remove(db, 3, nid) and N.mark_read(db, 3, [nid]) == 0
    assert N.mark_read(db, 2, [nid]) == 1 and N.list_for(db, 2)["unread"] == 0


# ── the generator ────────────────────────────────────────────────────────

def test_first_run_only_sets_marks(db, vdb):
    vdb.movie(1, "Old Great", rating=9.0, imdb=9.0)
    prime(db, vdb)
    assert G.get_mark(db, "media_files") == 1
    assert all(notes(db, p["id"]) == [] for p in PROFILES)


def test_new_episodes_batch_per_show_per_day_for_watchers_only(db, vdb):
    vdb.episode(10, show=5, season=1, number=1)
    prime(db, vdb)
    watched(db, 2, 10, 5, NOON - 3 * DAY)               # watching
    watched(db, 3, 10, 5, NOON - 200 * DAY)             # stopped long ago
    vdb.episode(11, 5, 1, 2, "The Second")
    G.run(ctx(db, vdb))
    got = notes(db, 2)
    assert [n["title"] for n in got] == ["New episode of Show 5"]
    assert got[0]["link"] == "/player/tv/5/episode/11"
    assert notes(db, 3) == [] and notes(db, 1) == []
    vdb.episode(12, 5, 1, 3)
    vdb.episode(13, 5, 1, 4)
    G.run(ctx(db, vdb, now=NOON + 900))
    got = notes(db, 2)
    assert len(got) == 1 and got[0]["title"] == "3 new episodes of Show 5"
    assert got[0]["body"] == "S1 E2, S1 E3, S1 E4"
    # the next day is a new note
    vdb.episode(14, 5, 1, 5)
    G.run(ctx(db, vdb, now=NOON + DAY))
    assert len(notes(db, 2)) == 2


def test_an_upgrade_of_an_old_episode_is_not_news(db, vdb):
    prime(db, vdb)
    watched(db, 2, 20, 6, NOON - DAY)
    vdb.episode(20, 6, 1, 1, added="2025-01-01")
    G.run(ctx(db, vdb))
    assert notes(db, 2) == []


def test_a_watchlisted_show_arrives_then_counts_as_watching(db, vdb):
    prime(db, vdb)
    watchlist.add(db, 2, "show", "7")
    vdb.episode(30, 7, 1, 1)
    G.run(ctx(db, vdb))
    assert [n["kind"] for n in notes(db, 2)] == ["watchlist_available"]
    vdb.episode(31, 7, 1, 2)
    G.run(ctx(db, vdb, now=NOON + 900))
    assert [n["kind"] for n in notes(db, 2)] == ["new_episode", "watchlist_available"]


def test_a_new_season_is_a_premiere_for_people_who_watched_before(db, vdb):
    vdb.episode(40, 8, 1, 10)
    prime(db, vdb)
    watched(db, 2, 40, 8, NOON - 300 * DAY)             # long ago still counts
    vdb.episode(41, 8, 2, 1, "Back Again")
    G.run(ctx(db, vdb))
    got = notes(db, 2)
    assert [(n["kind"], n["title"]) for n in got] == [("season_premiere", "Season 2 of Show 8 is here")]


def test_a_premiere_airing_soon_is_told_once(db, vdb):
    vdb.episode(50, 9, 1, 1)
    prime(db, vdb)
    watched(db, 2, 50, 9, NOON - DAY)
    vdb.episode(51, 9, 2, 1, air="2026-11-27", file=False)
    G.run(ctx(db, vdb, now=NOON + 4000))
    G.run(ctx(db, vdb, now=NOON + 4000 + 2 * 3600))
    got = notes(db, 2)
    assert [n["title"] for n in got] == ["Season 2 of Show 9 starts tomorrow"]


def test_featured_films_need_a_good_rating_and_stop_at_three_a_week(db, vdb):
    prime(db, vdb)
    vdb.movie(1, "B Movie", rating=8.2, imdb=4.1, rt=20)     # tmdb alone says yes, the rest say no
    vdb.movie(2, "Fine", rating=6.5, imdb=7.5)
    for i in range(3, 8):
        vdb.movie(i, f"Great {i}", rating=7.0 + i / 10, imdb=7.9)
    G.run(ctx(db, vdb))
    titles = [n["title"] for n in notes(db, 2)]
    assert len(titles) == 3 and not any("B Movie" in t or "Fine" in t for t in titles)
    assert "New film: Great 7 (2026)" in titles                  # the best go first
    vdb.movie(9, "Great 9", rating=9.0, imdb=9.0)
    G.run(ctx(db, vdb, now=NOON + DAY))
    assert len(notes(db, 2)) == 3
    vdb.movie(10, "Great 10", rating=9.0, imdb=9.0, added="2026-12-04")
    G.run(ctx(db, vdb, now=NOON + 8 * DAY))
    assert len(notes(db, 2)) == 4


def test_notable_without_other_scores_needs_a_higher_bar():
    assert not G.notable({"rating": 7.5}) and G.notable({"rating": 7.9})
    assert G.notable({"rating": 7.2, "rt_rating": 88, "imdb_rating": 6.5})


def test_hidden_libraries_and_caps_are_respected(db, vdb):
    prime(db, vdb)
    vdb.movie(1, "Hidden Gem", rating=8.5, imdb=8.4)
    watchlist.add(db, 3, "movie", "1")
    watched(db, 3, 60, 12, NOON - DAY)
    vdb.episode(60, 12, 1, 1)
    G.run(ctx(db, vdb, hidden={"movie": {1}, "show": {12}}))
    assert notes(db, 3) == []
    assert [n["kind"] for n in notes(db, 2)] == ["featured_movie"]


def test_the_watchlist_wins_over_featured(db, vdb):
    prime(db, vdb)
    watchlist.add(db, 2, "movie", "1")
    vdb.movie(1, "Wanted", rating=8.5, imdb=8.4)
    G.run(ctx(db, vdb))
    got = notes(db, 2)
    assert [(n["kind"], n["title"]) for n in got] == [("watchlist_available", "Wanted (2026) is here")]
    assert got[0]["link"] == "/player/movies/1" and got[0]["image"].startswith("/api/video/poster/movie/1")


def test_leaving_soon_only_for_titles_you_started(db, vdb):
    N.put_prefs(db, 2, {**UTC, "kinds": {"leaving_soon": True}})
    N.put_prefs(db, 3, {**UTC, "kinds": {"leaving_soon": True}})
    progress.save(db, 2, "movie", "4", 600, 6000, now=NOON - DAY)
    found = [{"details": {"kind": "movie", "item_id": 4, "title": "Long Film"}},
             {"details": {"kind": "movie", "item_id": 5, "title": "Unseen"}}]
    out = {}
    G._leaving_soon(ctx(db, vdb), out, findings=found)
    assert [n["title"] for n in notes(db, 2)] == ["Long Film may be leaving soon"]
    assert notes(db, 3) == []


def test_memories_once_a_day_after_the_chosen_time(db, vdb, monkeypatch):
    from core.player import home_access
    from core.video import home_videos as H
    N.put_prefs(db, 2, {**UTC, "kinds": {"home_memories": True}, "memories_time": "13:00"})
    monkeypatch.setattr(home_access, "visible_roots", lambda *a, **k: ["r1"])
    monkeypatch.setattr(H, "on_this_day_rows", lambda conn, roots, today=None, limit=12:
                        [{"sort_at": "2019-11-26"}, {"sort_at": "2021-11-26"}])
    c = ctx(db, vdb, home_ok=lambda p: True)
    G._memories(c, {})
    assert notes(db, 2) == []                                  # before 13:00
    c.now = NOON + 3600
    G._memories(c, {})
    G._memories(c, {})
    got = notes(db, 2)
    assert len(got) == 1 and got[0]["body"] == "2 home videos from this day in 2019 to 2021."


def test_request_notes_become_request_done(db, monkeypatch):
    monkeypatch.setattr(N, "_player_db", lambda: db)
    N.on_profile_note(2, "Dune (2021) is in your library now", "success", "video-requests")
    N.on_profile_note(2, "Dune was approved", "success", "video-requests")
    N.on_profile_note(2, "Song was declined: no", "warning", "requests")
    N.on_profile_note(2, "issue answered", "info", "issues")
    assert [n["title"] for n in notes(db, 2)] == ["About your request", "Your request is ready"]


def test_party_invites_skip_the_host(db):
    party = {"id": "abc", "host_profile_id": 1, "title": "Movie night", "scheduled_for": None}
    assert N.party_invites(db, party, "Ada", [1, 2, 3]) == 2
    assert notes(db, 1) == [] and notes(db, 2)[0]["link"] == "/player/watching?party=abc"


def test_old_notes_are_pruned(db):
    N.deliver(db, 2, "admin_message", "old", now=NOON - 100 * DAY)
    N.deliver(db, 2, "admin_message", "new", now=NOON)
    N.prune(db, now=NOON)
    assert [n["title"] for n in notes(db, 2)] == ["new"]


# ── routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    player_api._test_sent.clear()
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    return app.test_client()


def _as(client, profile_id):
    with client.session_transaction() as sess:
        sess["profile_id"] = profile_id


def test_routes(client, db):
    _as(client, 2)
    assert client.get("/api/player/notifications").get_json()["latest_id"] == 0
    prefs = client.get("/api/player/notifications/prefs").get_json()
    assert {k["id"] for k in prefs["kinds"]} == set(N.KIND_IDS)
    assert next(k for k in prefs["kinds"] if k["id"] == "admin_message")["locked"] is True
    assert client.put("/api/player/notifications/prefs", json={"quiet_start": "nope"}).status_code == 400
    saved = client.put("/api/player/notifications/prefs", json={"kinds": {"new_music": True}}).get_json()
    assert saved["prefs"]["kinds"]["new_music"] is True
    sent = client.post("/api/player/notifications/test").get_json()
    assert sent["notifications"][0]["kind"] == "test"
    assert client.post("/api/player/notifications/test").status_code == 429
    N.deliver(db, 2, "admin_message", "two")
    got = client.get("/api/player/notifications?since=1").get_json()
    assert [n["title"] for n in got["notifications"]] == ["two"] and got["unread"] == 2
    assert client.get("/api/player/notifications?since=x").status_code == 400
    assert client.post("/api/player/notifications/read", json={}).status_code == 400
    assert client.post("/api/player/notifications/read", json={"all": True}).get_json()["marked"] == 2
    _as(client, 3)
    assert client.delete(f"/api/player/notifications/{got['latest_id']}").status_code == 404
    _as(client, 2)
    assert client.delete(f"/api/player/notifications/{got['latest_id']}").status_code == 200


def test_films_not_in_the_library_only_when_asked_and_capped_kids_never(db, vdb):
    out_now = [
        {"kind": "movie", "tmdb_id": 101, "title": "Big Hit", "year": "2026", "date": "2026-11-20",
         "rating": 7.6, "popularity": 300},
        {"kind": "movie", "tmdb_id": 102, "title": "Meh", "year": "2026", "date": "2026-11-21",
         "rating": 5.1, "popularity": 20},
        {"kind": "movie", "tmdb_id": 103, "title": "Owned", "date": "2026-11-21", "rating": 8.0,
         "library_id": 9},
        {"kind": "movie", "tmdb_id": 104, "title": "Old", "date": "2025-01-01", "rating": 8.0},
    ]
    N.put_prefs(db, 2, {**UTC, "featured_not_in_library": True})
    N.put_prefs(db, 3, {**UTC, "featured_not_in_library": True})
    G._ensure(db)
    c = ctx(db, vdb, released=lambda: out_now, unowned_ok=lambda p: p["id"] != 3)
    G._released_films(c, {})
    got = notes(db, 2)
    assert [n["title"] for n in got] == ["Out now: Big Hit (2026)"]
    assert got[0]["link"] == "/player/title/movie/101"
    assert notes(db, 3) == [] and notes(db, 1) == []      # 1 never asked
