"""Manual overrides: include/exclude tmdb-id lists in the definition body apply
LAST, on top of whatever the builder resolved — every kind supports "perfect,
except that one movie". Excluded titles are never wishlisted."""

from __future__ import annotations

import pytest
from flask import Flask

from core.video.collections.resolver import resolve_collection
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed(db):
    conn = db._get_connection()
    try:
        for mid, tmdb, genre in ((1, 601, "Action"), (2, 602, "Action"),
                                 (3, 603, "Drama"), (4, 604, "Drama")):
            conn.execute("INSERT INTO movies (id, server_source, server_id, tmdb_id, title, has_file) "
                         "VALUES (?,?,?,?,?,1)", (mid, "plex", f"m{mid}", tmdb, f"M{mid}"))
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (genre,))
            gid = conn.execute("SELECT id FROM genres WHERE name=?", (genre,)).fetchone()[0]
            conn.execute("INSERT INTO movie_genres (movie_id, genre_id) VALUES (?,?)", (mid, gid))
        conn.commit()
    finally:
        conn.close()


def _tmdb_ids(res):
    return sorted(m["tmdb_id"] for m in res.owned)


def test_overrides_on_smart(db):
    _seed(db)
    d = {"media_type": "movie", "kind": "smart", "definition": {
        "rules": [{"field": "genre", "op": "in", "value": ["Action"]}],
        "include": [603],            # pin a Drama into the Action collection
        "exclude": [602]}}           # and kick one Action out
    res = resolve_collection(db, d)
    assert res.ok and _tmdb_ids(res) == [601, 603]


def test_overrides_on_list_and_missing(db):
    _seed(db)
    d = {"media_type": "movie", "kind": "list", "definition": {
        "source": "tmdb_chart", "chart": "top_movies", "limit": 250,
        "include": [604], "exclude": [602, 777]}}
    fetcher = lambda s, ref: [{"tmdb_id": i, "title": f"T{i}"} for i in (601, 602, 777, 888)]  # noqa: E731
    res = resolve_collection(db, d, list_fetcher=fetcher)
    assert res.ok
    assert _tmdb_ids(res) == [601, 604]                      # 602 excluded, 604 pinned
    assert [m["tmdb_id"] for m in res.missing] == [888]      # 777 excluded → never wishlisted


def test_exclude_wins_and_no_dupes(db):
    _seed(db)
    d = {"media_type": "movie", "kind": "smart", "definition": {
        "rules": [{"field": "genre", "op": "in", "value": ["Action"]}],
        "include": [601, 999999],    # 601 already matches (no dupe), 999999 unowned (no-op)
        "exclude": []}}
    res = resolve_collection(db, d)
    assert _tmdb_ids(res) == [601, 602]
    assert len(res.owned) == 2


def test_override_junk_values_ignored(db):
    _seed(db)
    d = {"media_type": "movie", "kind": "smart", "definition": {
        "rules": [{"field": "genre", "op": "in", "value": ["Action"]}],
        "include": ["not-a-number", None], "exclude": ["602"]}}   # strings coerce
    res = resolve_collection(db, d)
    assert _tmdb_ids(res) == [601]


# ── API seams for the editor pickers ─────────────────────────────────────────
def test_search_owned_and_titles_api(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    _seed(videoapi._video_db)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()

    r = client.get("/api/video/collections/search_owned?media_type=movie&q=M1").get_json()
    assert [x["tmdb_id"] for x in r["results"]] == [601]
    r = client.get("/api/video/collections/titles?media_type=movie&tmdb_ids=601,603,junk").get_json()
    assert {t["tmdb_id"] for t in r["titles"]} <= {601, 603}   # junk id list → best effort
    r = client.get("/api/video/collections/titles?media_type=movie&tmdb_ids=601,603").get_json()
    assert {t["tmdb_id"] for t in r["titles"]} == {601, 603}