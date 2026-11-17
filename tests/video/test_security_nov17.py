"""video side security gaps closed nov 17 (S8, S9, S16, S19, S21, S22).

through the real blueprint and its gate, a real video db in a temp folder.
profile 1 is the admin. 3 is a plain member who may download, 4 a member
with downloads off, 2 a kid capped at PG. the kids rule reads the profile
rows through the real current_restrictions() with only the load stubbed.
"""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase

ADMIN, KID, MEMBER, NODL = 1, 2, 3, 4
_PROFILES = {
    KID: {"id": KID, "is_admin": False, "hide_explicit": True, "max_rating": "PG"},
    MEMBER: {"id": MEMBER, "is_admin": False, "max_rating": None},
    NODL: {"id": NODL, "is_admin": False, "max_rating": None},
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    import api.video.requests as vreq
    import core.content_filter as cf
    import core.video.download_monitor as mon
    import core.video.sources as vsrc
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    monkeypatch.setattr(cf, "_load_profile", lambda pid: _PROFILES.get(pid))
    monkeypatch.setattr(vsrc, "resolve_video_server", lambda *a, **k: "plex")
    monkeypatch.setattr(vreq, "_tmdb_lookup", lambda kind, tid: None)
    monkeypatch.setattr(mon, "ensure_started", lambda *_a, **_k: None)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _fake_profile():
        from flask import g, request
        g.profile_id = int(request.headers.get("X-Test-Profile", ADMIN))
        g.profile_name = "Tester %s" % g.profile_id
        g.is_admin = g.profile_id == ADMIN
        g.can_download = g.profile_id != NODL
        g.allowed_sides = "both"
        if request.headers.get("X-Test-No-Stamp") != "1":
            g.request_limit = int(request.headers.get("X-Test-Limit", 0))
            g.request_limit_days = 7

    try:
        yield app.test_client(), videoapi._video_db
    finally:
        videoapi._video_db = None


def _as(pid, **extra):
    return {"X-Test-Profile": str(pid), **extra}


def _download(db, status="downloading", owner=None, title="Dune"):
    rid = db.add_video_download({"kind": "movie", "title": title, "status": status,
                                 "source": "soulseek", "username": "u", "filename": "f.mkv",
                                 "profile_id": owner})
    return rid


def _movie(db, sid, title, rating, tmdb_id):
    return db.upsert_movie("plex", {"server_id": sid, "title": title, "year": 2020,
                                    "tmdb_id": tmdb_id, "content_rating": rating})


# ── S8: the household's queue, bin, history and scans are the admin's ───────

@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/video/downloads/recycle/purge", {"all": True}),
    ("post", "/api/video/downloads/recycle/restore", {"all": True}),
    ("post", "/api/video/downloads/history/clear", {}),
    ("delete", "/api/video/downloads/history/1", None),
    ("post", "/api/video/downloads/remove", {"id": 1}),
    ("post", "/api/video/downloads/clear", {"scope": "everything"}),
    ("post", "/api/video/scan/request", {"mode": "full"}),
    ("post", "/api/video/scan/stop", {}),
    ("post", "/api/video/scan/server", {}),
])
def test_household_actions_refuse_a_member(client, method, path, body):
    c, _db = client
    kw = {"json": body} if body is not None else {}
    r = getattr(c, method)(path, headers=_as(MEMBER), **kw)
    assert r.status_code == 403 and r.get_json()["error"] == "Admin only."


def test_the_admin_still_clears_and_purges(client):
    c, db = client
    _download(db, status="completed")
    assert c.post("/api/video/downloads/clear", json={}).get_json()["cleared"] == 1
    assert c.post("/api/video/downloads/recycle/purge", json={"all": True}).status_code == 200
    assert c.post("/api/video/downloads/history/clear", json={}).status_code == 200
    # the reads a member's pages make stay open
    assert c.get("/api/video/scan/status", headers=_as(MEMBER)).status_code == 200
    assert c.get("/api/video/downloads/recycle", headers=_as(MEMBER)).status_code == 200


