"""the video side on people and permissions (#2166, #2167, #2168).

the blueprint reads the effective map (g.perms) through api/video/access.py:
movies and tv are separate switches everywhere, each kind has its own mode
and rating (tv follows the movie one), moderators decide other people's
asks, and every request path holds a member's quality to what they may
pick for that kind (resolve_request_quality)."""

from __future__ import annotations

import json

import pytest
from flask import Flask, g

from core import permissions as P
from database.video_database import VideoDatabase
from tests.permissions_fixtures import member_perms

PROFILE_BLOB = {"tiers": [{"key": "web-1080p", "enabled": True}], "cutoff_resolution": "1080p"}
UHD_BLOB = {"tiers": [{"key": "web-2160p", "enabled": True}], "cutoff_resolution": "2160p", "library": "4k"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import api.video as videoapi
    import api.video.requests as vreq
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    monkeypatch.setattr(vreq, "_tmdb_lookup", lambda kind, tid: None)
    monkeypatch.setattr(vreq, "_admin_name", lambda: "Sam")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    q = {}
    q["m1080"] = db.upsert_named_quality_profile(None, "Movie 1080p", json.dumps(PROFILE_BLOB), "1080p")
    q["m4k"] = db.upsert_named_quality_profile(None, "Movie 4K", json.dumps(UHD_BLOB), "2160p")
    q["tv"] = db.upsert_named_quality_profile(None, "TV 720p/1080p", json.dumps(PROFILE_BLOB), "1080p")
    q["tvx"] = db.upsert_named_quality_profile(None, "TV 720p/1080p EXTENDED", json.dumps(PROFILE_BLOB),
                                               "1080p")
    db.set_named_quality_profile_media_type(q["m1080"], "movie")
    db.set_named_quality_profile_media_type(q["m4k"], "movie")
    db.set_named_quality_profile_media_type(q["tv"], "tv")
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    persona = {}

    @app.before_request
    def _persona():
        for k, v in persona.items():
            setattr(g, k, v)

    def as_admin():
        persona.clear()
        persona.update({"profile_id": 1, "is_admin": True, "profile_name": "Admin",
                        "perms": P.admin_map(), "perms_source": "admin"})

    def as_member(pid=5, name="Maya", template="adult", **changes):
        persona.clear()
        persona.update({"profile_id": pid, "is_admin": False, "profile_name": name,
                        "can_download": True, "allowed_sides": "both",
                        "perms": member_perms(template, **changes), "perms_source": "template"})

    as_admin()
    try:
        yield app.test_client(), db, q, as_admin, as_member, app
    finally:
        videoapi._video_db = None


def _wish_q(db, tmdb, pid=5):
    """the quality on a profile's wishlist rows of a movie, or [] when none."""
    conn = db._get_connection()
    try:
        return [r[0] for r in conn.execute(
            "SELECT quality_profile_id FROM video_wishlist WHERE tmdb_id=? AND profile_id=?",
            (tmdb, pid))]
    finally:
        conn.close()


def _movie(client, tmdb=603, **extra):
    return client.post("/api/video/requests", json={"kind": "movie", "tmdb_id": tmdb, "title": "The Matrix",
                                                    **extra})


# ── the route table ──────────────────────────────────────────────────────

def test_every_video_route_declares_who_may_call_it(env):
    _client, _db, _q, _a, _m, app = env
    missing, kinds = [], {}
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/video"):
            continue
        mark = P.declared_access(app.view_functions[rule.endpoint])
        if mark is None:
            missing.append(rule.rule)
        else:
            kinds[mark[0]] = kinds.get(mark[0], 0) + 1
    assert not missing
    assert kinds.get("admin", 0) > 20 and kinds.get("perm", 0) > 100 and kinds.get("public") == 1


def test_route_access_table():
    from api.video.access import route_access
    assert route_access("/api/video/overlays/x", "GET") == ("admin", None)
    assert route_access("/api/video/downloads/quality/profiles", "GET") == ("perm", P_SIDE)
    assert route_access("/api/video/downloads/quality/profiles", "POST") == ("admin", None)
    assert route_access("/api/video/detail/movie/<int:movie_id>/metadata", "PUT") == ("admin", None)
    assert route_access("/api/video/downloads/grab", "POST") == ("perm", ("movies.get", "tv.get"))
    assert route_access("/api/video/youtube/follow", "POST") == ("perm", ("video.youtube",))
    assert route_access("/api/video/requests/<int:request_id>/approve", "POST") == \
        ("perm", ("requests.moderate",))
    assert route_access("/api/video/calendar/feed/<token>.ics", "GET") == ("public", None)
    assert route_access("/api/video/calendar/feed/%s.ics" % ("a" * 40), "GET") == ("public", None)


P_SIDE = ("movies.access", "tv.access")


# ── movies and tv are separate ───────────────────────────────────────────

def test_tv_only_profile_never_reaches_a_movie(env, monkeypatch):
    client, db, q, as_admin, as_member, _app = env
    as_member(**{"movies.access": False})
    # detail pages, library and tmdb: the calm refusal
    for url in ("/api/video/detail/movie/1", "/api/video/tmdb/movie/603",
                "/api/video/detail/movie/1/extras", "/api/video/poster/movie/1"):
        r = client.get(url)
        assert r.status_code == 403, url
        assert r.get_json()["permission"] == "movies.access", url
    assert client.get("/api/video/library?kind=movies").get_json()["items"] == []
    # search, discover-style lists: movies drop, shows and people stay
    import core.video.enrichment.engine as eng_mod

    class _Eng:
        def search(self, q):
            return [{"kind": "movie", "tmdb_id": 1}, {"kind": "show", "tmdb_id": 2},
                    {"kind": "person", "tmdb_id": 3}]

        def trending(self):
            return [{"kind": "movie", "tmdb_id": 1}, {"kind": "show", "tmdb_id": 2}]
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Eng())
    kinds = [x["kind"] for x in client.get("/api/video/search?q=x").get_json()["results"]]
    assert kinds == ["show", "person"]
    assert [x["kind"] for x in client.get("/api/video/trending").get_json()["results"]] == ["show"]
    # asking, getting and wishing a movie
    assert _movie(client).status_code == 403
    r = client.post("/api/video/wishlist/add", json={"movie": {"tmdb_id": 603, "title": "M"}})
    assert r.status_code == 403 and r.get_json()["permission"] == "movies.access"
    r = client.post("/api/video/downloads/grab", json={"kind": "movie", "title": "M"})
    assert r.status_code == 403
    # a show still works
    ok = client.post("/api/video/requests", json={"kind": "show", "tmdb_id": 9, "title": "Severance"})
    assert ok.status_code == 200 and ok.get_json()["success"]
    # the wishlist and the calendar hold no movies for them
    as_admin()
    db.add_movie_to_wishlist(603, "The Matrix", profile_id=5)
    as_member(**{"movies.access": False})
    items = client.get("/api/video/wishlist?kind=movie").get_json()["items"]
    assert items == []
    monkeypatch.setattr(db, "calendar_movie_releases", lambda *a, **k: [{"tmdb_id": 603, "title": "M"}])
    monkeypatch.setattr(db, "calendar_upcoming", lambda *a, **k: [])
    assert client.get("/api/video/calendar").get_json()["movies"] == []


