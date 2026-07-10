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


def test_studio_pack_groups_brand_variants(db):
    # One brand, many label strings (the 23-item Hallmark problem): the pack
    # entry must cover ALL variants and count them together.
    _add_movie(db, 21, "Xmas 1", studio="Hallmark Channel")
    _add_movie(db, 22, "Xmas 2", studio="Hallmark Channel")
    _add_movie(db, 23, "Xmas 3", studio="Hallmark Media")
    _add_movie(db, 24, "Xmas 4", studio="Hallmark Entertainment")
    _add_movie(db, 25, "Indie", studio="A24")
    entries = {e["name"]: e for e in expand_pack(db, "studios", "movie")}
    hm = entries["Hallmark"]                                # shared brand token
    assert hm["count"] == 4
    assert hm["definition"]["rules"] == [{
        "field": "studio", "op": "in",
        "value": ["Hallmark Channel", "Hallmark Entertainment", "Hallmark Media"]}]
    # The rule really resolves all variants.
    assert len(db.resolve_smart_members("movie", hm["definition"])) == 4
    assert entries["A24"]["count"] == 1                     # single variant keeps its name


def test_studio_pack_unifies_disney_across_eras(db):
    # The 22-movie Disney bug: 'Disney' and 'Walt Disney *' didn't share a first
    # word, so classics and modern films split into separate entries. Token
    # grouping unifies every era under one brand.
    _add_movie(db, 31, "Snow White", year=1937, studio="Walt Disney Productions")
    _add_movie(db, 32, "Frozen", year=2013, studio="Walt Disney Animation Studios")
    _add_movie(db, 33, "Jungle Cruise", year=2021, studio="Walt Disney Pictures")
    _add_movie(db, 34, "Streamer", year=2023, studio="Disney")
    _add_movie(db, 35, "Indie", studio="A24")
    entries = {e["name"]: e for e in expand_pack(db, "studios", "movie")}
    dis = entries["Disney"]                                 # shared token labels the brand
    assert dis["count"] == 4
    assert sorted(dis["definition"]["rules"][0]["value"]) == [
        "Disney", "Walt Disney Animation Studios", "Walt Disney Pictures",
        "Walt Disney Productions"]
    assert len(db.resolve_smart_members("movie", dis["definition"])) == 4
    assert "Walt Disney Pictures" not in entries            # no fragment entries survive


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


# ── remote packs: charts / seasonal (fetcher-backed, counts = owned ∩ list) ──
def test_charts_pack_counts_owned_against_chart(db):
    _seed_movies(db)                                    # owned tmdb ids 1001..1005

    def fetcher(source, ref):
        assert source == "tmdb_chart"
        return [{"tmdb_id": i} for i in (1001, 1004, 9999)]

    entries = expand_pack(db, "charts", "movie", fetcher)
    by_name = {e["name"]: e for e in entries}
    top = by_name["Top Rated 250"]
    assert top["count"] == 2 and top["of_total"] == 3   # owns 2 of the chart's 3
    assert top["kind"] == "list" and top["suggested"] is True
    assert top["definition"] == {"source": "tmdb_chart", "chart": "top_movies", "limit": 250}
    assert by_name["Most Popular"]["definition"]["chart"] == "popular_movies"
    # Show charts use the show-side chart keys; shows are wishlist-capable too
    # (missing shows expand into aired-episode rows on sync).
    shows = expand_pack(db, "charts", "show", fetcher=None)
    assert {e["definition"]["chart"] for e in shows} == \
        {"toptv", "top_shows", "popular_shows", "trending_shows", "on_the_air"}
    imdb = [e for e in shows if e["name"] == "IMDb Top 250 TV"][0]
    assert imdb["definition"] == {"source": "imdb_chart", "chart": "toptv"}
    assert all(e["wishlist_capable"] is True for e in shows)


def test_charts_pack_survives_fetch_failure(db):
    _seed_movies(db)
    entries = expand_pack(db, "charts", "movie", fetcher=lambda s, r: (_ for _ in ()).throw(RuntimeError()))
    assert len(entries) == 5
    assert all(e["count"] is None for e in entries)     # '—' in the picker
    assert all(e["suggested"] for e in entries)         # charts stay pre-checked
    # No fetcher at all behaves the same.
    assert all(e["count"] is None for e in expand_pack(db, "charts", "movie"))


def test_seasonal_and_stories_packs(db):
    _seed_movies(db)
    seen = []

    def fetcher(source, ref):
        seen.append((source, ref))
        return [{"tmdb_id": 1001}]

    seasonal = {e["name"]: e for e in expand_pack(db, "seasonal", "movie", fetcher)}
    assert "Christmas" in seasonal and "Halloween" in seasonal
    assert seasonal["Christmas"]["definition"] == \
        {"source": "tmdb_keyword", "query": "christmas", "limit": 250}
    assert seasonal["Christmas"]["count"] == 1
    assert all(s == "tmdb_keyword" and r["kind"] == "movie" for s, r in seen)
    stories = {e["name"] for e in expand_pack(db, "stories", "movie", fetcher)}
    assert "Based on a Book" in stories and "Based on a Video Game" in stories