def test_a_member_cancels_only_a_grab_they_started(client, monkeypatch):
    import core.video.slskd_download as sd
    cancelled = []
    monkeypatch.setattr(sd, "cancel_download", lambda u, f: cancelled.append(f))
    c, db = client
    theirs = _download(db, owner=MEMBER)
    admins = _download(db, owner=ADMIN)
    automatic = _download(db, owner=None)
    for other in (admins, automatic):
        r = c.post("/api/video/downloads/cancel", json={"id": other}, headers=_as(MEMBER))
        assert r.status_code == 403
        assert db.get_video_download(other)["status"] == "downloading"
    assert c.post("/api/video/downloads/cancel", json={"id": theirs},
                  headers=_as(MEMBER)).get_json()["ok"] is True
    assert db.get_video_download(theirs)["status"] == "cancelled"
    # an admin cancels anything, an automatic grab included
    assert c.post("/api/video/downloads/cancel", json={"id": automatic}).get_json()["ok"] is True
    assert len(cancelled) == 2


def test_a_hand_grab_is_recorded_under_who_started_it(client, monkeypatch):
    import api.video.downloads as vdl
    import core.video.disk_guard as dg
    import core.video.slskd_download as sd
    monkeypatch.setattr(sd, "start_download", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(dg, "check_room", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(vdl, "_target_for", lambda *a, **k: "/lib/movies")
    c, db = client
    r = c.post("/api/video/downloads/grab", headers=_as(MEMBER), json={
        "kind": "movie", "title": "Dune", "source": "soulseek", "username": "u",
        "filename": "Dune.2021.mkv", "size_bytes": 1})
    assert r.status_code == 200, r.get_json()
    assert db.get_video_download(r.get_json()["id"])["profile_id"] == MEMBER


# ── S22: a member's queue and history are their own ─────────────────────────

def test_a_member_sees_only_their_own_queue(client):
    c, db = client
    mine = _download(db, owner=MEMBER, title="Mine")
    _download(db, owner=ADMIN, title="Admin's")
    _download(db, owner=None, title="Automatic")
    rows = c.get("/api/video/downloads/active", headers=_as(MEMBER)).get_json()["downloads"]
    assert [r["id"] for r in rows] == [mine]
    assert rows[0]["can_cancel"] is True
    everyone = c.get("/api/video/downloads/active").get_json()["downloads"]
    assert len(everyone) == 3 and all(r["can_cancel"] for r in everyone)
    # one row by id: not someone else's
    other = everyone[-1]["id"] if everyone[-1]["id"] != mine else everyone[0]["id"]
    assert c.get("/api/video/downloads/status?id=%d" % other,
                 headers=_as(MEMBER)).get_json()["download"] is None
    assert c.get("/api/video/downloads/status?id=%d" % mine,
                 headers=_as(MEMBER)).get_json()["download"]["id"] == mine


def test_a_member_sees_only_their_own_history(client):
    c, db = client
    for owner, title in ((MEMBER, "Mine"), (ADMIN, "Admin's"), (None, "Automatic")):
        dl = db.get_video_download(_download(db, status="completed", owner=owner, title=title))
        db.record_download_history({**dl, "status": "completed", "completed_at": "2026-11-17 10:00:00"})
    body = c.get("/api/video/downloads/history", headers=_as(MEMBER)).get_json()
    assert [i["title"] for i in body["items"]] == ["Mine"]
    assert body["counts"]["total"] == 1
    all_rows = c.get("/api/video/downloads/history").get_json()
    assert all_rows["counts"]["total"] == 3
    others = [i["id"] for i in all_rows["items"] if i["title"] != "Mine"]
    assert c.get("/api/video/downloads/history/%d" % others[0],
                 headers=_as(MEMBER)).status_code == 404


@pytest.mark.parametrize("path,body", [
    ("/api/video/discover/languages", {"languages": ["ko"]}),
    ("/api/video/discover/providers-pref", {"providers": [8]}),
])
def test_discover_household_prefs_are_admin_writes(client, path, body):
    c, db = client
    assert c.post(path, json=body, headers=_as(MEMBER)).status_code == 403
    assert c.get(path, headers=_as(MEMBER)).status_code == 200
    assert c.post(path, json=body).get_json()["success"] is True


# ── S9: following on YouTube is a download ─────────────────────────────────

@pytest.mark.parametrize("path,body", [
    ("/api/video/youtube/follow", {"channel": {"youtube_id": "UC1", "title": "C"}}),
    ("/api/video/youtube/subscriptions/import", {"text": "x"}),
    ("/api/video/youtube/playlist/follow", {"playlist": {"playlist_id": "PL1", "title": "P"}}),
    ("/api/video/youtube/channel/UC1/settings", {"custom_name": "x"}),
])
def test_youtube_follows_need_downloads(client, path, body):
    c, _db = client
    r = c.post(path, json=body, headers=_as(NODL))
    assert r.status_code == 403 and r.get_json()["error"] == "Downloads are disabled for this profile."


def test_youtube_follow_still_works_with_downloads_on(client):
    c, db = client
    r = c.post("/api/video/youtube/follow", headers=_as(MEMBER),
               json={"channel": {"youtube_id": "UC1", "title": "C"}})
    assert r.status_code == 200 and r.get_json()["following"] is True
    # reading a channel's settings, and the channel's video pages, stay open
    assert c.get("/api/video/youtube/channel/UC1/settings", headers=_as(NODL)).status_code == 200


# ── S16: the kids cap holds on every way to acquire, and on the leaks ───────

def test_kids_cap_on_requests_wishlist_watchlist(client):
    c, db = client
    _movie(db, "m1", "Heat", "R", 949)
    _movie(db, "m2", "Paddington", "PG", 116149)
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "The Wire", "tmdb_id": 1438,
                                 "content_rating": "TV-MA", "seasons": []})
    r = c.post("/api/video/requests", headers=_as(KID),
               json={"kind": "movie", "tmdb_id": 949, "title": "Heat"})
    assert r.status_code == 403 and r.get_json()["restricted"] is True
    r = c.post("/api/video/requests", headers=_as(KID),
               json={"kind": "episode", "tmdb_id": 1438, "title": "The Wire", "season": 1, "episode": 1})
    assert r.status_code == 403
    r = c.post("/api/video/wishlist/add", headers=_as(KID),
               json={"movie": {"tmdb_id": 949, "title": "Heat"}})
    assert r.status_code == 403
    r = c.post("/api/video/watchlist/add", headers=_as(KID),
               json={"kind": "show", "tmdb_id": 1438, "title": "The Wire"})
    assert r.status_code == 403
    # under the cap goes through, and the admin is never capped
    assert c.post("/api/video/wishlist/add", headers=_as(KID),
                  json={"movie": {"tmdb_id": 116149, "title": "Paddington"}}).status_code == 200
    assert c.post("/api/video/wishlist/add",
                  json={"movie": {"tmdb_id": 949, "title": "Heat"}}).status_code == 200