def test_movie_only_profile_and_request_lists(env):
    client, db, q, as_admin, as_member, _app = env
    as_member(pid=6, name="Kim")
    assert client.post("/api/video/requests", json={"kind": "show", "tmdb_id": 9, "title": "S"}).status_code == 200
    assert _movie(client).status_code == 200
    # a moderator who can't see shows decides only movies, and sees only movies
    as_member(pid=7, name="Mo", **{"tv.access": False, "requests.moderate": True})
    rows = client.get("/api/video/requests").get_json()["requests"]
    assert [r["kind"] for r in rows] == ["movie"]
    show = [r for r in db.list_video_requests() if r["kind"] == "show"][0]
    assert client.post("/api/video/requests/%d/approve" % show["id"]).status_code == 403


def test_music_only_profile_gets_nothing(env):
    client, _db, _q, _a, as_member, _app = env
    as_member(template="guest")
    r = client.get("/api/video/requests")
    assert r.status_code == 403 and r.get_json()["error"] == "Video access is disabled for this profile."


# ── ratings: tv follows the movie one ────────────────────────────────────

def test_tv_rating_follows_the_movie_rating():
    from api.video.kids import VideoCap, cap_from_perms, kind_allows
    teen = cap_from_perms(member_perms("teen"))           # PG-13, tv follows
    assert teen == VideoCap(14, 14, ())
    assert kind_allows(teen, "show", "TV-14") and not kind_allows(teen, "show", "TV-MA")
    assert kind_allows(teen, "movie", "PG-13") and not kind_allows(teen, "movie", "R")
    own = cap_from_perms(member_perms("teen", **{"tv.max_rating": "PG"}))
    assert own == VideoCap(14, 8, ())
    assert not kind_allows(own, "show", "TV-14") and kind_allows(own, "show", "TV-PG")
    # a raw map that still says "follow" (a legacy read) follows too
    raw = dict(member_perms("adult"), **{"movies.max_rating": "PG", "tv.max_rating": P.FOLLOW})
    assert cap_from_perms(raw) == VideoCap(8, 8, ())
    assert cap_from_perms(member_perms("adult")) is None
    assert cap_from_perms(P.admin_map(), is_admin=True) is None
    assert cap_from_perms(None) == VideoCap(0, 0, ("movie", "show"))      # can't build: nothing


