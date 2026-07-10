"""Preset packs (Collection Studio "easy setup"): library-aware expansion with
real owned counts, idempotent apply, and the API seam."""

from __future__ import annotations

import pytest
from flask import Flask

from core.video.collections.presets import apply_pack, expand_pack, list_packs
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _add_movie(db, mid, title, *, year=2000, rating=7.0, studio=None, franchise=None,
               franchise_name=None, genres=(), director=None, resolution=None,
               server_id="auto"):
    sid = f"srv{mid}" if server_id == "auto" else server_id
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO movies (id, server_source, server_id, tmdb_id, title, year, rating, "
            "studio, tmdb_collection_id, tmdb_collection_name, has_file) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (mid, "plex", sid, 1000 + mid, title, year, rating, studio, franchise, franchise_name))
        for g in genres:
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (g,))
            gid = conn.execute("SELECT id FROM genres WHERE name=?", (g,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO movie_genres (movie_id, genre_id) VALUES (?,?)",
                         (mid, gid))
        if director:
            conn.execute("INSERT OR IGNORE INTO people (name, tmdb_id) VALUES (?,?)",
                         (director, hash(director) % 100000))
            pid = conn.execute("SELECT id FROM people WHERE name=?", (director,)).fetchone()[0]
            conn.execute(
                "INSERT INTO credits (person_id, movie_id, department, job) VALUES (?,?, 'crew', 'Director')",
                (pid, mid))
        if resolution:
            conn.execute(
                "INSERT INTO media_files (movie_id, relative_path, resolution) VALUES (?,?,?)",
                (mid, f"{title}.mkv", resolution))
        conn.commit()
    finally:
        conn.close()


def _add_show(db, sid_num, title, *, year=2010, network=None, genres=()):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO shows (id, server_source, server_id, title, year, network) "
            "VALUES (?,?,?,?,?,?)", (sid_num, "plex", f"shsrv{sid_num}", title, year, network))
        for g in genres:
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (g,))
            gid = conn.execute("SELECT id FROM genres WHERE name=?", (g,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO show_genres (show_id, genre_id) VALUES (?,?)",
                         (sid_num, gid))
        conn.commit()
    finally:
        conn.close()


def _seed_movies(db):
    _add_movie(db, 1, "Die Hard", year=1988, genres=("Action",), studio="Fox",
               director="John McTiernan", resolution="2160p", rating=8.2)
    _add_movie(db, 2, "Predator", year=1987, genres=("Action", "Sci-Fi"), studio="Fox",
               director="John McTiernan", rating=7.8)
    _add_movie(db, 3, "Up", year=2009, genres=("Animation",), studio="Pixar", rating=8.3)
    _add_movie(db, 4, "Iron Man", year=2008, genres=("Action",), studio="Marvel",
               franchise=131292, franchise_name="Iron Man Collection", rating=7.9)
    _add_movie(db, 5, "Iron Man 2", year=2010, genres=("Action",), studio="Marvel",
               franchise=131292, franchise_name="Iron Man Collection", rating=7.0)
    # Not on a server → must not count anywhere.
    _add_movie(db, 6, "Orphan", year=1988, genres=("Action",), server_id=None)


# ── aggregates feed expansion with true owned counts ────────────────────────
def test_genre_pack_counts_and_definitions(db):
    _seed_movies(db)
    entries = expand_pack(db, "genres", "movie")
    by_name = {e["name"]: e for e in entries}
    assert by_name["Action"]["count"] == 4          # movie 6 (no server) excluded
    assert by_name["Sci-Fi"]["count"] == 1
    d = by_name["Action"]["definition"]
    assert d["rules"] == [{"field": "genre", "op": "in", "value": ["Action"]}]
    # Counts must equal what the collection will actually resolve to.
    assert len(db.resolve_smart_members("movie", d)) == 4


def test_decade_pack(db):
    _seed_movies(db)
    entries = expand_pack(db, "decades", "movie")
    by_name = {e["name"]: e for e in entries}
    assert by_name["1980s"]["count"] == 2           # Die Hard '88, Predator '87
    assert by_name["2000s"]["count"] == 2           # Up '09, Iron Man '08
    assert by_name["2010s"]["count"] == 1           # Iron Man 2 '10
    assert len(db.resolve_smart_members("movie", by_name["1980s"]["definition"])) == 2


def test_franchise_pack_is_list_kind_and_wishlist_capable(db):
    _seed_movies(db)
    entries = expand_pack(db, "franchises", "movie")
    assert len(entries) == 1
    e = entries[0]
    assert e["name"] == "Iron Man"                  # " Collection" suffix stripped
    assert e["kind"] == "list" and e["wishlist_capable"] is True
    assert e["definition"] == {"source": "tmdb_collection", "collection_id": 131292}
    assert e["count"] == 2


def test_studio_director_and_essentials_packs(db):
    _seed_movies(db)
    studios = {e["name"]: e["count"] for e in expand_pack(db, "studios", "movie")}
    assert studios["Fox"] == 2 and studios["Pixar"] == 1
    directors = {e["name"]: e["count"] for e in expand_pack(db, "directors", "movie")}
    assert directors == {"John McTiernan": 2}       # min_titles=2 filters one-offs
    ess = {e["name"]: e["count"] for e in expand_pack(db, "essentials", "movie")}
    assert ess["4K Ultra HD"] == 1
    assert ess["Critically Acclaimed"] == 4         # rating >= 7.5
    assert "Recently Added" in ess and "New Releases" in ess


def test_show_packs_use_network_not_studio(db):
    _add_show(db, 1, "The Wire", network="HBO", genres=("Crime",))
    _add_show(db, 2, "Oz", network="HBO", genres=("Crime",))
    packs = {p["id"] for p in list_packs(db, "show")}
    assert "networks" in packs and "studios" not in packs and "franchises" not in packs
    nets = {e["name"]: e["count"] for e in expand_pack(db, "networks", "show")}
    assert nets == {"HBO": 2}
    genres = {e["name"]: e["count"] for e in expand_pack(db, "genres", "show")}
    assert genres == {"Crime": 2}


def test_unknown_pack_or_wrong_media_returns_empty(db):
    assert expand_pack(db, "nope", "movie") == []
    assert expand_pack(db, "networks", "movie") == []
    assert expand_pack(db, "franchises", "show") == []


# ── apply: creates normal definitions, idempotent, wishlist honored ─────────
def test_apply_creates_normal_definitions(db):
    _seed_movies(db)
    r = apply_pack(db, "genres", "movie", ["genre:Action", "genre:Sci-Fi"])
    assert [c["name"] for c in r["created"]] == ["Action", "Sci-Fi"] and r["skipped"] == []
    defs = db.list_collection_definitions()
    assert {d["name"] for d in defs} == {"Action", "Sci-Fi"}
    full = db.get_collection_definition(r["created"][0]["id"])
    assert full["kind"] == "smart" and full["enabled"]
    assert not full["wishlist_missing"]             # smart entries never wishlist
    # It's a completely normal definition — the resolver just works on it.
    from core.video.collections.resolver import resolve_collection
    assert len(resolve_collection(db, full).owned) == 4


def test_apply_is_idempotent_and_marks_existing(db):
    _seed_movies(db)
    apply_pack(db, "genres", "movie", ["genre:Action"])
    # Second apply skips; expansion marks it as existing.
    r2 = apply_pack(db, "genres", "movie", ["genre:Action"])
    assert r2["created"] == [] and r2["skipped"] == ["Action"]
    assert len(db.list_collection_definitions()) == 1
    e = {x["name"]: x for x in expand_pack(db, "genres", "movie")}["Action"]
    assert e["exists"] is True
    # A hand-made same-name collection (different case) also blocks duplication.
    db.create_collection_definition("SCI-FI", media_type="movie")
    r3 = apply_pack(db, "genres", "movie", ["genre:Sci-Fi"])
    assert r3["created"] == [] and r3["skipped"] == ["Sci-Fi"]


def test_apply_franchise_wishlist_choice(db):
    _seed_movies(db)
    r = apply_pack(db, "franchises", "movie", ["franchise:131292"], wishlist_missing=True)
    full = db.get_collection_definition(r["created"][0]["id"])
    assert full["kind"] == "list" and full["wishlist_missing"]
    db.delete_collection_definition(full["id"])
    r = apply_pack(db, "franchises", "movie", ["franchise:131292"], wishlist_missing=False)
    full = db.get_collection_definition(r["created"][0]["id"])
    assert not full["wishlist_missing"]


def test_apply_ignores_unselected_and_unknown_keys(db):
    _seed_movies(db)
    r = apply_pack(db, "genres", "movie", ["genre:Action", "genre:DoesNotExist"])
    assert [c["name"] for c in r["created"]] == ["Action"]


# ── API seam ─────────────────────────────────────────────────────────────────
def _make_client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi._video_db


def test_presets_api_browse_and_apply(tmp_path):
    client, vdb = _make_client(tmp_path)
    _seed_movies(vdb)

    r = client.get("/api/video/collections/presets?media_type=movie").get_json()
    packs = {p["id"]: p for p in r["packs"]}
    assert set(packs) == {"genres", "decades", "franchises", "studios", "directors", "essentials"}
    assert packs["genres"]["available"] >= 3
    action = [e for e in packs["genres"]["entries"] if e["name"] == "Action"][0]
    assert action["count"] == 4 and action["exists"] is False

    r = client.post("/api/video/collections/presets/apply", json={
        "media_type": "movie", "pack": "genres", "keys": ["genre:Action"]}).get_json()
    assert r["ok"] and [c["name"] for c in r["created"]] == ["Action"]

    # Browse again → marked existing; re-apply → skipped, not duplicated.
    r = client.get("/api/video/collections/presets?media_type=movie").get_json()
    action = [e for e in {p["id"]: p for p in r["packs"]}["genres"]["entries"]
              if e["name"] == "Action"][0]
    assert action["exists"] is True

    r = client.post("/api/video/collections/presets/apply", json={
        "media_type": "movie", "pack": "genres", "keys": ["genre:Action"]}).get_json()
    assert r["ok"] and r["created"] == [] and r["skipped"] == ["Action"]


def test_presets_api_validates_input(tmp_path):
    client, _ = _make_client(tmp_path)
    assert client.post("/api/video/collections/presets/apply", json={}).status_code == 400
    assert client.post("/api/video/collections/presets/apply",
                       json={"pack": "genres", "keys": []}).status_code == 400