def test_kids_cap_on_manual_search_and_grab(client):
    c, db = client
    _movie(db, "m1", "Heat", "R", 949)
    _movie(db, "m2", "Paddington", "PG", 116149)
    over = {"scope": "movie", "title": "Heat", "media_id": 949, "media_source": "tmdb"}
    for path in ("/api/video/downloads/search", "/api/video/downloads/search/start"):
        assert c.post(path, json=over, headers=_as(KID)).status_code == 403
        # a search that names no title can't be vouched for
        assert c.post(path, json={"scope": "movie", "title": "Heat"},
                      headers=_as(KID)).status_code == 403
    r = c.post("/api/video/downloads/grab", headers=_as(KID), json={
        "kind": "movie", "title": "Heat", "media_id": 949, "media_source": "tmdb",
        "source": "soulseek", "username": "u", "filename": "Heat.mkv"})
    assert r.status_code == 403 and r.get_json()["restricted"] is True
    assert db.list_video_downloads() == []
    # under the cap, the search runs (no source picked: it says so, no 403)
    ok = c.post("/api/video/downloads/search/start", headers=_as(KID),
                json={"scope": "movie", "title": "Paddington", "media_id": 116149,
                      "media_source": "tmdb"})
    assert ok.status_code == 200
    # nobody else is capped
    assert c.post("/api/video/downloads/search/start", json=over).status_code == 200


