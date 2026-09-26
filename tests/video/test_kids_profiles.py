"""kids profiles on the video side, through the real blueprint routes.

profile 2 is a kid capped at PG. profile 3 is a plain member, no cap.
profile 1 is the admin. the profile rows come through the real
current_restrictions() reader with only the profile load stubbed.
"""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase

KID, MEMBER = 2, 3
_PROFILES = {
    KID: {"id": KID, "is_admin": False, "hide_explicit": True, "max_rating": "PG"},
    MEMBER: {"id": MEMBER, "is_admin": False, "hide_explicit": False, "max_rating": None},
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.content_filter as cf
    import core.video.sources as vsrc
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    monkeypatch.setattr(cf, "_load_profile", lambda pid: _PROFILES.get(pid))
    monkeypatch.setattr(vsrc, "resolve_video_server", lambda *a, **k: "plex")
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _fake_profile():
        from flask import g, request
        g.profile_id = int(request.headers.get("X-Test-Profile", 1))
        g.profile_name = "Tester %s" % g.profile_id
        g.can_download = True
        g.allowed_sides = "both"
        g.is_admin = g.profile_id == 1

    try:
        yield app.test_client(), videoapi._video_db
    finally:
        videoapi._video_db = None


def _as(pid):
    return {"X-Test-Profile": str(pid)}


def _movie(db, sid, title, rating, tmdb_id, added="2026-09-01 10:00:00"):
    mid = db.upsert_movie("plex", {"server_id": sid, "title": title, "year": 2020,
                                   "tmdb_id": tmdb_id, "content_rating": rating})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET added_at=? WHERE id=?", (added, mid))
    conn.commit()
    conn.close()
    return mid


def _show(db, sid, title, rating, tmdb_id):
    return db.upsert_show_tree("plex", {
        "server_id": sid, "title": title, "tmdb_id": tmdb_id, "content_rating": rating,
        "seasons": [{"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "Pilot", "air_date": "2026-09-25"}]}]})


# ── hard blocks ──────────────────────────────────────────────────────────────

def test_movie_detail_over_cap_is_refused_for_the_kid_only(client):
    c, db = client
    r_id = _movie(db, "m1", "Heat", "R", 949)
    pg_id = _movie(db, "m2", "Paddington", "PG", 116149)
    nr_id = _movie(db, "m3", "Mystery", None, 777)

    r = c.get(f"/api/video/detail/movie/{r_id}", headers=_as(KID))
    assert r.status_code == 403
    assert r.get_json() == {"success": False, "error": "restricted", "restricted": True}
    # unrated fails closed
    assert c.get(f"/api/video/detail/movie/{nr_id}", headers=_as(KID)).status_code == 403
    assert c.get(f"/api/video/detail/movie/{pg_id}", headers=_as(KID)).status_code == 200
    # admin and an uncapped member are untouched
    assert c.get(f"/api/video/detail/movie/{r_id}").status_code == 200
    assert c.get(f"/api/video/detail/movie/{r_id}", headers=_as(MEMBER)).status_code == 200


def test_show_detail_and_extras_over_cap_are_refused(client, monkeypatch):
    c, db = client
    sid = _show(db, "s1", "The Wire", "TV-MA", 1438)
    ok = _show(db, "s2", "Bluey", "TV-Y", 82728)
    assert c.get(f"/api/video/detail/show/{sid}", headers=_as(KID)).status_code == 403
    assert c.get(f"/api/video/detail/show/{ok}", headers=_as(KID)).status_code == 200
    assert c.get(f"/api/video/detail/show/{sid}").status_code == 200

    import core.video.enrichment.engine as eng_mod

    class _Eng:
        def item_extras(self, kind, item_id):
            return {"trailer": {"key": "x"}}
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Eng())
    assert c.get(f"/api/video/detail/show/{sid}/extras", headers=_as(KID)).status_code == 403
    assert c.get(f"/api/video/detail/show/{ok}/extras", headers=_as(KID)).status_code == 200


def test_tmdb_detail_uses_its_certification(client, monkeypatch):
    c, db = client
    import core.video.enrichment.engine as eng_mod
    certs = {1: "R", 2: "PG", 3: None}

    class _Eng:
        def tmdb_detail(self, kind, tmdb_id):
            return {"source": "tmdb", "tmdb_id": tmdb_id, "title": "T", "content_rating": certs[tmdb_id]}

        def tmdb_season(self, tv_id, n):
            return {"episodes": []}
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Eng())
    assert c.get("/api/video/tmdb/movie/1", headers=_as(KID)).status_code == 403
    assert c.get("/api/video/tmdb/movie/2", headers=_as(KID)).status_code == 200
    assert c.get("/api/video/tmdb/movie/3", headers=_as(KID)).status_code == 403
    assert c.get("/api/video/tmdb/movie/1").status_code == 200
    # a show's season listing goes by the show's certification
    assert c.get("/api/video/tmdb/show/1/season/1", headers=_as(KID)).status_code == 403
    assert c.get("/api/video/tmdb/show/2/season/1", headers=_as(KID)).status_code == 200


def test_watch_stream_refuses_over_cap_movie_and_episode(client):
    c, db = client
    _movie(db, "m1", "Heat", "R", 949)
    _movie(db, "m2", "Paddington", "PG", 116149)
    _show(db, "s1", "The Wire", "TV-MA", 1438)

    r = c.get("/api/video/watch/stream?kd=m&id=949", headers=_as(KID))
    assert r.status_code == 403 and r.get_json()["restricted"] is True
    # an episode is judged by its show
    r = c.get("/api/video/watch/stream?kd=t&id=1438&s=1&e=1", headers=_as(KID))
    assert r.status_code == 403
    r = c.get("/api/video/watch/playable?kd=m&id=949", headers=_as(KID))
    assert r.status_code == 403 and r.get_json()["restricted"] is True
    # allowed title gets past the guard to the normal "no file" answer
    assert c.get("/api/video/watch/stream?kd=m&id=116149", headers=_as(KID)).status_code == 404
    # admin is not stopped by the guard (no file here, so 404)
    assert c.get("/api/video/watch/stream?kd=m&id=949").status_code == 404


def test_discover_trailer_refused_over_cap(client, monkeypatch):
    c, db = client
    _movie(db, "m1", "Heat", "R", 949)
    import core.video.enrichment.engine as eng_mod

    class _Eng:
        def trailer(self, kind, tmdb_id):
            return {"key": "abc"}
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Eng())
    assert c.get("/api/video/discover/trailer?kind=movie&tmdb_id=949", headers=_as(KID)).status_code == 403
    assert c.get("/api/video/discover/trailer?kind=movie&tmdb_id=949").get_json()["trailer"] == {"key": "abc"}


# ── browse filtering ────────────────────────────────────────────────────────

def test_library_listing_hides_over_cap_and_pages_honestly(client):
    c, db = client
    for i in range(5):
        _movie(db, f"ok{i}", f"Kid {i}", "G", 1000 + i)
    for i in range(3):
        _movie(db, f"no{i}", f"Adult {i}", "R", 2000 + i)
    _movie(db, "nr", "Unrated", None, 3000)

    d = c.get("/api/video/library?kind=movies&limit=2&page=1", headers=_as(KID)).get_json()
    assert d["pagination"]["total_count"] == 5 and d["pagination"]["total_pages"] == 3
    titles = []
    for p in (1, 2, 3):
        d = c.get(f"/api/video/library?kind=movies&limit=2&page={p}", headers=_as(KID)).get_json()
        titles += [it["title"] for it in d["items"]]
    assert sorted(titles) == [f"Kid {i}" for i in range(5)]

    full = c.get("/api/video/library?kind=movies&limit=50").get_json()
    assert full["pagination"]["total_count"] == 9
    member = c.get("/api/video/library?kind=movies&limit=50", headers=_as(MEMBER)).get_json()
    assert member == full


def test_dashboard_recent_and_calendar_hide_over_cap(client):
    c, db = client
    _movie(db, "m1", "Heat", "R", 949)
    _movie(db, "m2", "Paddington", "PG", 116149)
    _show(db, "s1", "The Wire", "TV-MA", 1438)
    _show(db, "s2", "Bluey", "TV-Y", 82728)
    conn = db._get_connection()
    conn.execute("UPDATE shows SET added_at='2026-09-02 10:00:00'")
    conn.commit()
    conn.close()

    kid = {r["title"] for r in c.get("/api/video/dashboard", headers=_as(KID)).get_json()["recent"]}
    assert kid == {"Paddington", "Bluey"}
    admin = {r["title"] for r in c.get("/api/video/dashboard").get_json()["recent"]}
    assert admin == {"Heat", "Paddington", "The Wire", "Bluey"}

    kid_cal = c.get("/api/video/calendar?scope=all&start=2026-09-25&days=1", headers=_as(KID)).get_json()
    assert {e["show_title"] for e in kid_cal["episodes"]} == {"Bluey"}
    assert kid_cal["total"] == 1
    adm_cal = c.get("/api/video/calendar?scope=all&start=2026-09-25&days=1").get_json()
    assert {e["show_title"] for e in adm_cal["episodes"]} == {"Bluey", "The Wire"}


def test_search_keeps_only_titles_the_library_vouches_for(client, monkeypatch):
    c, db = client
    _movie(db, "m1", "Heat", "R", 949)
    _movie(db, "m2", "Paddington", "PG", 116149)
    import core.video.enrichment.engine as eng_mod
    results = [{"kind": "movie", "tmdb_id": 949, "title": "Heat"},
               {"kind": "movie", "tmdb_id": 116149, "title": "Paddington"},
               {"kind": "movie", "tmdb_id": 5, "title": "Not owned"},
               {"kind": "person", "tmdb_id": 9, "title": "Someone"}]

    class _Eng:
        def search(self, q):
            return [dict(r) for r in results]
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Eng())
    kid = [r["title"] for r in c.get("/api/video/search?q=x", headers=_as(KID)).get_json()["results"]]
    assert kid == ["Paddington", "Someone"]
    adm = [r["title"] for r in c.get("/api/video/search?q=x").get_json()["results"]]
    assert adm == ["Heat", "Paddington", "Not owned", "Someone"]
