"""Seam tests for the /api/video/collections endpoints (Collection Studio)."""

from __future__ import annotations

from flask import Flask


def _make_client(tmp_path):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi


def _seed(db):
    conn = db._get_connection()
    try:
        for mid, g in [(1, "Action"), (2, "Action"), (3, "Comedy")]:
            conn.execute("INSERT INTO movies (id, server_source, server_id, title, has_file) "
                         "VALUES (?,?,?,?,1)", (mid, "plex", f"srv{mid}", f"M{mid}"))
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (g,))
            gid = conn.execute("SELECT id FROM genres WHERE name=?", (g,)).fetchone()[0]
            conn.execute("INSERT INTO movie_genres (movie_id, genre_id) VALUES (?,?)", (mid, gid))
        conn.commit()
    finally:
        conn.close()


def test_collections_routes_registered():
    from api.video import create_video_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_video_blueprint(), url_prefix="/api/video")
    rules = {r.rule for r in app.url_map.iter_rules()}
    for want in ("/api/video/collections",
                 "/api/video/collections/<int:cid>",
                 "/api/video/collections/<int:cid>/duplicate",
                 "/api/video/collections/<int:cid>/sync",
                 "/api/video/collections/preview",
                 "/api/video/collections/fields",
                 "/api/video/collections/sync"):
        assert want in rules, want


def test_crud_preview_and_fields(tmp_path):
    client, videoapi = _make_client(tmp_path)
    _seed(videoapi._video_db)
    action = {"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]}

    r = client.post("/api/video/collections", json={
        "name": "Action", "media_type": "movie", "kind": "smart", "definition": action})
    assert r.status_code == 200 and r.get_json()["ok"]
    cid = r.get_json()["id"]

    got = client.get(f"/api/video/collections/{cid}").get_json()["collection"]
    assert got["name"] == "Action" and got["definition"]["rules"][0]["field"] == "genre"

    listed = client.get("/api/video/collections").get_json()["collections"]
    assert any(c["id"] == cid for c in listed)

    fields = client.get("/api/video/collections/fields?media_type=movie").get_json()
    names = {f["field"] for f in fields["fields"]}
    assert {"genre", "director", "franchise", "studio"} <= names
    assert "genre" in fields["suggestions"]

    prev = client.post("/api/video/collections/preview", json={
        "media_type": "movie", "kind": "smart", "definition": action}).get_json()
    assert prev["ok"] and prev["count"] == 2 and len(prev["sample"]) == 2

    bad = client.post("/api/video/collections/preview", json={
        "media_type": "movie", "kind": "smart", "definition": {"rules": []}}).get_json()
    assert bad["ok"] is False and "no rules" in bad["error"]

    assert client.put(f"/api/video/collections/{cid}", json={"name": "Renamed", "pinned": True}).get_json()["ok"]
    assert client.get(f"/api/video/collections/{cid}").get_json()["collection"]["name"] == "Renamed"

    dup = client.post(f"/api/video/collections/{cid}/duplicate").get_json()
    assert dup["ok"] and dup["id"] != cid

    # Sync with no server configured -> a clean non-200, never a crash.
    s = client.post(f"/api/video/collections/{cid}/sync")
    assert s.status_code == 400 and s.get_json()["ok"] is False

    assert client.delete(f"/api/video/collections/{cid}").get_json()["ok"]
    assert client.get(f"/api/video/collections/{cid}").status_code == 404