def test_tv_rating_of_its_own_holds_on_the_api(env, monkeypatch):
    client, db, _q, _a, as_member, _app = env
    import api.video.kids as kids
    monkeypatch.setattr(kids, "tmdb_title_rating", lambda db, kind, tid: "TV-14")
    as_member(**{"movies.max_rating": "PG-13"})
    ok = client.post("/api/video/requests", json={"kind": "show", "tmdb_id": 9, "title": "S"})
    assert ok.status_code == 200
    as_member(pid=6, **{"movies.max_rating": "PG-13", "tv.max_rating": "PG"})
    r = client.post("/api/video/requests", json={"kind": "show", "tmdb_id": 10, "title": "S2"})
    assert r.status_code == 403 and r.get_json()["error"] == "restricted"


# ── modes ────────────────────────────────────────────────────────────────

def test_modes_are_per_kind(env):
    client, _db, _q, _a, as_member, _app = env
    as_member(**{"movies.get": "ask"})              # music + tv download, movies ask
    r = client.post("/api/video/wishlist/add", json={"movie": {"tmdb_id": 603, "title": "M"}})
    assert r.status_code == 403 and r.get_json()["permission"] == "movies.get"
    r = client.post("/api/video/wishlist/add", json={"show": {"tmdb_id": 9, "title": "S"},
                                                     "episodes": [{"season_number": 1, "episode_number": 1}]})
    assert r.status_code == 200 and r.get_json()["success"]
    as_member(**{"tv.get": "none"})
    r = client.post("/api/video/requests", json={"kind": "show", "tmdb_id": 9, "title": "S"})
    assert r.status_code == 403 and r.get_json()["permission"] == "tv.get"


# ── request quality on every path ────────────────────────────────────────

def test_request_dialog_quality(env):
    client, db, q, _a, as_member, _app = env
    limited = {"movies.quality": [q["m1080"], q["m4k"]], "movies.quality_default": q["m1080"],
               "movies.quality_ask": ["4k"], "movies.get": "ask"}
    as_member(**limited)
    # a tv profile on a movie, or one outside the set: refused calmly
    for bad in (q["tv"], 0):
        r = _movie(client, quality_profile_id=bad)
        assert r.status_code == 403 and r.get_json()["error"] == P.QUALITY_NOT_ALLOWED
    # no pick: the default, stored resolved
    assert _movie(client).status_code == 200
    assert db.list_video_requests()[0]["quality_profile_id"] == q["m1080"]
    # an unknown id (a seerr-ish client): the default
    assert _movie(client, tmdb=604, quality_profile_id=999).status_code == 200
    assert [r for r in db.list_video_requests() if r["tmdb_id"] == 604][0]["quality_profile_id"] == q["m1080"]