def test_kids_cap_on_title_reads(client, monkeypatch):
    c, db = client
    r_id = _movie(db, "m1", "Heat", "R", 949)
    pg_id = _movie(db, "m2", "Paddington", "PG", 116149)
    for path in ("/api/video/detail/movie/%d/acquisition", "/api/video/detail/movie/%d/history"):
        assert c.get(path % r_id, headers=_as(KID)).status_code == 403
        assert c.get(path % pg_id, headers=_as(KID)).status_code == 200
        assert c.get(path % r_id).status_code == 200
    assert c.get("/api/video/title-status/movie/949", headers=_as(KID)).status_code == 403
    assert c.get("/api/video/title-status/movie/116149", headers=_as(KID)).status_code == 200
    assert c.get("/api/video/downloads/meta/movie/949", headers=_as(KID)).status_code == 403
    # posters: an over-cap title's art reads as missing for the kid only
    asked = []
    monkeypatch.setattr(db, "get_art_ref", lambda *a, **k: asked.append(a) or None)
    assert c.get("/api/video/poster/movie/%d" % r_id, headers=_as(KID)).status_code == 404
    assert asked == []
    c.get("/api/video/poster/movie/%d" % r_id)
    assert asked, "the admin's poster read goes through"


# ── S19 + S21: the request limit ────────────────────────────────────────────

def test_withdrawing_a_request_does_not_hand_the_ask_back(client):
    c, db = client
    lim = _as(MEMBER, **{"X-Test-Limit": "1"})
    r = c.post("/api/video/requests", headers=lim, json={"kind": "movie", "tmdb_id": 11, "title": "A"})
    assert r.status_code == 200
    rid = r.get_json()["id"]
    assert c.delete("/api/video/requests/%d" % rid, headers=lim).status_code == 200
    again = c.post("/api/video/requests", headers=lim, json={"kind": "movie", "tmdb_id": 12, "title": "B"})
    assert again.status_code == 429
    assert c.get("/api/video/requests", headers=lim).get_json()["quota"]["used"] == 1


def test_clearing_resolved_requests_does_not_hand_them_back(client):
    c, db = client
    lim = _as(MEMBER, **{"X-Test-Limit": "1"})
    rid = c.post("/api/video/requests", headers=lim,
                 json={"kind": "movie", "tmdb_id": 11, "title": "A"}).get_json()["id"]
    assert c.post("/api/video/requests/%d/deny" % rid, json={}).status_code == 200
    assert c.delete("/api/video/requests/resolved", headers=lim).get_json()["removed"] == 1
    assert c.post("/api/video/requests", headers=lim,
                  json={"kind": "movie", "tmdb_id": 12, "title": "B"}).status_code == 429


def test_a_member_with_no_limit_on_record_is_refused_not_waved_through(client):
    c, db = client
    r = c.post("/api/video/requests", headers=_as(MEMBER, **{"X-Test-No-Stamp": "1"}),
               json={"kind": "movie", "tmdb_id": 11, "title": "A"})
    assert r.status_code == 503
    assert db.list_video_requests() == []
    # the admin has no limit to read
    assert c.post("/api/video/requests", headers={"X-Test-No-Stamp": "1"},
                  json={"kind": "movie", "tmdb_id": 11, "title": "A"}).status_code == 200


def test_an_unreadable_count_is_a_full_quota(client, monkeypatch):
    c, db = client
    monkeypatch.setattr(db, "count_video_requests_since", lambda *a, **k: None)
    r = c.post("/api/video/requests", headers=_as(MEMBER, **{"X-Test-Limit": "3"}),
               json={"kind": "movie", "tmdb_id": 11, "title": "A"})
    assert r.status_code == 429


def test_the_upgrade_counts_requests_already_filed(tmp_path):
    """v73: a db from before the log keeps counting what it already holds."""
    path = str(tmp_path / "v.db")
    db = VideoDatabase(database_path=path)
    db.add_video_request(profile_id=5, requester_name="K", kind="movie", tmdb_id=1, title="A")
    conn = db._get_connection()
    conn.execute("DELETE FROM video_request_log")
    conn.execute("PRAGMA user_version = 72")
    conn.commit()
    conn.close()
    from database import video_database as vd
    vd._initialized_paths.discard(str(__import__("pathlib").Path(path).resolve()))
    db2 = VideoDatabase(database_path=path)
    assert db2.count_video_requests_since(5, 7) == 1
