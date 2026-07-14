"""Missing-members browser: see exactly which titles a list collection's source
has that the library doesn't, and wishlist them on demand (an explicit action —
independent of the collection's nightly wishlist toggle)."""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def client_db(tmp_path, monkeypatch):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db = videoapi._video_db
    conn = db._get_connection()
    conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, has_file) "
                 "VALUES (1, 'plex', 'm1', 601, 'Owned', 1)")
    conn.commit(); conn.close()

    def fake_fetcher(dbb):
        return lambda source, ref: [
            {"tmdb_id": 601, "title": "Owned"},
            {"tmdb_id": 777, "title": "Missing One", "year": 2020,
             "poster_url": "https://img/777.jpg"},
            {"tmdb_id": 888, "title": "Missing Two", "year": 2021,
             "poster_url": "https://img/888.jpg"},
        ]
    monkeypatch.setattr("core.video.collections.list_sources.build_list_fetcher", fake_fetcher)

    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), db


def _chart(db, **kw):
    cid = db.create_collection_definition(
        "Top 250", kind="list", media_type="movie",
        definition=dict({"source": "tmdb_chart", "chart": "top_movies", "limit": 250}, **kw))
    return cid


def test_missing_endpoint_lists_the_gap(client_db):
    client, db = client_db
    cid = _chart(db)
    d = client.get(f"/api/video/collections/{cid}/missing").get_json()
    assert d["ok"] and d["count"] == 2
    assert [m["tmdb_id"] for m in d["missing"]] == [777, 888]
    assert d["missing"][0]["poster_url"] == "https://img/777.jpg"
    assert client.get("/api/video/collections/99999/missing").status_code == 404


def test_missing_respects_exclude_overrides(client_db):
    client, db = client_db
    cid = _chart(db, exclude=[888])
    d = client.get(f"/api/video/collections/{cid}/missing").get_json()
    assert d["count"] == 1 and d["missing"][0]["tmdb_id"] == 777


def test_wishlist_missing_on_demand_ignores_the_toggle(client_db):
    client, db = client_db
    cid = _chart(db)                                    # wishlist_missing = False
    d = client.post(f"/api/video/collections/{cid}/wishlist_missing", json={}).get_json()
    assert d["ok"] and d["added"] == 2 and d["unit"] == "movies"
    assert set(db.wishlisted_movie_status()) == {777, 888}


def test_wishlist_missing_subset(client_db):
    client, db = client_db
    cid = _chart(db)
    d = client.post(f"/api/video/collections/{cid}/wishlist_missing",
                    json={"tmdb_ids": [777]}).get_json()
    assert d["ok"] and d["added"] == 1
    assert set(db.wishlisted_movie_status()) == {777}