def test_options_endpoint_is_what_the_dialog_shows(env):
    client, _db, q, _a, as_member, _app = env
    as_member(**{"tv.quality": [q["tv"], q["tvx"]], "tv.quality_default": q["tv"],
                 "tv.quality_ask": [q["tvx"]], "requests.limit": {"n": 5, "days": 7}})
    d = client.get("/api/video/requests/options?kind=show").get_json()
    assert d["mode"] == "download" and d["asks_who"] == "Sam"
    assert [(x["id"], x["default"], x["asks"]) for x in d["qualities"]] == [
        (q["tv"], True, False), (q["tvx"], False, True)]
    assert d["quota"] == {"limit": 5, "days": 7, "used": 0, "remaining": 5}
    assert all("profile" not in x for x in d["qualities"])
    one = client.get("/api/video/requests/options").get_json()
    assert one["modes"] == {"movie": "download", "show": "download", "youtube": "download"}
    as_member(**{"movies.get": "none", "movies.quality": [q["m1080"]]})
    d = client.get("/api/video/requests/options?kind=movie").get_json()
    assert d["mode"] == "none" and d["qualities"] == []
    d = client.get("/api/video/requests/options?kind=show").get_json()
    # an open set: every profile for shows (both + tv), none for movies
    assert {x["name"] for x in d["qualities"]} == {"Default", "TV 720p/1080p", "TV 720p/1080p EXTENDED"}


def test_straight_get_and_ask_first_quality_becomes_a_request(env):
    client, db, q, _a, as_member, _app = env
    as_member(**{"movies.quality": [q["m1080"], q["m4k"]], "movies.quality_ask": ["4k"],
                 "movies.quality_default": q["m1080"]})
    r = _movie(client, get=True).get_json()
    assert r["got"] and r["quality_profile_id"] == q["m1080"]
    assert db.list_video_requests() == []
    assert _wish_q(db, 603) == [q["m1080"]]
    r = _movie(client, tmdb=604, get=True, quality_profile_id=q["m4k"]).get_json()
    assert r["asked"] and not r.get("got")
    req = db.list_video_requests()[0]
    assert req["tmdb_id"] == 604 and req["quality_profile_id"] == q["m4k"]


def test_wishlist_and_watchlist_adds_hold_the_quality(env):
    client, db, q, _a, as_member, _app = env
    as_member(**{"movies.quality": [q["m1080"], q["m4k"]], "movies.quality_ask": [q["m4k"]],
                 "tv.quality": [q["tvx"]], "tv.quality_ask": [q["tvx"]]})
    r = client.post("/api/video/wishlist/add", json={"movie": {"tmdb_id": 603, "title": "M"},
                                                     "quality_profile_id": q["tv"]})
    assert r.status_code == 403 and r.get_json()["error"] == P.QUALITY_NOT_ALLOWED
    r = client.post("/api/video/wishlist/add", json={"movie": {"tmdb_id": 603, "title": "M"}})
    assert r.get_json()["success"]
    assert _wish_q(db, 603) == [q["m1080"]]
    # an ask-first quality turns the add into a request
    r = client.post("/api/video/wishlist/add", json={"movie": {"tmdb_id": 605, "title": "N"},
                                                     "quality_profile_id": q["m4k"]}).get_json()
    assert r["asked"] and db.list_video_requests()[0]["tmdb_id"] == 605
    # following a show whose only quality asks first: a show request
    r = client.post("/api/video/watchlist/add", json={"kind": "show", "tmdb_id": 9, "title": "S"}).get_json()
    assert r["asked"]
    assert [x for x in db.list_video_requests() if x["kind"] == "show"][0]["quality_profile_id"] == q["tvx"]


