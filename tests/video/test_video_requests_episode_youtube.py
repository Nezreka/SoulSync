"""Video requests: episode + youtube kinds, and the podcast watchlist
auto-download permission fix.

Covers the four fixes in one place:
- episode requests (file/validate/approve, scoped idempotency + claims)
- youtube requests (file/validate/approve, id not spoofable)
- shows stay requestable when owned (episodes may be missing)
- podcast watchlist/add can no longer arm auto-download for a profile
  that may not download, and the automation won't queue for them either
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g

from core.requests.video import (episode_request_title, parse_season_episode,
                                 parse_youtube_id, youtube_poster_url)
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    persona = {"profile_id": 1, "is_admin": True, "can_download": True, "profile_name": "Admin"}

    @app.before_request
    def _persona():
        for k, v in persona.items():
            setattr(g, k, v)

    try:
        yield app.test_client(), db, persona
    finally:
        videoapi._video_db = None


def _as_member(persona, pid=5, name="Kid"):
    persona.update({"profile_id": pid, "is_admin": False, "can_download": False,
                    "profile_name": name})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_parse_youtube_id():
    assert parse_youtube_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_youtube_id("abc-def_123") == "abc-def_123"
    assert parse_youtube_id("too short") is None
    assert parse_youtube_id("waytoolongforthis") is None
    assert parse_youtube_id("drop table;--") is None
    assert parse_youtube_id(None) is None
    assert parse_youtube_id("") is None


def test_parse_season_episode():
    assert parse_season_episode(2) == 2
    assert parse_season_episode("7") == 7
    assert parse_season_episode(0) == 0  # season 0 = specials, requestable
    assert parse_season_episode(-1) is None
    assert parse_season_episode("x") is None
    assert parse_season_episode(None) is None


def test_episode_request_title():
    assert episode_request_title("Breaking Bad", 2, 7) == "Breaking Bad S02E07"
    assert episode_request_title("Show", 12, 3) == "Show S12E03"


def test_youtube_poster_is_server_built():
    url = youtube_poster_url("dQw4w9WgXcQ")
    assert url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


# ---------------------------------------------------------------------------
# episode requests
# ---------------------------------------------------------------------------

def test_member_files_episode_request(app_db):
    client, db, persona = app_db
    _as_member(persona)
    out = client.post("/api/video/requests", json={
        "kind": "episode", "tmdb_id": 1396, "season": 2, "episode": 7,
        "title": "Breaking Bad"}).get_json()
    assert out["success"] and not out["already"]
    rows = db.list_video_requests(profile_id=5, status="pending")
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "episode" and r["tmdb_id"] == 1396
    assert r["season_number"] == 2 and r["episode_number"] == 7
    assert r["title"] == "Breaking Bad S02E07"


def test_episode_request_needs_season_and_episode(app_db):
    client, db, persona = app_db
    _as_member(persona)
    for body in ({"kind": "episode", "tmdb_id": 1396, "season": 2},
                 {"kind": "episode", "tmdb_id": 1396, "episode": 7},
                 {"kind": "episode", "tmdb_id": 1396, "season": "x", "episode": 7},
                 {"kind": "episode", "tmdb_id": 1396, "season": -1, "episode": 7}):
        resp = client.post("/api/video/requests", json=body)
        assert resp.status_code == 400, body


def test_member_files_specials_season_zero_request(app_db):
    # season 0 (specials) is a real, requestable season — it used to 400
    client, db, persona = app_db
    _as_member(persona)
    base = {"kind": "episode", "tmdb_id": 1396, "title": "Black Clover"}
    out = client.post("/api/video/requests",
                      json={**base, "season": 0, "episode": 2}).get_json()
    assert out["success"] and not out["already"]
    rows = db.list_video_requests(profile_id=5, status="pending")
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "episode" and r["tmdb_id"] == 1396
    assert r["season_number"] == 0 and r["episode_number"] == 2
    assert r["title"] == "Black Clover S00E02"
    # idempotent per (season, episode) — a re-ask finds the same row
    again = client.post("/api/video/requests",
                        json={**base, "season": 0, "episode": 2}).get_json()
    assert again["already"] is True and again["id"] == out["id"]
    # a different season is a different title
    other = client.post("/api/video/requests",
                        json={**base, "season": 1, "episode": 2}).get_json()
    assert other["already"] is False and other["id"] != out["id"]


def test_episode_idempotency_scopes_to_the_episode(app_db):
    client, db, persona = app_db
    _as_member(persona)
    base = {"kind": "episode", "tmdb_id": 1396, "title": "Breaking Bad"}
    first = client.post("/api/video/requests", json={**base, "season": 2, "episode": 7}).get_json()
    again = client.post("/api/video/requests", json={**base, "season": 2, "episode": 7}).get_json()
    assert again["already"] is True and again["id"] == first["id"]
    # a different episode is a different title
    other = client.post("/api/video/requests", json={**base, "season": 2, "episode": 8}).get_json()
    assert other["already"] is False and other["id"] != first["id"]


def test_episode_request_409_when_owned(app_db):
    client, db, persona = app_db
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (tmdb_id, title) VALUES (1396, 'Breaking Bad')")
        show_id = conn.execute("SELECT id FROM shows WHERE tmdb_id=1396").fetchone()[0]
        conn.execute("INSERT INTO seasons (show_id, season_number) VALUES (?, 2)", (show_id,))
        season_id = conn.execute(
            "SELECT id FROM seasons WHERE show_id=? AND season_number=2", (show_id,)).fetchone()[0]
        conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, has_file) "
                     "VALUES (?, ?, 2, 7, 1)", (show_id, season_id))
        conn.commit()
    finally:
        conn.close()
    _as_member(persona)
    resp = client.post("/api/video/requests", json={
        "kind": "episode", "tmdb_id": 1396, "season": 2, "episode": 7, "title": "Breaking Bad"})
    assert resp.status_code == 409
    assert resp.get_json()["in_library"] is True
    # a missing episode of the same show is still requestable
    ok = client.post("/api/video/requests", json={
        "kind": "episode", "tmdb_id": 1396, "season": 2, "episode": 8, "title": "Breaking Bad"})
    assert ok.get_json()["success"] is True


def test_approve_episode_wishes_that_episode_only(app_db):
    client, db, persona = app_db
    _as_member(persona)
    base = {"kind": "episode", "tmdb_id": 1396, "title": "Breaking Bad"}
    rid7 = client.post("/api/video/requests", json={**base, "season": 2, "episode": 7}).get_json()["id"]
    rid8 = client.post("/api/video/requests", json={**base, "season": 2, "episode": 8}).get_json()["id"]
    # member cannot approve
    assert client.post("/api/video/requests/%d/approve" % rid7).status_code == 403
    persona.update({"profile_id": 1, "is_admin": True})
    out = client.post("/api/video/requests/%d/approve" % rid7).get_json()
    assert out["success"] and out["kind"] == "episode" and out["approved"] == 1
    wished = db._get_connection().execute(
        "SELECT season_number, episode_number FROM video_wishlist WHERE kind='episode' AND tmdb_id=1396"
    ).fetchall()
    assert [(w[0], w[1]) for w in wished] == [(2, 7)]
    # the other episode's request is still pending (scoped claim)
    rows = {r["id"]: r["status"] for r in db.list_video_requests()}
    assert rows[rid7] == "approved" and rows[rid8] == "pending"


def test_show_request_still_allowed_when_show_owned(app_db):
    # a show in the library may be missing episodes — that is a legitimate ask
    client, db, persona = app_db
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (tmdb_id, title) VALUES (1396, 'Breaking Bad')")
        conn.commit()
    finally:
        conn.close()
    _as_member(persona)
    out = client.post("/api/video/requests", json={
        "kind": "show", "tmdb_id": 1396, "title": "Breaking Bad"}).get_json()
    assert out["success"] is True


# ---------------------------------------------------------------------------
# youtube requests
# ---------------------------------------------------------------------------

_YT = "dQw4w9WgXcQ"


def test_member_files_youtube_request(app_db):
    client, db, persona = app_db
    _as_member(persona)
    out = client.post("/api/video/requests", json={
        "kind": "youtube", "youtube_id": _YT, "title": "Never Gonna Give You Up",
        "channel": {"youtube_id": "UCuAXFkgsw1Lo7Zz9Z5Z5Z5Z5"[:24], "title": "Rick Astley"}}).get_json()
    assert out["success"] and not out["already"]
    rows = db.list_video_requests(profile_id=5, status="pending")
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "youtube" and r["youtube_id"] == _YT
    assert r["tmdb_id"] == 0
    assert r["poster_url"] == "https://i.ytimg.com/vi/%s/hqdefault.jpg" % _YT


def test_youtube_request_rejects_bad_ids(app_db):
    client, db, persona = app_db
    _as_member(persona)
    for body in ({"kind": "youtube", "title": "x"},
                 {"kind": "youtube", "youtube_id": "short", "title": "x"},
                 {"kind": "youtube", "youtube_id": _YT}):
        assert client.post("/api/video/requests", json=body).status_code == 400, body


def test_youtube_request_409_when_owned(app_db):
    client, db, persona = app_db
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO video_download_history (kind, source, media_id, outcome) "
                     "VALUES ('video', 'youtube', ?, 'completed')", (_YT,))
        conn.commit()
    finally:
        conn.close()
    _as_member(persona)
    resp = client.post("/api/video/requests", json={"kind": "youtube", "youtube_id": _YT, "title": "x"})
    assert resp.status_code == 409
    assert resp.get_json()["in_library"] is True


def test_youtube_idempotency_per_video(app_db):
    client, db, persona = app_db
    _as_member(persona)
    first = client.post("/api/video/requests",
                        json={"kind": "youtube", "youtube_id": _YT, "title": "x"}).get_json()
    again = client.post("/api/video/requests",
                        json={"kind": "youtube", "youtube_id": _YT, "title": "x"}).get_json()
    assert again["already"] is True and again["id"] == first["id"]
    other = client.post("/api/video/requests",
                        json={"kind": "youtube", "youtube_id": "abcdefghijk", "title": "y"}).get_json()
    assert other["already"] is False


def test_approve_youtube_wishes_the_video(app_db):
    client, db, persona = app_db
    _as_member(persona)
    rid = client.post("/api/video/requests", json={
        "kind": "youtube", "youtube_id": _YT, "title": "Never Gonna Give You Up",
        "channel": {"youtube_id": "UC-9-kyTW8ZkZzZzZzZzZz", "title": "Rick Astley"}}).get_json()["id"]
    assert client.post("/api/video/requests/%d/approve" % rid).status_code == 403
    persona.update({"profile_id": 1, "is_admin": True})
    out = client.post("/api/video/requests/%d/approve" % rid).get_json()
    assert out["success"] and out["kind"] == "youtube"
    rows = db._get_connection().execute(
        "SELECT source_id, episode_title FROM video_wishlist WHERE kind='video' AND source='youtube'").fetchall()
    assert [(r[0], r[1]) for r in rows] == [(_YT, "Never Gonna Give You Up")]


def test_approve_youtube_without_channel_still_resolves(app_db):
    # the video stands in as its own channel row rather than failing approval
    client, db, persona = app_db
    _as_member(persona)
    rid = client.post("/api/video/requests",
                      json={"kind": "youtube", "youtube_id": _YT, "title": "x"}).get_json()["id"]
    persona.update({"profile_id": 1, "is_admin": True})
    assert client.post("/api/video/requests/%d/approve" % rid).get_json()["success"] is True


def test_deny_scopes_to_the_video(app_db):
    client, db, persona = app_db
    _as_member(persona)
    r1 = client.post("/api/video/requests",
                     json={"kind": "youtube", "youtube_id": _YT, "title": "x"}).get_json()["id"]
    r2 = client.post("/api/video/requests",
                     json={"kind": "youtube", "youtube_id": "abcdefghijk", "title": "y"}).get_json()["id"]
    persona.update({"profile_id": 1, "is_admin": True})
    assert client.post("/api/video/requests/%d/deny" % r1, json={"response": "no"}).get_json()["success"]
    rows = {r["id"]: r["status"] for r in db.list_video_requests()}
    assert rows[r1] == "denied" and rows[r2] == "pending"


# ---------------------------------------------------------------------------
# review-fix regression tests
# ---------------------------------------------------------------------------

def test_episode_approve_handles_three_digit_episode(app_db):
    # S01E120: the show title must not leak the episode suffix into the
    # wishlist row's show title
    client, db, persona = app_db
    _as_member(persona)
    rid = client.post("/api/video/requests", json={
        "kind": "episode", "tmdb_id": 1396, "season": 1, "episode": 120,
        "title": "Daily Show"}).get_json()["id"]
    persona.update({"profile_id": 1, "is_admin": True})
    assert client.post("/api/video/requests/%d/approve" % rid).get_json()["success"]
    row = db._get_connection().execute(
        "SELECT title FROM video_wishlist WHERE kind='episode' AND tmdb_id=1396").fetchone()
    assert row[0] == "Daily Show"


def test_episode_quality_profile_scopes_to_the_episode(app_db, monkeypatch):
    client, db, persona = app_db
    import api.video.requests as req_mod
    monkeypatch.setattr(req_mod, "_quality_profile", lambda v: int(v) if v else None)
    _as_member(persona)
    base = {"kind": "episode", "tmdb_id": 1396, "title": "Breaking Bad"}
    # quality profiles are admin-only: a member's pick at filing is ignored
    r1 = client.post("/api/video/requests",
                     json={**base, "season": 2, "episode": 7, "quality_profile_id": 7}).get_json()["id"]
    assert db.get_video_request(r1)["quality_profile_id"] is None
    r2 = client.post("/api/video/requests",
                     json={**base, "season": 2, "episode": 8}).get_json()["id"]
    persona.update({"profile_id": 1, "is_admin": True})
    # the admin's pick on approve stamps only that episode's row
    client.post("/api/video/requests/%d/approve" % r1, json={"quality_profile_id": 7})
    client.post("/api/video/requests/%d/approve" % r2)
    rows = {r[0]: r[1] for r in db._get_connection().execute(
        "SELECT episode_number, quality_profile_id FROM video_wishlist "
        "WHERE kind='episode' AND tmdb_id=1396").fetchall()}
    assert rows == {7: 7, 8: None}


def test_youtube_approve_when_downloaded_in_the_window(app_db):
    # video lands on disk between filing and approval: approve still resolves
    # (arrival sweep marks it available) instead of failing
    client, db, persona = app_db
    _as_member(persona)
    rid = client.post("/api/video/requests",
                      json={"kind": "youtube", "youtube_id": _YT, "title": "x"}).get_json()["id"]
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO video_download_history (kind, source, media_id, outcome) "
                     "VALUES ('video', 'youtube', ?, 'completed')", (_YT,))
        conn.commit()
    finally:
        conn.close()
    persona.update({"profile_id": 1, "is_admin": True})
    out = client.post("/api/video/requests/%d/approve" % rid).get_json()
    assert out["success"] is True
    assert db.get_video_request(rid)["status"] == "approved"


def test_podcast_settings_cannot_arm_auto_download_without_permission(tmp_path):
    import api.podcasts as podcasts_mod
    client = _podcast_client(tmp_path)
    with patch("core.profile_context.get_current_profile_id", return_value=5), \
         patch("api.helpers.download_permission_error",
               return_value=({"success": False}, 403)), \
         patch.object(podcasts_mod, "_db") as mock_db:
        db = MagicMock()
        mock_db.return_value = db
        resp = client.post("/api/podcasts/watchlist/settings", json={
            "feed_url": "https://x/feed", "auto_download": True})
        assert resp.status_code == 403
        db.update_watchlist_podcast_settings.assert_not_called()

def _podcast_client(tmp_path):
    from flask import Flask
    from api.podcasts import create_podcasts_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_podcasts_blueprint())
    return app.test_client()


def test_podcast_follow_forces_auto_download_off_without_permission(tmp_path):
    import api.podcasts as podcasts_mod
    client = _podcast_client(tmp_path)
    with patch("core.profile_context.get_current_profile_id", return_value=5), \
         patch("api.helpers.download_permission_error",
               return_value=({"success": False}, 403)), \
         patch.object(podcasts_mod, "_db") as mock_db:
        db = MagicMock()
        db.add_watchlist_podcast.return_value = True
        db.get_watchlist_podcast.return_value = {"feed_url": "https://x/feed", "auto_download": False}
        mock_db.return_value = db
        out = client.post("/api/podcasts/watchlist/add", json={
            "feed_url": "https://x/feed", "title": "Show", "auto_download": True}).get_json()
    assert out["success"] is True
    assert out["downloads_disabled"] is True
    # the follow landed, but auto-download was forced off
    assert db.add_watchlist_podcast.call_args.kwargs["auto_download"] is False


def test_podcast_follow_keeps_auto_download_with_permission(tmp_path):
    import api.podcasts as podcasts_mod
    client = _podcast_client(tmp_path)
    with patch("core.profile_context.get_current_profile_id", return_value=1), \
         patch("api.helpers.download_permission_error", return_value=None), \
         patch.object(podcasts_mod, "_db") as mock_db:
        db = MagicMock()
        db.add_watchlist_podcast.return_value = True
        db.get_watchlist_podcast.return_value = {"feed_url": "https://x/feed", "auto_download": True}
        mock_db.return_value = db
        out = client.post("/api/podcasts/watchlist/add", json={
            "feed_url": "https://x/feed", "title": "Show", "auto_download": True}).get_json()
    assert out["success"] is True
    assert out["downloads_disabled"] is False
    assert db.add_watchlist_podcast.call_args.kwargs["auto_download"] is True


def test_automation_wont_queue_for_profile_without_download_rights():
    from core import podcast_automation as pa
    db = MagicMock()
    db.get_profile.return_value = {"id": 5, "can_download": False}
    assert pa._profile_may_download(db, 5) is False
    db.get_profile.return_value = {"id": 5, "can_download": True}
    assert pa._profile_may_download(db, 5) is True
    assert pa._profile_may_download(db, 1) is True  # profile 1 always may


def test_automation_skips_queue_when_downloads_off(tmp_path, monkeypatch):
    from core import podcast_automation as pa
    db = MagicMock()
    db.get_watchlist_podcasts.return_value = [{
        "feed_url": "https://x/feed", "title": "Show", "auto_download": True,
        "date_added": "2026-01-01 00:00:00"}]
    db.get_profile.return_value = {"id": 5, "can_download": False}
    monkeypatch.setattr(pa, "_get_db", lambda: db)
    show = MagicMock()
    ep = MagicMock()
    ep.pub_date = None
    show.episodes = [ep]
    monkeypatch.setattr("core.podcast_automation.get_podcast_client",
                        lambda: MagicMock(fetch_feed=lambda url: show))
    queued = []
    monkeypatch.setattr("api.podcasts.queue_podcast_download",
                        lambda data: queued.append(data) or {"success": True})
    pa.scan_and_auto_download_podcasts(profile_id=5)
    assert queued == []
