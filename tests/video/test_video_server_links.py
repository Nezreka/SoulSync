"""plex and jellyfin as one library (core/video/server_links.py).

the other server's items are matched to the library's rows (the file itself,
then ids, never a guess), watched follows across the two, and switching the
video server keeps every linked title's row.
"""

from __future__ import annotations

import pytest

from core.video import server_links as sl
from database.video_database import VideoDatabase


def _q(db, sql, *args):
    conn = db._get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _x(db, sql, *args):
    conn = db._get_connection()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


class Server:
    """a media server as the scanner's duck type, with a memory of pushes"""

    def __init__(self, name, movies=(), shows=()):
        self.server_name = name
        self.movies, self.shows = list(movies), list(shows)
        self.pushed = []
        self.fail = False

    def iter_movies(self, incremental=False, since=None):
        return iter(self.movies)

    def iter_shows(self, incremental=False, since=None):
        return iter(self.shows)

    def set_watched(self, server_id, watched, kind="movie"):
        if self.fail:
            return {"ok": False, "error": "down"}
        self.pushed.append((kind, str(server_id), watched))
        for m in self.movies:
            if str(m["server_id"]) == str(server_id):
                m["play_count"] = 1 if watched else 0
        for s in self.shows:
            for se in s["seasons"]:
                for e in se["episodes"]:
                    if str(e["server_id"]) == str(server_id):
                        e["play_count"] = 1 if watched else 0
        return {"ok": True}


def _movie(sid, title, tmdb=None, imdb=None, path=None, size=None, plays=0):
    d = {"server_id": sid, "title": title, "tmdb_id": tmdb, "imdb_id": imdb, "play_count": plays}
    if path:
        d["files"] = [{"relative_path": path, "size_bytes": size}]
    return d


def _show(sid, title, eps, tvdb=None, tmdb=None, year=None, prefix="e"):
    seasons = {}
    for s, e, plays in eps:
        seasons.setdefault(s, []).append({"server_id": f"{prefix}{s}x{e}", "episode_number": e,
                                          "play_count": plays, "air_date": "2023-01-01"})
    return {"server_id": sid, "title": title, "tvdb_id": tvdb, "tmdb_id": tmdb, "year": year,
            "seasons": [{"season_number": s, "episodes": v} for s, v in seasons.items()]}


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda _db=None: "plex")
    monkeypatch.setattr("core.video.sources.configured_video_servers", lambda _db=None: ["plex", "jellyfin"])
    plex = Server("plex", movies=[
        _movie("p1", "The Matrix", tmdb=603, path="/plex/Matrix (1999)/Matrix.mkv", size=1000),
        _movie("p2", "Heat", tmdb=949, path="/plex/Heat/Heat.mkv", size=2000),
        _movie("p3", "Heat", tmdb=949, path="/plex4k/Heat/Heat.2160p.mkv", size=9000),   # the 4K copy
        _movie("p4", "Home Video", path="/plex/home/home.mkv", size=4242),               # no ids at all
        _movie("p5", "Only On Plex", tmdb=5),
    ], shows=[_show("ps", "Silo", [(1, 1, 0), (1, 2, 0)], tvdb=403245, prefix="pe"),
              _show("ps2", "No Ids Show", [(1, 1, 0)], year=2020, prefix="pn"),
              _show("ps3", "Just A Name", [(1, 1, 0)], prefix="pj")])
    for m in plex.movies:
        db.upsert_movie("plex", m)
    for s in plex.shows:
        db.upsert_show_tree("plex", s)
    jelly = Server("jellyfin", movies=[
        _movie("j1", "Matrix", imdb="tt0133093", path="D:\\media\\Matrix (1999)\\Matrix.mkv", size=1000),
        _movie("j2", "Heat", tmdb=949),
        _movie("j3", "Heat", tmdb=949),
        _movie("j4", "home", path="/jf/home/home.mkv", size=4242),
        _movie("j9", "Only On Jellyfin", tmdb=77),
    ], shows=[_show("js", "Silo (2023)", [(1, 1, 0), (1, 2, 0), (1, 3, 0)], tvdb=403245, prefix="je"),
              _show("js2", "No Ids Show", [(1, 1, 0)], year=2020, prefix="jn"),
              _show("js3", "Another Show", [(1, 1, 0)], prefix="jx"),
              _show("js4", "Just A Name", [(1, 1, 0)], prefix="jj")])
    db.set_setting(sl.SETTING, "1")
    return db, plex, jelly


