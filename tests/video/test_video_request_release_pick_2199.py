"""#2199 on the video side: an approved movie, show or episode request can wait
for an admin to pick the release.

held wishlist rows (video_wishlist.awaiting_selection = 1) are invisible to
every automatic path: the drain, its retry backoff, rss, "search all" and
the download monitor's retries all read movie/episode_wishlist_to_download
or wishlist_manual_search_items, or ask wishlist_held. the pick itself is
the normal manual search + grab (video-download-view.js), so nothing new
downloads here; the row stays held until the picked file lands.
"""

from __future__ import annotations

import pytest
from flask import Flask, g

from core import permissions as P
from core.requests import acquisition
from database.video_database import SCHEMA_VERSION, VideoDatabase
from tests.permissions_fixtures import member_perms


class _Config:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key, default=None):
        node = self.data
        for k in key.split('.'):
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set(self, key, value):
        node = self.data
        keys = key.split('.')
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import api.video as videoapi
    import api.video.requests as vreq
    import core.profile_notify as pn
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    monkeypatch.setattr(vreq, "_tmdb_lookup", lambda kind, tid: None)
    notes = []
    monkeypatch.setattr(pn, "_journal", lambda pid, kind, msg: notes.append((pid, kind, msg)))
    monkeypatch.setattr(pn, "_emitter", None)
    cfg = _Config()
    monkeypatch.setattr(acquisition, "_config", lambda config=None: config if config is not None else cfg)
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
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

    def as_member(pid=5, name="Maya", **changes):
        persona.clear()
        persona.update({"profile_id": pid, "is_admin": False, "profile_name": name,
                        "can_download": False, "allowed_sides": "both",
                        "perms": member_perms("adult", **changes), "perms_source": "template"})

    as_admin()
    try:
        yield app.test_client(), db, cfg, as_admin, as_member, notes
    finally:
        videoapi._video_db = None


def _ask(db, kind="movie", tmdb=603, title="The Matrix", pid=5, name="Maya", **kw):
    rid, _created = db.add_video_request(profile_id=pid, requester_name=name, kind=kind, tmdb_id=tmdb,
                                         title=title, year=1999 if kind == "movie" else None, **kw)
    return rid


def _wanted_movies(db):
    return [r["tmdb_id"] for r in db.movie_wishlist_to_download(due_only=False)]


def _wanted_episodes(db):
    return [(r["show_tmdb_id"], r["season_number"], r["episode_number"])
            for r in db.episode_wishlist_to_download(due_only=False)]


def test_the_migration_adds_both_columns_and_holds_nothing(env):
    _c, db, *_ = env
    assert SCHEMA_VERSION >= 76
    conn = db._get_connection()
    try:
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(video_wishlist)")}
        assert "awaiting_selection" in cols and str(cols["awaiting_selection"][4]) == "0"
        assert "acquisition" in {r[1] for r in conn.execute("PRAGMA table_info(video_requests)")}
    finally:
        conn.close()


def test_automatic_by_default_the_drain_takes_it(env):
    c, db, cfg, *_ = env
    rid = _ask(db)
    out = c.post(f"/api/video/requests/{rid}/approve").get_json()
    assert out["success"] and out["acquisition"] == "auto"
    assert _wanted_movies(db) == [603]
    assert db.get_video_request(rid)["acquisition"] is None


def test_a_held_movie_is_out_of_every_automatic_path(env):
    c, db, cfg, _admin, as_member, notes = env
    cfg.set("requests.acquisition.movie", "manual")
    rid = _ask(db)
    out = c.post(f"/api/video/requests/{rid}/approve").get_json()
    assert out["success"] and out["acquisition"] == "manual"
    req = db.get_video_request(rid)
    assert req["status"] == "approved" and req["acquisition"] == "manual"
    # on the requester's wishlist, held
    assert db.wishlist_counts(profile_id=5).get("movie") == 1
    assert _wanted_movies(db) == []                                  # drain + rss + search all
    assert db.wishlist_manual_search_items("movie", 603, profile_id=5) == []   # retries
    assert db.wishlist_held("movie", 603) is True
    assert any(p == 5 and "picked" in m for p, _k, m in notes)
    # a re-add (a watchlist scan, a collection) never clears the hold
    db.add_movie_to_wishlist(603, "The Matrix", profile_id=5)
    assert _wanted_movies(db) == []
    # the requester sees it waiting for a pick
    as_member()
    rows = c.get("/api/video/requests").get_json()["requests"]
    assert rows[0]["awaiting_selection"] is True and rows[0]["picking"] is False


