"""API seam for the Manage sidebar: PUT metadata, field lock/release, watched
toggle, and the detail-payload additions (sort_title / locked_fields / watched)."""

from __future__ import annotations

from flask import Flask


def _client(tmp_path):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi._video_db


def _seed_movie(db):
    return db.upsert_movie("plex", {"server_id": "m1", "title": "Server Title",
                                    "year": 1999, "genres": ["Action"],
                                    "file": {"path": "/x.mkv"}})


def test_manage_routes_exist(tmp_path):
    client, _ = _client(tmp_path)
    rules = {r.rule for r in client.application.url_map.iter_rules()}
    assert "/api/video/detail/<kind>/<int:item_id>/metadata" in rules
    assert "/api/video/detail/<kind>/<int:item_id>/lock" in rules
    assert "/api/video/detail/<kind>/<int:item_id>/watched" in rules


def test_put_metadata_roundtrip_and_detail_payload(tmp_path):
    client, db = _client(tmp_path)
    mid = _seed_movie(db)
    r = client.put(f"/api/video/detail/movie/{mid}/metadata",
                   json={"changes": {"title": "The User Cut", "genres": ["Comfort"]}})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and "title" in body["locked"] and "genres" in body["locked"]
    assert body["pushed"] is False                      # no live server in tests

    d = client.get(f"/api/video/detail/movie/{mid}").get_json()
    assert d["title"] == "The User Cut" and d["sort_title"] == "user cut"
    assert d["genres"] == ["Comfort"]
    assert set(d["locked_fields"]) == {"genres", "sort_title", "title"}
    assert d["watched"] is False


def test_put_metadata_validation(tmp_path):
    client, db = _client(tmp_path)
    mid = _seed_movie(db)
    assert client.put(f"/api/video/detail/movie/{mid}/metadata", json={}).status_code == 400
    assert client.put(f"/api/video/detail/movie/{mid}/metadata",
                      json={"changes": {"poster_url": "x"}}).status_code == 400
    assert client.put(f"/api/video/detail/album/{mid}/metadata",
                      json={"changes": {"title": "x"}}).status_code == 400
    assert client.put("/api/video/detail/movie/999999/metadata",
                      json={"changes": {"title": "x"}}).status_code == 404


def test_lock_and_release(tmp_path):
    client, db = _client(tmp_path)
    mid = _seed_movie(db)
    r = client.post(f"/api/video/detail/movie/{mid}/lock",
                    json={"field": "title", "locked": True})
    assert r.status_code == 200 and r.get_json()["locked"] == ["title"]
    r = client.post(f"/api/video/detail/movie/{mid}/lock",
                    json={"field": "title", "locked": False})
    assert r.status_code == 200 and r.get_json()["locked"] == []
    assert client.post(f"/api/video/detail/movie/{mid}/lock",
                       json={"field": "nope", "locked": True}).status_code == 404
    assert client.post(f"/api/video/detail/movie/{mid}/lock", json={}).status_code == 400


def test_watched_toggle(tmp_path):
    client, db = _client(tmp_path)
    mid = _seed_movie(db)
    r = client.post(f"/api/video/detail/movie/{mid}/watched", json={"watched": True})
    assert r.status_code == 200 and r.get_json()["watched"] is True
    assert client.get(f"/api/video/detail/movie/{mid}").get_json()["watched"] is True
    client.post(f"/api/video/detail/movie/{mid}/watched", json={"watched": False})
    assert client.get(f"/api/video/detail/movie/{mid}").get_json()["watched"] is False
    assert client.post("/api/video/detail/movie/999999/watched",
                       json={"watched": True}).status_code == 404