def _links(db, kind):
    return {(r["item_id"]): r["server_id"] for r in
            _q(db, "SELECT item_id, server_id FROM video_server_links WHERE kind=?", kind)}


def _id(db, table, server_id):
    return _q(db, f"SELECT id FROM {table} WHERE server_id=?", server_id)[0]["id"]


# ── matching ────────────────────────────────────────────────────────────────

def test_titles_are_matched_by_the_file_then_ids_and_never_guessed(lib):
    db, plex, jelly = lib
    res = sl.link_pass(db, jelly)
    m = _links(db, "movie")
    assert m[_id(db, "movies", "p1")] == "j1"            # same file name + size, different ids
    assert m[_id(db, "movies", "p4")] == "j4"            # no ids at all: the file says so
    # two rows of one film (HD and 4K) take one item each, none twice
    assert sorted([m[_id(db, "movies", "p2")], m[_id(db, "movies", "p3")]]) == ["j2", "j3"]
    assert _id(db, "movies", "p5") not in m
    assert res["movies"] == 4 and "Only On Jellyfin" in res["unmatched"]
    s = _links(db, "show")
    assert s[_id(db, "shows", "ps")] == "js"
    assert s[_id(db, "shows", "ps2")] == "js2"           # no ids: exact title + year, the only one
    # a title alone, with no year and no ids, is a guess: not linked
    assert _id(db, "shows", "ps3") not in s
    assert "Another Show" in res["unmatched"] and "Just A Name" in res["unmatched"]
    assert res["unmatched_count"] == 3
    e = _links(db, "episode")
    assert sorted(e.values()) == ["je1x1", "je1x2", "jn1x1"]      # an episode the library lacks isn't linked


def test_a_full_pass_forgets_what_the_other_server_no_longer_has(lib):
    db, plex, jelly = lib
    sl.link_pass(db, jelly)
    jelly.movies = [m for m in jelly.movies if m["server_id"] != "j1"]
    sl.link_pass(db, jelly, incremental=True)
    assert "j1" in _links(db, "movie").values()          # an incremental read only adds
    sl.link_pass(db, jelly)
    assert "j1" not in _links(db, "movie").values()
    # a read that comes back empty (the server was down) forgets nothing
    jelly.movies, jelly.shows = [], []
    before = len(_q(db, "SELECT 1 FROM video_server_links"))
    sl.link_pass(db, jelly)
    assert len(_q(db, "SELECT 1 FROM video_server_links")) == before


# ── watched, in step ────────────────────────────────────────────────────────

def test_the_rule():
    # first compare: watched on either side is watched
    assert sl.decide(False, True, None, None) is True
    assert sl.decide(False, False, None, None) is False
    # after that the side that changed wins, both ways
    assert sl.decide(True, False, 0, 0) is True          # we watched it
    assert sl.decide(False, True, 0, 0) is True          # they watched it
    assert sl.decide(False, True, 1, 1) is False         # we unwatched it
    assert sl.decide(True, False, 1, 1) is False         # they unwatched it
    # both changed, opposite ways: watched wins
    assert sl.decide(True, False, 0, 1) is True
    # nothing changed
    assert sl.decide(True, True, 1, 1) is True and sl.decide(False, False, 0, 0) is False


def _pass(db, plex, jelly):
    res = sl.link_pass(db, jelly)
    return sl.sync_watch(db, res["watch"], jelly, primary=plex)


