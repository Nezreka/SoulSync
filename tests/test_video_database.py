"""Seam tests for the isolated video database (experimental branch).

These pin the contract that matters: the schema builds with all tables/views,
the no-polymorphic-id CHECK constraints actually reject bad rows, cascades fire,
the derived Watchlist/Wishlist/Calendar views return the right membership, the
settings KV roundtrips — and, critically, that the video DB layer imports
NOTHING from the music database (isolation).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from database.video_database import VideoDatabase, SCHEMA_VERSION

_MODULE = Path(__file__).resolve().parent.parent / "database" / "video_database.py"

EXPECTED_TABLES = {
    "meta", "root_folders", "quality_profiles", "video_settings",
    "movies", "shows", "seasons", "episodes", "channels", "channel_videos",
    "media_files", "downloads", "activity",
    "genres", "movie_genres", "show_genres", "people", "credits",
}
EXPECTED_VIEWS = {"v_watchlist", "v_wishlist", "v_calendar"}


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# ── schema ──────────────────────────────────────────────────────────────────

def test_schema_builds_with_all_tables_and_views(db):
    with db.connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        views = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
    assert EXPECTED_TABLES <= tables, f"missing tables: {EXPECTED_TABLES - tables}"
    assert views == EXPECTED_VIEWS


def test_schema_version_recorded(db):
    assert db.schema_version == SCHEMA_VERSION


def test_init_is_idempotent(tmp_path):
    path = str(tmp_path / "video_library.db")
    VideoDatabase(database_path=path)
    # Second construction on the same path must not raise or wipe data.
    db2 = VideoDatabase(database_path=path)
    assert db2.health_check()


def test_health_check_ok(db):
    assert db.health_check() is True


# ── no-polymorphic-id CHECK constraints ──────────────────────────────────────

def test_media_file_requires_exactly_one_owner(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO movies(id,title) VALUES (1,'M')")
        conn.execute("INSERT INTO shows(id,title) VALUES (1,'S')")
        conn.execute("INSERT INTO seasons(id,show_id,season_number) VALUES (1,1,1)")
        conn.execute("INSERT INTO episodes(id,show_id,season_id,season_number,episode_number) "
                     "VALUES (1,1,1,1,1)")
        # zero owners -> reject
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO media_files(relative_path) VALUES ('x.mkv')")
        # two owners -> reject
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO media_files(movie_id,episode_id,relative_path) "
                         "VALUES (1,1,'x.mkv')")
        # exactly one -> ok
        conn.execute("INSERT INTO media_files(movie_id,relative_path) VALUES (1,'x.mkv')")


def test_download_requires_exactly_one_target(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO movies(id,title) VALUES (1,'M')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO downloads(title) VALUES ('no target')")
        conn.execute("INSERT INTO downloads(movie_id,title) VALUES (1,'ok')")


def test_download_status_is_constrained(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO movies(id,title) VALUES (1,'M')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO downloads(movie_id,title,status) VALUES (1,'x','bogus')")


# ── cascades ──────────────────────────────────────────────────────────────────

def test_deleting_show_cascades_to_seasons_and_episodes(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO shows(id,title) VALUES (1,'S')")
        conn.execute("INSERT INTO seasons(id,show_id,season_number) VALUES (1,1,1)")
        conn.execute("INSERT INTO episodes(id,show_id,season_id,season_number,episode_number) "
                     "VALUES (1,1,1,1,1)")
        conn.execute("DELETE FROM shows WHERE id=1")
        assert conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0


# ── derived views ─────────────────────────────────────────────────────────────

def test_watchlist_view_is_monitored_shows_and_channels(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO shows(id,title,monitored) VALUES (1,'Followed',1)")
        conn.execute("INSERT INTO shows(id,title,monitored) VALUES (2,'Unfollowed',0)")
        conn.execute("INSERT INTO channels(id,youtube_id,title,monitored) VALUES (1,'yt1','Chan',1)")
        rows = {(r["kind"], r["title"]) for r in conn.execute("SELECT kind,title FROM v_watchlist")}
    assert rows == {("show", "Followed"), ("channel", "Chan")}


def test_wishlist_view_is_wanted_but_missing(db):
    with db.connect() as conn:
        # wanted movie (monitored, no file) -> in wishlist
        conn.execute("INSERT INTO movies(id,title,monitored,has_file) VALUES (1,'Want',1,0)")
        # owned movie -> not in wishlist
        conn.execute("INSERT INTO movies(id,title,monitored,has_file) VALUES (2,'Own',1,1)")
        # aired, monitored, missing episode -> in wishlist; future episode -> not
        conn.execute("INSERT INTO shows(id,title) VALUES (1,'S')")
        conn.execute("INSERT INTO seasons(id,show_id,season_number) VALUES (1,1,1)")
        conn.execute("INSERT INTO episodes(id,show_id,season_id,season_number,episode_number,"
                     "monitored,has_file,air_date) VALUES (1,1,1,1,1,1,0,'2000-01-01')")
        conn.execute("INSERT INTO episodes(id,show_id,season_id,season_number,episode_number,"
                     "monitored,has_file,air_date) VALUES (2,1,1,1,2,1,0,'2999-01-01')")
        rows = [(r["kind"], r["ref_id"]) for r in conn.execute("SELECT kind,ref_id FROM v_wishlist")]
    assert ("movie", 1) in rows        # wanted movie present
    assert ("movie", 2) not in rows    # owned movie absent
    assert ("episode", 1) in rows      # aired+missing episode present
    assert ("episode", 2) not in rows  # future episode absent


def test_dashboard_stats_empty_is_all_zero(db):
    s = db.dashboard_stats()
    assert s["library"] == {"movies": 0, "shows": 0, "episodes": 0, "size_bytes": 0}
    assert s["downloads"] == {"active": 0, "finished": 0, "speed_bps": 0}
    assert s["watchlist"] == 0 and s["wishlist"] == 0


def test_dashboard_stats_counts_content_and_downloads(db):
    with db.connect() as conn:
        conn.execute("INSERT INTO movies(id,title,monitored,has_file) VALUES (1,'M',1,0)")
        conn.execute("INSERT INTO shows(id,title,monitored) VALUES (1,'S',1)")
        conn.execute("INSERT INTO seasons(id,show_id,season_number) VALUES (1,1,1)")
        conn.execute("INSERT INTO episodes(id,show_id,season_id,season_number,episode_number) "
                     "VALUES (1,1,1,1,1)")
        conn.execute("INSERT INTO media_files(movie_id,relative_path,size_bytes) VALUES (1,'m.mkv',1000)")
        conn.execute("INSERT INTO downloads(movie_id,title,status,download_speed_bps) "
                     "VALUES (1,'d','downloading',500)")
        conn.commit()
    s = db.dashboard_stats()
    assert s["library"] == {"movies": 1, "shows": 1, "episodes": 1, "size_bytes": 1000}
    assert s["downloads"]["active"] == 1 and s["downloads"]["speed_bps"] == 500
    assert s["watchlist"] == 1 and s["wishlist"] == 1


def test_library_selection_roundtrip(db):
    assert db.get_library_selection("plex") == {"movies": None, "tv": None}
    db.set_library_selection("plex", "Movies", "TV Shows")
    assert db.get_library_selection("plex") == {"movies": "Movies", "tv": "TV Shows"}
    # Per-server keys don't collide.
    assert db.get_library_selection("jellyfin") == {"movies": None, "tv": None}


def test_settings_kv_roundtrip(db):
    assert db.get_setting("download_dir", "unset") == "unset"
    db.set_setting("download_dir", "/data/video")
    assert db.get_setting("download_dir") == "/data/video"
    db.set_setting("download_dir", "/data/video2")  # upsert
    assert db.get_setting("download_dir") == "/data/video2"


# ── scan upserts (server source of truth) ────────────────────────────────────

def test_upsert_movie_inserts_updates_and_attaches_file(db):
    mid = db.upsert_movie("plex", {
        "server_id": "p1", "title": "Dune", "year": 2021,
        "file": {"relative_path": "Dune.mkv", "size_bytes": 2000, "resolution": "2160p"}})
    with db.connect() as c:
        row = c.execute("SELECT title,year,has_file FROM movies WHERE id=?", (mid,)).fetchone()
        assert (row["title"], row["year"], row["has_file"]) == ("Dune", 2021, 1)
        f = c.execute("SELECT relative_path,size_bytes,resolution FROM media_files WHERE movie_id=?",
                      (mid,)).fetchone()
        assert (f["relative_path"], f["size_bytes"], f["resolution"]) == ("Dune.mkv", 2000, "2160p")
    # Re-scan same server id -> same row, updated fields, file replaced (not duplicated).
    mid2 = db.upsert_movie("plex", {
        "server_id": "p1", "title": "Dune: Part One", "year": 2021,
        "file": {"relative_path": "Dune1.mkv", "size_bytes": 3000}})
    assert mid2 == mid
    with db.connect() as c:
        assert c.execute("SELECT title FROM movies WHERE id=?", (mid,)).fetchone()["title"] == "Dune: Part One"
        files = c.execute("SELECT relative_path FROM media_files WHERE movie_id=?", (mid,)).fetchall()
        assert [r["relative_path"] for r in files] == ["Dune1.mkv"]
    assert db.dashboard_stats()["library"]["movies"] == 1


def test_upsert_show_tree_builds_seasons_episodes_and_prunes(db):
    item = {"server_id": "s1", "title": "Show", "seasons": [
        {"season_number": 1, "server_id": "se1", "episodes": [
            {"episode_number": 1, "title": "E1", "server_id": "ep1", "air_date": "2020-01-01",
             "file": {"relative_path": "e1.mkv", "size_bytes": 10}},
            {"episode_number": 2, "title": "E2", "server_id": "ep2", "air_date": "2020-01-08"}]}]}
    sid = db.upsert_show_tree("plex", item)
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM episodes WHERE show_id=?", (sid,)).fetchone()[0] == 2
        assert c.execute("SELECT has_file FROM episodes WHERE show_id=? AND episode_number=1",
                         (sid,)).fetchone()["has_file"] == 1
        assert c.execute("SELECT has_file FROM episodes WHERE show_id=? AND episode_number=2",
                         (sid,)).fetchone()["has_file"] == 0
    # Re-scan with E2 removed from the server -> it gets pruned.
    item["seasons"][0]["episodes"] = item["seasons"][0]["episodes"][:1]
    assert db.upsert_show_tree("plex", item) == sid
    with db.connect() as c:
        eps = [r["episode_number"] for r in c.execute(
            "SELECT episode_number FROM episodes WHERE show_id=?", (sid,)).fetchall()]
    assert eps == [1]


def test_upsert_stores_provider_ids(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Inception",
                                   "tmdb_id": 27205, "imdb_id": "tt1375666"})
    with db.connect() as c:
        row = c.execute("SELECT tmdb_id, imdb_id FROM movies WHERE id=?", (mid,)).fetchone()
    assert (row["tmdb_id"], row["imdb_id"]) == (27205, "tt1375666")

    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show", "tvdb_id": 121361,
                                       "tmdb_id": 1396, "imdb_id": "tt0903747", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1, "tvdb_id": 349232}]}]})
    with db.connect() as c:
        srow = c.execute("SELECT tvdb_id, tmdb_id, imdb_id FROM shows WHERE id=?", (sid,)).fetchone()
        erow = c.execute("SELECT tvdb_id FROM episodes WHERE show_id=?", (sid,)).fetchone()
    assert (srow["tvdb_id"], srow["tmdb_id"], srow["imdb_id"]) == (121361, 1396, "tt0903747")
    assert erow["tvdb_id"] == 349232


def test_show_detail_builds_season_episode_tree_with_rollups(db):
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Show", "year": 2019, "overview": "A show",
        "network": "HBO", "content_rating": "TV-MA", "status": "ended",
        "poster_url": "/p.jpg", "seasons": [
            {"season_number": 0, "episodes": [{"episode_number": 1, "title": "Special"}]},
            {"season_number": 1, "title": "Season One", "episodes": [
                {"episode_number": 1, "title": "Pilot", "air_date": "2019-01-01",
                 "file": {"relative_path": "e1.mkv", "size_bytes": 5}},
                {"episode_number": 2, "title": "Two", "air_date": "2019-01-08"}]}]})
    # backdrop_url is filled by TMDB enrichment, not the scan — simulate that.
    with db.connect() as c:
        c.execute("UPDATE shows SET backdrop_url='/b.jpg' WHERE id=?", (sid,))
        c.commit()
    d = db.show_detail(sid)
    assert d["title"] == "Show" and d["network"] == "HBO" and d["status"] == "ended"
    assert d["has_poster"] and d["has_backdrop"]
    assert (d["episode_total"], d["episode_owned"], d["season_count"]) == (3, 1, 2)
    # Season 0 renders as "Specials"; season 1 keeps its title; ordered by number.
    assert [s["season_number"] for s in d["seasons"]] == [0, 1]
    assert d["seasons"][0]["title"] == "Specials"
    s1 = d["seasons"][1]
    assert s1["title"] == "Season One"
    assert isinstance(s1["id"], int)              # season id present for the poster proxy
    assert (s1["episode_total"], s1["episode_owned"]) == (2, 1)
    assert s1["episodes"][0]["owned"] is True and s1["episodes"][1]["owned"] is False


def test_show_detail_returns_none_for_missing(db):
    assert db.show_detail(999999) is None


def test_capture_everything_movie(db):
    mid = db.upsert_movie("plex", {
        "server_id": "m1", "title": "Dune", "tagline": "Fear is the mind-killer",
        "rating": 8.4, "rating_critic": 83, "genres": ["Sci-Fi", "Adventure", "Sci-Fi"]})
    d = db.movie_detail(mid)
    assert d["tagline"] == "Fear is the mind-killer" and d["rating"] == 8.4 and d["rating_critic"] == 83
    assert d["genres"] == ["Adventure", "Sci-Fi"]          # deduped + sorted
    # Re-upsert with different genres replaces the links (no stale rows).
    db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "genres": ["Drama"]})
    assert db.movie_detail(mid)["genres"] == ["Drama"]


def test_capture_everything_show_and_episode_still(db):
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Show", "tagline": "Tick tock", "rating": 9.1,
        "first_air_date": "2015-01-16", "genres": ["Drama", "Mystery"], "seasons": [
            {"season_number": 1, "episodes": [
                {"episode_number": 1, "title": "Pilot", "still_url": "/ep1.jpg", "rating": 8.0}]}]})
    d = db.show_detail(sid)
    assert d["tagline"] == "Tick tock" and d["rating"] == 9.1 and d["first_air_date"] == "2015-01-16"
    assert d["genres"] == ["Drama", "Mystery"]
    ep = d["seasons"][0]["episodes"][0]
    assert ep["has_still"] is True and ep["rating"] == 8.0
    # Episode still resolves through the image proxy ref (server source from the episode row).
    with db.connect() as c:
        eid = c.execute("SELECT id FROM episodes WHERE title='Pilot'").fetchone()["id"]
    ref = db.get_art_ref("episode", eid, "poster")
    assert ref["poster_url"] == "/ep1.jpg" and ref["server_source"] == "plex"


def test_movie_detail_includes_owned_and_file(db):
    mid = db.upsert_movie("plex", {
        "server_id": "m1", "title": "Dune", "year": 2021, "overview": "Sand",
        "tmdb_id": 438631, "poster_url": "/p.jpg",
        "file": {"relative_path": "dune.mkv", "size_bytes": 99, "resolution": "2160p"}})
    d = db.movie_detail(mid)
    assert d["title"] == "Dune" and d["owned"] is True and d["tmdb_id"] == 438631
    assert d["file"] and d["file"]["resolution"] == "2160p"
    # A wishlist movie with no file reports owned False, file None.
    mid2 = db.upsert_movie("plex", {"server_id": "m2", "title": "Wanted"})
    d2 = db.movie_detail(mid2)
    assert d2["owned"] is False and d2["file"] is None


def test_get_art_ref_poster_vs_backdrop(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "poster_url": "/p.jpg"})
    with db.connect() as c:           # backdrop is enrichment-filled, not scanned
        c.execute("UPDATE shows SET backdrop_url='/b.jpg' WHERE id=?", (sid,))
        c.commit()
    assert db.get_art_ref("show", sid, "poster")["poster_url"] == "/p.jpg"
    assert db.get_art_ref("show", sid, "backdrop")["poster_url"] == "/b.jpg"
    assert db.get_art_ref("show", sid, "bogus") is None


def test_get_art_ref_season_inherits_show_server_source(db):
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "server_id": "se1", "poster_url": "/sp.jpg",
         "episodes": [{"episode_number": 1}]}]})
    with db.connect() as c:
        seid = c.execute("SELECT id FROM seasons WHERE season_number=1").fetchone()["id"]
    ref = db.get_art_ref("season", seid, "poster")
    # Season carries its own poster + server_id, but inherits the show's source.
    assert ref["poster_url"] == "/sp.jpg" and ref["server_source"] == "plex"
    assert ref["server_id"] == "se1"


def test_prune_missing_skips_when_over_half_would_be_removed(db):
    # >100 movies; a scan that "sees" only a couple must NOT wipe the rest
    # (mirrors music's deep-scan 50% safety against partial server failures).
    for i in range(150):
        db.upsert_movie("plex", {"server_id": "m%d" % i, "title": "M%d" % i})
    assert db.prune_missing("movies", "plex", {"m0", "m1"}) == 0
    assert db.table_count("movies") == 150


def test_upsert_show_tree_skips_episodes_without_number(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "E1"},
            {"episode_number": None, "title": "Unmatched special"}]}]})
    with db.connect() as c:
        eps = [r["episode_number"] for r in c.execute(
            "SELECT episode_number FROM episodes WHERE show_id=?", (sid,)).fetchall()]
    assert eps == [1]


def test_prune_missing_removes_unseen_top_level(db):
    db.upsert_movie("plex", {"server_id": "a", "title": "A"})
    db.upsert_movie("plex", {"server_id": "b", "title": "B"})
    assert db.prune_missing("movies", "plex", {"a"}) == 1
    with db.connect() as c:
        ids = {r["server_id"] for r in c.execute("SELECT server_id FROM movies").fetchall()}
    assert ids == {"a"}


def test_list_movies_and_shows(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "Zardoz", "year": 1974})
    db.upsert_movie("plex", {"server_id": "m2", "title": "Akira", "year": 1988})
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show", "seasons": [
        {"season_number": 1, "episodes": [
            {"episode_number": 1, "file": {"relative_path": "e1.mkv"}},
            {"episode_number": 2}]}]})
    assert [m["title"] for m in db.list_movies()] == ["Akira", "Zardoz"]  # title NOCASE sort
    shows = db.list_shows()
    assert len(shows) == 1
    assert (shows[0]["episode_count"], shows[0]["owned_count"]) == (2, 1)


def test_query_library_search_letter_sort_status_pagination(db):
    db.upsert_movie("plex", {"server_id": "1", "title": "The Matrix", "year": 1999,
                             "file": {"relative_path": "x.mkv", "resolution": "1080p"}})
    db.upsert_movie("plex", {"server_id": "2", "title": "Akira", "year": 1988})           # wanted
    db.upsert_movie("plex", {"server_id": "3", "title": "Avatar", "year": 2009,
                             "file": {"relative_path": "y.mkv"}})

    # Article-aware sort: "The Matrix" files under M.
    res = db.query_library("movies")
    assert [i["title"] for i in res["items"]] == ["Akira", "Avatar", "The Matrix"]
    assert res["pagination"]["total_count"] == 3

    assert [i["title"] for i in db.query_library("movies", letter="m")["items"]] == ["The Matrix"]
    assert [i["title"] for i in db.query_library("movies", search="aki")["items"]] == ["Akira"]
    assert {i["title"] for i in db.query_library("movies", status="owned")["items"]} == {"The Matrix", "Avatar"}
    assert [i["title"] for i in db.query_library("movies", status="wanted")["items"]] == ["Akira"]
    assert [i["title"] for i in db.query_library("movies", sort="year")["items"]] == ["Avatar", "The Matrix", "Akira"]

    # Resolution badge field comes through.
    assert db.query_library("movies", search="matrix")["items"][0]["resolution"] == "1080p"

    # Pagination.
    p1 = db.query_library("movies", limit=2, page=1)
    assert len(p1["items"]) == 2 and p1["pagination"]["total_pages"] == 2 and p1["pagination"]["has_next"]
    p2 = db.query_library("movies", limit=2, page=2)
    assert len(p2["items"]) == 1 and p2["pagination"]["has_prev"]


def test_query_library_shows_status_and_counts(db):
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Owned Show", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1, "file": {"relative_path": "e.mkv"}}]}]})
    db.upsert_show_tree("plex", {"server_id": "s2", "title": "Wanted Show", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    assert [i["title"] for i in db.query_library("shows", status="owned")["items"]] == ["Owned Show"]
    assert [i["title"] for i in db.query_library("shows", status="wanted")["items"]] == ["Wanted Show"]
    owned = db.query_library("shows", search="Owned")["items"][0]
    assert (owned["episode_count"], owned["owned_count"]) == (1, 1)


# ── enrichment plumbing ───────────────────────────────────────────────────────

def test_enrichment_columns_present(db):
    with db.connect() as c:
        mcols = {r[1] for r in c.execute("PRAGMA table_info(movies)").fetchall()}
        scols = {r[1] for r in c.execute("PRAGMA table_info(shows)").fetchall()}
    assert {"tmdb_match_status", "tmdb_last_attempted"} <= mcols
    assert {"tmdb_match_status", "tvdb_match_status", "tvdb_last_attempted"} <= scols


def test_ensure_columns_is_idempotent(db):
    # Running the migration again on an already-migrated DB must not error.
    with db.connect() as c:
        db._ensure_columns(c)
        c.commit()
    assert db.enrichment_breakdown("tmdb")["movie"]["pending"] == 0


def test_enrichment_next_pending_then_none_when_fresh(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "A"})
    db.upsert_movie("plex", {"server_id": "m2", "title": "B", "tmdb_id": 5})
    nxt = db.enrichment_next("tmdb")
    assert nxt and nxt["kind"] == "movie"
    db.enrichment_apply("tmdb", "movie", nxt["id"], matched=True, external_id=1)
    nxt2 = db.enrichment_next("tmdb")
    assert nxt2 and nxt2["id"] != nxt["id"]
    db.enrichment_apply("tmdb", "movie", nxt2["id"], matched=False)
    # both attempted; not_found is fresh (<30d) so nothing is due
    assert db.enrichment_next("tmdb") is None


def test_enrichment_apply_matched_sets_id_status_and_metadata(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "A"})
    db.enrichment_apply("tmdb", "movie", mid, matched=True, external_id=27205,
                        metadata={"overview": "O", "backdrop_url": "/b.jpg", "imdb_id": "tt1",
                                  "bogus_col": "x"})
    with db.connect() as c:
        row = c.execute("SELECT tmdb_id, tmdb_match_status, overview, backdrop_url, imdb_id "
                        "FROM movies WHERE id=?", (mid,)).fetchone()
    assert (row["tmdb_id"], row["tmdb_match_status"]) == (27205, "matched")
    assert (row["overview"], row["backdrop_url"], row["imdb_id"]) == ("O", "/b.jpg", "tt1")


def test_enrichment_apply_survives_legacy_unique(db):
    # Simulate a pre-existing DB where tvdb_id still carries a UNIQUE index.
    with db.connect() as c:
        c.execute("CREATE UNIQUE INDEX ux_legacy_shows_tvdb ON shows(tvdb_id)")
        c.commit()
    a = db.upsert_show_tree("plex", {"server_id": "s1", "title": "A", "seasons": []})
    b = db.upsert_show_tree("plex", {"server_id": "s2", "title": "B", "seasons": []})
    db.enrichment_apply("tvdb", "show", a, matched=True, external_id=555, metadata={"overview": "OA"})
    # b would collide on tvdb_id=555 — must NOT crash; keeps existing id, records the rest.
    db.enrichment_apply("tvdb", "show", b, matched=True, external_id=555, metadata={"overview": "OB"})
    with db.connect() as c:
        ra = c.execute("SELECT tvdb_id, tvdb_match_status FROM shows WHERE id=?", (a,)).fetchone()
        rb = c.execute("SELECT tvdb_id, tvdb_match_status, overview FROM shows WHERE id=?", (b,)).fetchone()
    assert (ra["tvdb_id"], ra["tvdb_match_status"]) == (555, "matched")
    assert rb["tvdb_id"] is None and rb["tvdb_match_status"] == "matched" and rb["overview"] == "OB"


def test_upsert_movie_survives_legacy_unique_tmdb(db):
    # Old DBs created movies.tmdb_id UNIQUE; the new model allows the same film in
    # >1 library. The scan must STORE the second movie (dropping the colliding id),
    # not skip it with an IntegrityError.
    with db.connect() as c:
        c.execute("CREATE UNIQUE INDEX ux_legacy_movies_tmdb ON movies(tmdb_id)")
        c.commit()
    a = db.upsert_movie("plex", {"server_id": "m1", "title": "A", "tmdb_id": 548522})
    b = db.upsert_movie("plex", {"server_id": "m2", "title": "B", "tmdb_id": 548522})
    assert a != b                                    # both rows exist (not skipped)
    with db.connect() as c:
        ra = c.execute("SELECT tmdb_id FROM movies WHERE id=?", (a,)).fetchone()
        rb = c.execute("SELECT title, tmdb_id FROM movies WHERE id=?", (b,)).fetchone()
    assert ra["tmdb_id"] == 548522
    assert rb["title"] == "B" and rb["tmdb_id"] is None   # kept the row, dropped the dup id


def test_upsert_show_survives_legacy_unique_tvdb(db):
    with db.connect() as c:
        c.execute("CREATE UNIQUE INDEX ux_legacy_shows_tvdb2 ON shows(tvdb_id)")
        c.commit()
    a = db.upsert_show_tree("plex", {"server_id": "s1", "title": "A", "tvdb_id": 9000, "seasons": []})
    b = db.upsert_show_tree("plex", {"server_id": "s2", "title": "B", "tvdb_id": 9000, "seasons": []})
    assert a != b
    with db.connect() as c:
        rb = c.execute("SELECT title, tvdb_id FROM shows WHERE id=?", (b,)).fetchone()
    assert rb["title"] == "B" and rb["tvdb_id"] is None


def test_enrichment_breakdown_unmatched_retry(db):
    a = db.upsert_movie("plex", {"server_id": "m1", "title": "A"})
    b = db.upsert_movie("plex", {"server_id": "m2", "title": "B"})
    db.enrichment_apply("tmdb", "movie", a, matched=True, external_id=1)
    db.enrichment_apply("tmdb", "movie", b, matched=False)
    assert db.enrichment_breakdown("tmdb")["movie"] == {"matched": 1, "not_found": 1, "errors": 0, "pending": 0}
    un = db.enrichment_unmatched("tmdb", "movie", status="not_found")
    assert [i["title"] for i in un["items"]] == ["B"] and un["total"] == 1
    assert db.enrichment_retry("tmdb", "movie", scope="failed") == 1
    assert db.enrichment_breakdown("tmdb")["movie"]["pending"] == 1


def test_enrichment_backfills_only_gaps_never_clobbers_server(db):
    # Server gave overview + a genre; enrichment must fill the EMPTY fields
    # (tagline/rating) but leave the server's overview + genres untouched.
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune",
                                   "overview": "server overview", "genres": ["Sci-Fi"]})
    db.enrichment_apply("tmdb", "movie", mid, matched=True, external_id=438631, metadata={
        "overview": "tmdb overview", "tagline": "Fear is the mind-killer",
        "rating": 8.4, "genres": ["Drama"]})
    d = db.movie_detail(mid)
    assert d["overview"] == "server overview"     # NOT clobbered
    assert d["tagline"] == "Fear is the mind-killer" and d["rating"] == 8.4   # gaps filled
    assert d["genres"] == ["Sci-Fi"]              # had genres → enrichment left them


def test_enrichment_backfills_genres_when_item_has_none(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "X"})   # no genres
    db.enrichment_apply("tmdb", "movie", mid, matched=True, external_id=1,
                        metadata={"genres": ["Drama", "Comedy"]})
    assert db.movie_detail(mid)["genres"] == ["Comedy", "Drama"]


def test_enrichment_backfills_cast_and_crew(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": []})
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1, metadata={
        "cast": [
            {"name": "Aidan Gillen", "tmdb_id": 39388, "character": "James Cole",
             "photo_url": "https://img/ag.jpg"},
            {"name": "Amanda Schull", "tmdb_id": 84223, "character": "Cassie"}],
        "crew": [{"name": "Terry Matalas", "tmdb_id": 1, "job": "Creator"}]})
    d = db.show_detail(sid)
    assert [c["name"] for c in d["cast"]] == ["Aidan Gillen", "Amanda Schull"]   # billing order
    assert d["cast"][0]["character"] == "James Cole" and d["cast"][0]["photo"] == "https://img/ag.jpg"
    assert d["crew"] == [{"name": "Terry Matalas", "job": "Creator"}]
    # Clearlogo backfills like the other art (gap-only) and rides in the payload.
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1,
                        metadata={"logo_url": "https://img/logo.png"})
    assert db.show_detail(sid)["logo"] == "https://img/logo.png"
    # People are deduped across titles by tmdb_id.
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    db.enrichment_apply("tmdb", "movie", mid, matched=True, external_id=2,
                        metadata={"cast": [{"name": "Aidan Gillen", "tmdb_id": 39388, "character": "X"}]})
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM people WHERE tmdb_id=39388").fetchone()[0] == 1


def test_enrichment_does_not_clobber_existing_credits(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": []})
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1,
                        metadata={"cast": [{"name": "First", "tmdb_id": 1}]})
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1,
                        metadata={"cast": [{"name": "Second", "tmdb_id": 2}]})
    assert [c["name"] for c in db.show_detail(sid)["cast"]] == ["First"]   # kept, not replaced


def test_enrichment_backfills_season_posters_only_when_missing(db):
    # Season 1 has no art (server didn't provide it); season 2 has server art.
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1}]},
        {"season_number": 2, "poster_url": "/server.jpg", "episodes": [{"episode_number": 1}]}]})
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1, metadata={"seasons": [
        {"season_number": 1, "poster_url": "https://image.tmdb.org/s1.jpg"},
        {"season_number": 2, "poster_url": "https://image.tmdb.org/s2.jpg"}]})
    with db.connect() as c:
        rows = {r["season_number"]: r["poster_url"] for r in c.execute(
            "SELECT season_number, poster_url FROM seasons WHERE show_id=?", (sid,)).fetchall()}
    assert rows[1] == "https://image.tmdb.org/s1.jpg"   # gap filled from TMDB
    assert rows[2] == "/server.jpg"                      # server art kept, not clobbered
    assert db.show_detail(sid)["seasons"][0]["has_poster"] is True


def test_backfill_episodes_gap_only_and_season_overview(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [
            {"episode_number": 1, "overview": "server overview"},   # has overview already
            {"episode_number": 2}]}]})                              # bare
    n = db.backfill_episodes(sid, 1, [
        {"episode_number": 1, "still_url": "/e1.jpg", "overview": "tmdb overview", "rating": 8.0},
        {"episode_number": 2, "still_url": "/e2.jpg", "overview": "tmdb e2", "rating": 7.0},
    ], season_overview="Season one")
    assert n == 2
    eps = {e["episode_number"]: e for e in db.show_detail(sid)["seasons"][0]["episodes"]}
    assert eps[1]["has_still"] is True and eps[1]["overview"] == "server overview"  # overview kept
    assert eps[2]["has_still"] is True and eps[2]["overview"] == "tmdb e2"          # gap filled
    with db.connect() as c:
        assert c.execute("SELECT overview FROM seasons WHERE show_id=? AND season_number=1",
                         (sid,)).fetchone()["overview"] == "Season one"


def test_backfill_inserts_missing_episodes_as_unowned(db):
    # Server has only E1; the provider's season has E1-E3 → E2/E3 are inserted
    # MISSING (has_file=0) so the page shows what we have AND what we need.
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1, "file": {"relative_path": "e1.mkv"}}]}]})
    db.backfill_episodes(sid, 1, [
        {"episode_number": 1, "title": "One"},
        {"episode_number": 2, "title": "Two", "air_date": "2020-01-08"},
        {"episode_number": 3, "title": "Three"}])
    s1 = db.show_detail(sid)["seasons"][0]
    assert (s1["episode_total"], s1["episode_owned"]) == (3, 1)     # full list, 1 owned
    by = {e["episode_number"]: e for e in s1["episodes"]}
    assert by[1]["owned"] is True and by[2]["owned"] is False and by[3]["owned"] is False
    assert by[2]["title"] == "Two" and by[2]["air_date"] == "2020-01-08"


def test_backfill_creates_fully_missing_season(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    db.backfill_episodes(sid, 2, [{"episode_number": 1, "title": "S2E1"}],
                         season_poster="https://img/s2.jpg")          # season 2 not on the server
    d = db.show_detail(sid)
    s2 = [s for s in d["seasons"] if s["season_number"] == 2][0]
    assert (s2["episode_total"], s2["episode_owned"]) == (1, 0) and s2["has_poster"] is True


def test_missing_episodes_survive_a_rescan_prune(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1, "file": {"relative_path": "e1.mkv"}}]}]})
    db.backfill_episodes(sid, 1, [{"episode_number": 1}, {"episode_number": 2}, {"episode_number": 3}])
    # Re-scan: the server still only reports E1; the prune must NOT remove the
    # enrichment-added missing episodes (server_id NULL).
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1, "file": {"relative_path": "e1.mkv"}}]}]})
    with db.connect() as c:
        nums = [r["episode_number"] for r in c.execute(
            "SELECT episode_number FROM episodes WHERE show_id=? ORDER BY episode_number", (sid,)).fetchall()]
    assert nums == [1, 2, 3]


def test_breakdown_reports_episode_art_coverage_for_tmdb(db):
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1, "still_url": "/e1.jpg"},
                                          {"episode_number": 2}]}]})
    bd = db.enrichment_breakdown("tmdb")
    assert bd["episode"]["matched"] == 1 and bd["episode"]["pending"] == 1
    assert bd["episode"].get("coverage_only") is True
    # TVDB doesn't cascade episodes → no episode coverage entry.
    assert "episode" not in db.enrichment_breakdown("tvdb")


def test_unmatched_lists_episodes_missing_art(db):
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1, "title": "Has", "still_url": "/e1.jpg"},
                                          {"episode_number": 2, "title": "Missing"}]}]})
    res = db.enrichment_unmatched("tmdb", "episode", status="unmatched")
    assert res["total"] == 1 and "Missing" in res["items"][0]["title"]


def test_error_status_is_distinct_and_retryable_in_ui(db):
    a = db.upsert_movie("plex", {"server_id": "m1", "title": "A"})
    db.enrichment_apply("tmdb", "movie", a, matched=False, error=True)
    bd = db.enrichment_breakdown("tmdb")["movie"]
    assert bd == {"matched": 0, "not_found": 0, "errors": 1, "pending": 0}
    # Errored items surface in the modal's "unmatched" list so they can be retried.
    assert db.enrichment_unmatched("tmdb", "movie", status="unmatched")["total"] == 1
    # ...but NOT in the strict 'not_found'-only view.
    assert db.enrichment_unmatched("tmdb", "movie", status="not_found")["total"] == 0
    # "Retry all failed" re-queues errors too (back to pending/NULL).
    assert db.enrichment_retry("tmdb", "movie", scope="failed") == 1
    assert db.enrichment_breakdown("tmdb")["movie"]["pending"] == 1


def test_tvdb_is_shows_only(db):
    # TVDB enriches shows, never movies. The breakdown must not advertise a
    # movie bucket, and asking for tvdb movies returns empty (never garbage) —
    # this is what keeps the Manage-Workers modal from showing a Movies view
    # for TVDB.
    assert "movie" not in db.enrichment_breakdown("tvdb")
    assert "show" in db.enrichment_breakdown("tvdb")
    db.upsert_movie("plex", {"server_id": "m1", "title": "A"})
    assert db.enrichment_unmatched("tvdb", "movie") == {"items": [], "total": 0}


# ── isolation: the video DB imports nothing from music ───────────────────────

def test_video_db_module_imports_nothing_from_music():
    src = _MODULE.read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "music" not in stripped.lower(), f"music import leaked in: {stripped!r}"


def test_video_db_uses_distinct_default_path_and_env():
    src = _MODULE.read_text(encoding="utf-8")
    assert "video_library.db" in src
    assert "VIDEO_DATABASE_PATH" in src      # distinct env var from music's DATABASE_PATH
    assert "music_library.db" not in src