def test_the_approver_overrides_the_setting_both_ways(env):
    c, db, cfg, *_ = env
    cfg.set("requests.acquisition.movie", "manual")
    rid = _ask(db)
    assert c.post(f"/api/video/requests/{rid}/approve",
                  json={"acquisition": "auto"}).get_json()["acquisition"] == "auto"
    assert _wanted_movies(db) == [603]
    cfg.set("requests.acquisition.tv", "auto")
    rid2 = _ask(db, kind="episode", tmdb=95396, title="Severance S01E02", season_number=1, episode_number=2)
    assert c.post(f"/api/video/requests/{rid2}/approve",
                  json={"acquisition": "manual"}).get_json()["acquisition"] == "manual"
    assert _wanted_episodes(db) == []


def test_a_held_show_waits_off_the_watchlist(env, monkeypatch):
    c, db, cfg, *_ = env

    class _Eng:
        def tmdb_detail(self, kind, tid):
            return {"seasons": [{"season_number": 1}]}

        def tmdb_season(self, tid, sn):
            return {"episodes": [{"episode_number": 1, "title": "Pilot", "air_date": "2025-01-01"},
                                 {"episode_number": 2, "title": "Two", "air_date": "2025-01-08"}]}

    import core.video.enrichment.engine as eng_mod
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Eng())
    cfg.set("requests.acquisition.tv", "manual")
    rid = _ask(db, kind="show", tmdb=95396, title="Severance", monitor="all")
    out = c.post(f"/api/video/requests/{rid}/approve").get_json()
    assert out["success"] and out["wished"] == 2
    assert _wanted_episodes(db) == []
    # its later episodes would download by themselves from the watchlist
    assert db.watchlist_states("show", profile_id=5) == {}
    queue = c.get("/api/video/requests/awaiting").get_json()["titles"]
    assert len(queue) == 1 and queue[0]["seasons"] == [{"season_number": 1, "episodes": [1, 2]}]
    assert queue[0]["requesters"] == ["Maya"] and queue[0]["downloading"] is False
    # handed back to the automatic path: the drain sees it, it joins the watchlist
    assert c.post(f"/api/video/requests/{rid}/auto").get_json()["released"] == 2
    assert len(_wanted_episodes(db)) == 2
    assert 95396 in db.watchlist_states("show", profile_id=5)
    assert c.get("/api/video/requests/awaiting").get_json()["titles"] == []


def test_only_admins_and_other_peoples_moderators_pick(env):
    c, db, cfg, as_admin, as_member, _notes = env
    cfg.set("requests.acquisition.movie", "manual")
    rid = _ask(db, pid=7, name="Mo")
    c.post(f"/api/video/requests/{rid}/approve")
    as_member(pid=8, name="Kim")
    assert c.get("/api/video/requests/awaiting").status_code == 403
    assert c.post(f"/api/video/requests/{rid}/auto").status_code == 403
    # a moderator: anyone's but their own
    as_member(pid=7, name="Mo", **{"requests.moderate": True})
    assert c.get("/api/video/requests/awaiting").get_json()["titles"] == []
    assert c.post(f"/api/video/requests/{rid}/auto").status_code == 403
    as_member(pid=9, name="Lee", **{"requests.moderate": True})
    titles = c.get("/api/video/requests/awaiting").get_json()["titles"]
    assert [t["tmdb_id"] for t in titles] == [603] and titles[0]["requesters"] == ["Mo"]
    as_admin()
    assert c.get("/api/video/requests/awaiting").get_json()["titles"][0]["id"] == rid


def test_the_auto_route_needs_moderation_rights_at_the_gate():
    from api.video.access import route_access
    assert route_access("/api/video/requests/<int:request_id>/auto", "POST") == \
        ("perm", ("requests.moderate",))


def test_a_failed_pick_is_never_retried_with_another_release(env, monkeypatch):
    _c, db, cfg, *_ = env
    import core.video.download_monitor as mon
    import core.video.retry as retry
    tried = []
    monkeypatch.setattr(retry, "plan_retry", lambda *a, **k: {"action": "candidate",
                                                                "candidate": {"filename": "b"}, "rest": []})
    monkeypatch.setattr(mon, "_apply_candidate", lambda *a, **k: tried.append(a) or True)
    monkeypatch.setattr(mon, "_publish_failed", lambda *a, **k: None)
    db.add_movie_to_wishlist(603, "The Matrix", profile_id=5, awaiting_selection=True)
    dl = {"id": 1, "kind": "movie", "media_id": "603", "media_source": "tmdb", "title": "The Matrix",
          "source": "soulseek", "username": "u", "filename": "a"}
    monkeypatch.setattr(db, "update_video_download", lambda *a, **k: None)
    monkeypatch.setattr(db, "record_download_history", lambda *a, **k: None)
    mon._fail_or_retry(db, dl, "peer went away")
    assert tried == []
    assert db.wishlist_held("movie", 603) is True        # still held, picked again from Requests
    # an unheld title retries as before
    db.release_held_wishlist("movie", 603)
    mon._fail_or_retry(db, dl, "peer went away")
    assert len(tried) == 1