def test_watched_follows_both_ways_and_unwatched_too(lib):
    db, plex, jelly = lib
    m1 = _id(db, "movies", "p1")
    ep = _q(db, "SELECT id FROM episodes WHERE server_id='pe1x1'")[0]["id"]
    # watched on jellyfin before linking ever ran: the library and plex get it
    jelly.movies[0]["play_count"] = 2
    out = _pass(db, plex, jelly)
    assert out == {"to_library": 1, "to_other": 0, "failed": 0}
    assert _q(db, "SELECT play_count FROM movies WHERE id=?", m1)[0]["play_count"] == 1
    assert ("movie", "p1", True) in plex.pushed
    # an episode watched on plex (the scan brought it in): jellyfin gets it
    _x(db, "UPDATE episodes SET play_count=1 WHERE id=?", ep)
    out = _pass(db, plex, jelly)
    assert out == {"to_library": 0, "to_other": 1, "failed": 0} and ("episode", "je1x1", True) in jelly.pushed
    # nothing changed: nothing is pushed
    n = (len(plex.pushed), len(jelly.pushed))
    assert _pass(db, plex, jelly) == {"to_library": 0, "to_other": 0, "failed": 0}
    assert (len(plex.pushed), len(jelly.pushed)) == n
    # unwatched on jellyfin: the library and plex follow
    jelly.movies[0]["play_count"] = 0
    _pass(db, plex, jelly)
    assert _q(db, "SELECT play_count FROM movies WHERE id=?", m1)[0]["play_count"] == 0
    assert plex.pushed[-1] == ("movie", "p1", False)
    # unwatched in the library (plex's scan said so): jellyfin follows
    _x(db, "UPDATE episodes SET play_count=0 WHERE id=?", ep)
    _pass(db, plex, jelly)
    assert jelly.pushed[-1] == ("episode", "je1x1", False)


def test_a_push_that_fails_is_tried_again(lib):
    db, plex, jelly = lib
    m1 = _id(db, "movies", "p1")
    _pass(db, plex, jelly)
    _x(db, "UPDATE movies SET play_count=1 WHERE id=?", m1)
    jelly.fail = True
    assert _pass(db, plex, jelly)["failed"] == 1
    jelly.fail = False
    assert _pass(db, plex, jelly)["to_other"] == 1 and jelly.movies[0]["play_count"] == 1
    # and the video server being down doesn't leave the library saying something it can't keep
    jelly.movies[1]["play_count"] = 1
    plex.fail = True
    target = [r for r in _q(db, "SELECT item_id FROM video_server_links WHERE server_id=?",
                            jelly.movies[1]["server_id"])][0]["item_id"]
    assert _pass(db, plex, jelly)["failed"] == 1
    assert _q(db, "SELECT play_count FROM movies WHERE id=?", target)[0]["play_count"] in (0, None)


def test_a_tick_in_soulsync_reaches_the_linked_server_at_once(lib, monkeypatch):
    db, plex, jelly = lib
    sl.link_pass(db, jelly)
    monkeypatch.setattr("core.video.sources.source_for_server", lambda name: jelly)
    monkeypatch.setattr("core.video.sources.get_active_video_source", lambda **k: plex)
    from core.video import metadata as med
    m1 = _id(db, "movies", "p1")
    assert med.set_watched(db, "movie", m1, True, source=plex)["pushed"] is True
    assert ("movie", "p1", True) in plex.pushed and ("movie", "j1", True) in jelly.pushed
    # the next compare sees both already agree
    n = len(jelly.pushed)
    assert _pass(db, plex, jelly) == {"to_library": 0, "to_other": 0, "failed": 0} and len(jelly.pushed) == n
    # off: only the video server is told
    db.set_setting(sl.SETTING, "0")
    med.set_watched(db, "movie", m1, False, source=plex)
    assert len(jelly.pushed) == n


# ── switching servers ───────────────────────────────────────────────────────

def test_switching_the_video_server_keeps_every_linked_row(lib):
    db, plex, jelly = lib
    sl.link_pass(db, jelly)
    m1, lonely = _id(db, "movies", "p1"), _id(db, "movies", "p5")
    show = _id(db, "shows", "ps")
    _x(db, "UPDATE movies SET monitored=0 WHERE id=?", m1)        # a choice the person made
    out = sl.rekey_library(db, "plex", "jellyfin")
    assert out["movies"] == 4 and out["shows"] == 2 and out["episodes"] == 3
    assert _q(db, "SELECT server_source, server_id, monitored FROM movies WHERE id=?", m1) == \
        [{"server_source": "jellyfin", "server_id": "j1", "monitored": 0}]
    assert _q(db, "SELECT server_source, server_id FROM shows WHERE id=?", show) == \
        [{"server_source": "jellyfin", "server_id": "js"}]
    # the way back is kept, and what jellyfin doesn't have stays plex's
    assert _q(db, "SELECT server_source, server_id FROM video_server_links WHERE kind='movie' AND item_id=?", m1) \
        == [{"server_source": "plex", "server_id": "p1"}]
    assert _q(db, "SELECT server_source FROM movies WHERE id=?", lonely)[0]["server_source"] == "plex"
    # jellyfin's own scan now lands on the same rows: no second copy
    before = len(_q(db, "SELECT id FROM movies"))
    db.upsert_movie("jellyfin", jelly.movies[0])
    assert len(_q(db, "SELECT id FROM movies")) == before
    # and back again
    assert sl.rekey_library(db, "jellyfin", "plex")["movies"] == 4
    assert _q(db, "SELECT server_source, server_id FROM movies WHERE id=?", m1) == \
        [{"server_source": "plex", "server_id": "p1"}]