def test_manual_search_and_grab_hold_the_quality(env):
    client, _db, q, _a, as_member, _app = env
    as_member(**{"movies.quality": [q["m1080"], q["m4k"]], "movies.quality_ask": ["4k"]})
    r = client.post("/api/video/downloads/search", json={"scope": "movie", "title": "M",
                                                         "quality_profile_id": q["tv"]})
    assert r.status_code == 403 and r.get_json()["error"] == P.QUALITY_NOT_ALLOWED
    r = client.post("/api/video/downloads/grab", json={"kind": "movie", "title": "M", "source": "soulseek",
                                                       "quality_profile_id": q["m4k"]})
    assert r.status_code == 403 and r.get_json()["error"] == "needs_approval"


def test_member_held_to_their_set_when_the_title_has_another(env):
    from api.video import downloads as dl
    client, db, q, _a, as_member, app = env
    db.set_setting("quality_default_profile_movie", str(q["m4k"]))
    as_member(**{"movies.quality": [q["m1080"]]})
    with app.test_request_context("/api/video/downloads/search"):
        for k, v in {"profile_id": 5, "is_admin": False, "perms": member_perms(
                "adult", **{"movies.quality": [q["m1080"]]})}.items():
            setattr(g, k, v)
        _prof, pid = dl._profile_for_request(db, {"scope": "movie", "title": "M"})
        assert pid == q["m1080"]
        g.is_admin, g.perms = True, P.admin_map()
        g._video_perm_ctx = None
        del g._video_perm_ctx
        _prof, pid = dl._profile_for_request(db, {"scope": "movie", "title": "M"})
        assert pid == q["m4k"]


# ── members never read the blobs ─────────────────────────────────────────

def test_quality_profiles_list_for_members_and_admins(env):
    client, _db, q, as_admin, as_member, _app = env
    full = client.get("/api/video/downloads/quality/profiles").get_json()["profiles"]
    assert all("profile" in p for p in full)
    m4k = [p for p in full if p["id"] == q["m4k"]][0]
    assert m4k["media_type"] == "movie" and "4k" in m4k["tags"]
    as_member(**{"movies.quality": [q["m1080"]], "tv.quality": [q["tv"]]})
    mine = client.get("/api/video/downloads/quality/profiles").get_json()["profiles"]
    assert {p["id"] for p in mine} == {q["m1080"], q["tv"]}
    assert all(set(p) == {"id", "name", "tags"} for p in mine)
    assert [p["id"] for p in client.get("/api/video/downloads/quality/profiles?kind=show")
            .get_json()["profiles"]] == [q["tv"]]
    r = client.get("/api/video/downloads/quality/for?kind=movie&tmdb_id=603").get_json()
    assert [p["id"] for p in r["profiles"]] == [q["m1080"]] and r["member"]


def test_media_type_is_set_by_admins_only(env):
    client, db, q, _a, as_member, _app = env
    r = client.put("/api/video/downloads/quality/profiles/%d/media-type" % q["tvx"], json={"media_type": "tv"})
    assert r.get_json() == {"success": True, "id": q["tvx"], "media_type": "tv"}
    assert client.put("/api/video/downloads/quality/profiles/0/media-type",
                      json={"media_type": "tv"}).status_code == 400
    assert client.put("/api/video/downloads/quality/profiles/%d/media-type" % q["tvx"],
                      json={"media_type": "book"}).status_code == 400
    as_member()
    r = client.put("/api/video/downloads/quality/profiles/%d/media-type" % q["tvx"], json={"media_type": None})
    assert r.status_code == 403
    assert db.get_named_quality_profile(q["tvx"])["media_type"] == "tv"


# ── moderators, see everyone's ───────────────────────────────────────────