def test_universes_pack_unions_franchises_and_keywords(db):
    _seed_movies(db)
    seen = []

    def fetcher(source, ref):
        seen.append((source, ref))
        return [{"tmdb_id": 1004}, {"tmdb_id": 1005}, {"tmdb_id": 555}]

    entries = {e["name"]: e for e in expand_pack(db, "universes", "movie", fetcher)}
    assert {"Marvel Cinematic Universe", "Middle-earth", "Wizarding World",
            "Star Wars Saga"} <= set(entries)
    mcu = entries["Marvel Cinematic Universe"]
    assert mcu["kind"] == "list" and mcu["wishlist_capable"] is True
    assert mcu["count"] == 2 and mcu["of_total"] == 3      # owns Iron Man 1+2 of 3
    assert mcu["definition"]["source"] == "tmdb_union"
    assert mcu["definition"]["keywords"] == ["marvel cinematic universe"]
    me = entries["Middle-earth"]
    assert me["definition"]["collections"] == [119, 121938]
    assert all(s == "tmdb_union" for s, _ in seen)
    assert expand_pack(db, "universes", "show", fetcher) == []   # movies only


def test_franchise_backfill_drains_the_backlog(db):
    # Movies matched before the collection column have tmdb_collection_id NULL —
    # the pack under-reports until they're backfilled (the LOTR-is-missing bug).
    from core.video.collections.presets import backfill_missing_franchises
    _add_movie(db, 10, "Fellowship", year=2001)                  # tmdb 1010, no franchise
    _add_movie(db, 11, "Two Towers", year=2002)                  # tmdb 1011, no franchise

    class _Eng:
        def movie_collection(self, tmdb_id):
            if tmdb_id == 1010:
                return {"id": 119, "name": "The Lord of the Rings Collection"}
            if tmdb_id == 1011:
                return None                                       # lookup failed → retry later
            return {"id": None, "name": None}                     # genuinely standalone

    n = backfill_missing_franchises(db, engine=_Eng(), batch=2, cap=100)
    assert n >= 2
    franchises = {e["name"]: e for e in expand_pack(db, "franchises", "movie")}
    assert franchises["The Lord of the Rings"]["count"] == 1      # now discoverable
    # The failed lookup stays in the backlog for a later pass.
    assert any(m["tmdb_id"] == 1011 for m in db.movies_missing_collection(limit=50))


def test_apply_chart_creates_living_list_definition(db):
    _seed_movies(db)
    r = apply_pack(db, "charts", "movie", ["chart:top"], wishlist_missing=True,
                   fetcher=lambda s, ref: [{"tmdb_id": 1001}])
    full = db.get_collection_definition(r["created"][0]["id"])
    assert full["kind"] == "list" and full["wishlist_missing"]
    assert full["definition"] == {"source": "tmdb_chart", "chart": "top_movies", "limit": 250}
    # The resolver turns it into owned members + the missing set via the fetcher.
    from core.video.collections.resolver import resolve_collection
    res = resolve_collection(
        db, full, list_fetcher=lambda s, ref: [{"tmdb_id": 1001}, {"tmdb_id": 777}])
    assert res.ok and len(res.owned) == 1 and len(res.missing) == 1


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
    assert set(packs) == {"charts", "genres", "decades", "franchises", "universes",
                          "studios", "directors", "essentials", "seasonal", "stories"}
    assert packs["genres"]["available"] >= 3
    # Remote packs list fine with no engine — counts just resolve on sync.
    assert packs["charts"]["available"] == 5
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


def test_presets_catalog_is_instant_and_pure(tmp_path):
    # The skeleton first-paint source: pack identities only, no DB expansion.
    client, _ = _make_client(tmp_path)
    d = client.get("/api/video/collections/presets/catalog?media_type=movie").get_json()
    ids = [p["id"] for p in d["packs"]]
    assert "charts" in ids and "studios" in ids and "networks" not in ids
    assert all("entries" not in p for p in d["packs"])


def test_presets_payload_cached_with_fresh_exists_marks(tmp_path, monkeypatch):
    client, vdb = _make_client(tmp_path)
    _seed_movies(vdb)
    import core.video.collections.presets as presets_mod
    real = presets_mod.list_packs
    calls = []
    monkeypatch.setattr(presets_mod, "list_packs",
                        lambda *a, **k: calls.append(1) or real(*a, **k))

    r1 = client.get("/api/video/collections/presets?media_type=movie").get_json()
    assert len(calls) == 1
    a1 = [e for p in r1["packs"] if p["id"] == "genres" for e in p["entries"]
          if e["name"] == "Action"][0]
    assert a1["exists"] is False

    # Apply between browses; the second browse serves the CACHE (no recompute)
    # but re-marks exists — the only part that changed.
    client.post("/api/video/collections/presets/apply", json={
        "media_type": "movie", "pack": "genres", "keys": ["genre:Action"]})
    r2 = client.get("/api/video/collections/presets?media_type=movie").get_json()
    assert len(calls) == 1                                   # cache hit
    a2 = [e for p in r2["packs"] if p["id"] == "genres" for e in p["entries"]
          if e["name"] == "Action"][0]
    assert a2["exists"] is True


def test_presets_api_validates_input(tmp_path):
    client, _ = _make_client(tmp_path)
    assert client.post("/api/video/collections/presets/apply", json={}).status_code == 400
    assert client.post("/api/video/collections/presets/apply",
                       json={"pack": "genres", "keys": []}).status_code == 400