def test_a_title_the_new_server_already_has_a_row_for_is_left_alone(lib):
    db, plex, jelly = lib
    sl.link_pass(db, jelly)
    db.upsert_movie("jellyfin", jelly.movies[0])          # scanned as the video server once before
    out = sl.rekey_library(db, "plex", "jellyfin")
    assert out["skipped"] == 1 and out["movies"] == 3
    assert _q(db, "SELECT server_source FROM movies WHERE server_id='p1'") == [{"server_source": "plex"}]


# ── running it, and the routes ──────────────────────────────────────────────

def test_it_runs_only_when_on_and_with_two_servers(lib, monkeypatch):
    db, plex, jelly = lib
    db.set_setting(sl.SETTING, "0")
    assert sl.run(db, source_for=lambda n: jelly, primary_source=lambda: plex) == {"skipped": "off"}
    assert _q(db, "SELECT 1 FROM video_server_links") == []
    db.set_setting(sl.SETTING, "1")
    monkeypatch.setattr("core.video.sources.configured_video_servers", lambda _db=None: ["plex"])
    assert sl.run(db, source_for=lambda n: jelly, primary_source=lambda: plex) == {"skipped": "one server"}
    monkeypatch.setattr("core.video.sources.configured_video_servers", lambda _db=None: ["plex", "jellyfin"])
    asked = []
    res = sl.run(db, source_for=lambda n: (asked.append(n), jelly)[1], primary_source=lambda: plex)
    assert asked == ["jellyfin"] and res["movies"] == 4 and res["watched"]["failed"] == 0
    c = sl.counts(db)
    assert (c["enabled"], c["server"], c["other"], c["movies"], c["movies_total"], c["shows"]) == \
        (True, "plex", "jellyfin", 4, 5, 2)
    # a server that can't be reached is an error in the status, never an exception
    assert "error" in sl.run(db, source_for=lambda n: None, primary_source=lambda: plex)
    assert sl.status()["state"] == "error"


def test_the_routes(lib, monkeypatch):
    from flask import Flask
    import api.video as videoapi
    db, plex, jelly = lib
    started = []
    monkeypatch.setattr(sl, "run_async", lambda _db, **k: (started.append(1), True)[1])
    videoapi._video_db = db
    try:
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        c = app.test_client()
        db.set_setting(sl.SETTING, "0")
        d = c.get("/api/video/server/links").get_json()
        assert d["success"] and d["enabled"] is False and d["other"] == "jellyfin"
        d = c.post("/api/video/server/links", json={"enabled": True}).get_json()
        assert d["enabled"] is True and d["started"] is True and started == [1]
        assert c.post("/api/video/server/links", json={"run": True}).get_json()["started"] is True
        c.post("/api/video/server/links", json={"enabled": False})
        assert db.get_setting(sl.SETTING) == "0"
        # the pick moves the library when linking is on
        db.set_setting(sl.SETTING, "1")
        sl.link_pass(db, jelly)
        d = c.post("/api/video/server", json={"server": "jellyfin"}).get_json()
        assert d["moved"]["movies"] == 4
        assert db.get_setting("video_server") == "jellyfin"
    finally:
        videoapi._video_db = None


def test_a_scan_starts_the_pass(lib, monkeypatch):
    db, plex, jelly = lib
    from core.video.scanner import VideoLibraryScanner
    seen = []
    monkeypatch.setattr(sl, "run_async", lambda _db, **k: seen.append(k) or True)
    st = VideoLibraryScanner(db).scan_sync(lambda: plex, mode="full")
    assert st["state"] == "done" and seen == [{"incremental": False, "since": None}]