def test_moderator_decides_others_never_their_own(env, monkeypatch):
    client, db, q, as_admin, as_member, _app = env
    as_member(pid=5, name="Maya", **{"movies.get": "ask"})
    assert _movie(client).status_code == 200
    as_member(pid=6, name="Kim", **{"movies.get": "ask"})
    assert _movie(client, tmdb=700).status_code == 200
    # a plain member can't decide anything
    rid = [r for r in db.list_video_requests() if r["tmdb_id"] == 603][0]["id"]
    assert client.post("/api/video/requests/%d/approve" % rid).status_code == 403
    # the moderator approves Maya's, but not their own
    as_member(pid=6, name="Kim", **{"movies.get": "ask", "requests.moderate": True})
    rows = client.get("/api/video/requests").get_json()
    assert rows["moderator"] and rows["see_all"]
    decide = {r["tmdb_id"]: r["can_decide"] for r in rows["requests"]}
    assert decide == {603: True, 700: False}
    own = [r for r in db.list_video_requests() if r["tmdb_id"] == 700][0]["id"]
    r = client.post("/api/video/requests/%d/approve" % own)
    assert r.status_code == 403 and r.get_json()["error"] == "own_request"
    assert client.post("/api/video/requests/%d/deny" % own).status_code == 403
    # a moderator's quality pick is held to the requester's set
    import api.video.requests as vreq
    monkeypatch.setattr(vreq, "_requester_context", lambda pid: P.make_context(
        pid, perms=member_perms("adult", **{"movies.quality": [q["m1080"]]})))
    r = client.post("/api/video/requests/%d/approve" % rid, json={"quality_profile_id": q["m4k"]})
    assert r.status_code == 403 and r.get_json()["error"] == P.QUALITY_NOT_ALLOWED
    assert client.post("/api/video/requests/%d/approve" % rid,
                       json={"quality_profile_id": q["m1080"]}).get_json()["success"]
    assert _wish_q(db, 603) == [q["m1080"]]
    # approve-all skips their own
    out = client.post("/api/video/requests/approve-all").get_json()
    assert out["skipped"] == 1 and out["approved"] == 0


def test_see_all_without_moderating(env):
    client, _db, _q, _a, as_member, _app = env
    as_member(pid=5, **{"movies.get": "ask"})
    assert _movie(client).status_code == 200
    as_member(pid=6)
    assert client.get("/api/video/requests").get_json()["requests"] == []
    as_member(pid=6, **{"requests.see_all": True})
    rows = client.get("/api/video/requests").get_json()
    assert len(rows["requests"]) == 1 and not rows["requests"][0]["can_decide"] and not rows["moderator"]
    assert client.get("/api/video/requests/counts").get_json()["pending"] == 1


def test_request_limit_reads_the_permission(env):
    client, _db, _q, _a, as_member, _app = env
    as_member(**{"movies.get": "ask", "requests.limit": {"n": 1, "days": 7}})
    assert _movie(client).status_code == 200
    r = _movie(client, tmdb=604)
    assert r.status_code == 429 and r.get_json()["quota"]["remaining"] == 0


def test_youtube_follows_need_their_own_row(env):
    client, _db, _q, _a, as_member, _app = env
    as_member(**{"video.youtube": False})
    r = client.post("/api/video/youtube/follow", json={"channel_id": "x"})
    assert r.status_code == 403 and r.get_json()["permission"] == "video.youtube"
    r = client.post("/api/video/requests", json={"kind": "youtube", "youtube_id": "dQw4w9WgXcQ", "title": "v"})
    assert r.status_code == 403


def test_unmapped_request_reads_the_old_stamps(env):
    """a request web_server didn't stamp with g.perms (an edge caller): the old
    g columns, never more than they said."""
    client, _db, _q, _a, _m, app = env
    import api.video as videoapi  # noqa: F401
    with app.test_request_context("/api/video/requests"):
        g.profile_id, g.is_admin, g.allowed_sides, g.can_download = 5, False, "video", False
        from api.video import access
        assert access.mode("movie") == "ask" and access.has_kind("show")
        with pytest.raises(LookupError):
            access.quota_row(5)       # no limit on record is not "no limit"
