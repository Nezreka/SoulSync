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
        # airing library show → on the curated watchlist by default
        conn.execute("INSERT INTO shows(id,title,monitored,tmdb_id,status) VALUES (1,'S',1,555,'continuing')")
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
    # watchlist = the airing show (curated default); wishlist is cleared for now
    assert s["watchlist"] == 1 and s["wishlist"] == 0


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
    # Full media specs surface for the owned-media block.
    assert "video_codec" in d["file"] and "release_source" in d["file"]
    assert d["files"] == [d["file"]]                  # all versions (one here)
    # A wishlist movie with no file reports owned False, file None.
    mid2 = db.upsert_movie("plex", {"server_id": "m2", "title": "Wanted"})
    d2 = db.movie_detail(mid2)
    assert d2["owned"] is False and d2["file"] is None and d2["files"] == []


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
    assert d["crew"] == [{"name": "Terry Matalas", "job": "Creator", "tmdb_id": 1}]
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


def test_apply_ratings_and_payload(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune"})
    db.apply_ratings("movie", mid, {"imdb_rating": 8.4, "rt_rating": 95, "metacritic": 74})
    d = db.movie_detail(mid)
    assert (d["imdb_rating"], d["rt_rating"], d["metacritic"]) == (8.4, 95, 74)
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": []})
    db.apply_ratings("show", sid, {"imdb_rating": 9.1})       # partial is fine
    sd = db.show_detail(sid)
    assert sd["imdb_rating"] == 9.1 and sd["rt_rating"] is None


def test_episodes_synced_flag_drives_lazy_refresh(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": []})
    assert db.show_detail(sid)["episodes_synced"] is False    # → lazy refresh will run
    db.mark_episodes_synced(sid)
    assert db.show_detail(sid)["episodes_synced"] is True      # → won't re-cascade


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


def test_servers_do_not_commingle_in_reads(db):
    """Per-server isolation (mirrors the music side): the SAME movie/show on Plex
    and Jellyfin are separate rows, and scoped reads only ever return the active
    server's data — so a Jellyfin scan never shows up alongside Plex."""
    # Same title on both servers → two distinct rows.
    db.upsert_movie("plex", {"server_id": "p1", "title": "Dune", "tmdb_id": 438631, "year": 2021})
    db.upsert_movie("jellyfin", {"server_id": "j1", "title": "Dune", "tmdb_id": 438631, "year": 2021})
    db.upsert_show_tree("plex", {"server_id": "ps", "title": "Severance", "tmdb_id": 95396, "seasons": []})
    db.upsert_show_tree("jellyfin", {"server_id": "js", "title": "Severance", "tmdb_id": 95396, "seasons": []})

    # query_library is scoped per server (and unscoped == all).
    assert db.query_library("movies", server_source="plex")["pagination"]["total_count"] == 1
    assert db.query_library("movies", server_source="jellyfin")["pagination"]["total_count"] == 1
    assert db.query_library("movies")["pagination"]["total_count"] == 2
    assert db.query_library("shows", server_source="plex")["pagination"]["total_count"] == 1

    # dashboard counts are scoped; None counts everything.
    assert db.dashboard_stats("plex")["library"]["movies"] == 1
    assert db.dashboard_stats("jellyfin")["library"]["movies"] == 1
    assert db.dashboard_stats()["library"]["movies"] == 2

    # ownership lookup resolves to the row on the ACTIVE server, not the other.
    plex_id = db.library_id_for_tmdb("movie", 438631, "plex")
    jelly_id = db.library_id_for_tmdb("movie", 438631, "jellyfin")
    assert plex_id is not None and jelly_id is not None and plex_id != jelly_id


def test_prune_is_scoped_to_one_server(db):
    """A deep scan of one server must never prune the other server's rows."""
    db.upsert_movie("plex", {"server_id": "p1", "title": "Dune"})
    db.upsert_movie("jellyfin", {"server_id": "j1", "title": "Arrival"})
    # Deep-scan Plex sees nothing → prunes only Plex rows, leaves Jellyfin intact.
    db.prune_missing("movies", "plex", seen_ids=[])
    assert db.query_library("movies", server_source="plex")["pagination"]["total_count"] == 0
    assert db.query_library("movies", server_source="jellyfin")["pagination"]["total_count"] == 1


# ── user watchlist (shows + people) ─────────────────────────────────────

def test_watchlist_add_list_remove(db):
    assert db.add_to_watchlist("show", 1399, "Game of Thrones", poster_url="/p.jpg", library_id=7) is True
    assert db.add_to_watchlist("person", 287, "Brad Pitt") is True
    assert db.add_to_watchlist("movie", 1, "nope") is False          # wrong kind
    assert db.add_to_watchlist("show", 0, "no id") is False          # no tmdb id
    rows = db.list_watchlist()
    assert {r["kind"] for r in rows} == {"show", "person"}
    assert db.list_watchlist("person")[0]["title"] == "Brad Pitt"
    assert db.remove_from_watchlist("show", 1399) is True
    assert db.remove_from_watchlist("show", 1399) is True            # idempotent (mute tombstone)
    assert all(r["tmdb_id"] != 1399 for r in db.list_watchlist("show"))  # gone from the list


def test_watchlist_airing_library_shows_are_watched_by_default(db):
    # Two library shows: one still airing, one ended. No explicit watchlist rows.
    with db.connect() as conn:
        conn.execute("INSERT INTO shows(id,title,tmdb_id,status) VALUES (1,'Severance',95396,'continuing')")
        conn.execute("INSERT INTO shows(id,title,tmdb_id,status) VALUES (2,'The Wire',1438,'ended')")
        conn.commit()
    # The airing show is on the watchlist by default; the ended one is not.
    shows = db.list_watchlist("show")
    assert [s["tmdb_id"] for s in shows] == [95396]
    assert shows[0].get("auto") is True and shows[0]["library_id"] == 1
    assert db.watchlist_state("show", [95396, 1438]) == {95396: True}  # ended one absent

    # Un-following the airing show (mute tombstone) drops it from the default…
    db.remove_from_watchlist("show", 95396)
    assert db.list_watchlist("show") == []
    assert db.watchlist_state("show", [95396]) == {}

    # …and explicitly re-following brings it back (clears the mute).
    db.add_to_watchlist("show", 95396, "Severance")
    assert db.watchlist_state("show", [95396]) == {95396: True}


def test_watchlist_reAdd_is_upsert_and_coalesces_library_id(db):
    db.add_to_watchlist("show", 1399, "GoT", poster_url="/a.jpg", library_id=7)
    # Re-add WITHOUT library_id/poster (e.g. from a TMDB search card) must not
    # wipe the previously-known library_id/poster, but should refresh the title.
    db.add_to_watchlist("show", 1399, "Game of Thrones")
    rows = db.list_watchlist("show")
    assert len(rows) == 1                                            # no duplicate
    assert rows[0]["title"] == "Game of Thrones"                    # refreshed
    assert rows[0]["library_id"] == 7 and rows[0]["poster_url"] == "/a.jpg"  # preserved


def test_watchlist_state_and_counts(db):
    db.add_to_watchlist("show", 1399, "GoT")
    db.add_to_watchlist("show", 1396, "Breaking Bad")
    db.add_to_watchlist("person", 287, "Brad Pitt")
    assert db.watchlist_state("show", [1399, 1396, 9999]) == {1399: True, 1396: True}
    assert db.watchlist_state("show", []) == {}
    assert db.watchlist_state("person", [287]) == {287: True}
    assert db.watchlist_counts() == {"show": 2, "person": 1, "total": 3}


def test_query_watchlist_paginates_and_searches(db):
    for i in range(1, 8):
        db.add_to_watchlist("person", 100 + i, "Person %d" % i)
    db.add_to_watchlist("person", 200, "Brad Pitt")
    res = db.query_watchlist("person", page=1, limit=3)
    assert len(res["items"]) == 3
    assert res["pagination"]["total_count"] == 8 and res["pagination"]["total_pages"] == 3
    assert res["pagination"]["has_next"] is True and res["pagination"]["has_prev"] is False
    # search (case-insensitive title contains)
    res2 = db.query_watchlist("person", search="brad", limit=60)
    assert len(res2["items"]) == 1 and res2["items"][0]["title"] == "Brad Pitt"
    assert res2["pagination"]["total_count"] == 1
    # page beyond range clamps to the last page
    assert db.query_watchlist("person", page=99, limit=3)["pagination"]["page"] == 3


# ── wishlist (movies + episodes; show/season are bulk ops over episodes) ──────

def test_wishlist_movie_add_is_idempotent(db):
    assert db.add_movie_to_wishlist(603, "The Matrix", year=1999, poster_url="/m.jpg") is True
    db.add_movie_to_wishlist(603, "The Matrix", year=1999)            # re-add → upsert, not dup
    res = db.query_wishlist("movie")
    assert len(res["items"]) == 1
    m = res["items"][0]
    assert m["tmdb_id"] == 603 and m["year"] == 1999 and m["poster_url"] == "/m.jpg"  # poster kept
    assert db.wishlist_counts()["movie"] == 1


def test_wishlist_episodes_group_into_show_tree(db):
    n = db.add_episodes_to_wishlist(1396, "Breaking Bad", [
        {"season_number": 1, "episode_number": 1, "title": "Pilot", "air_date": "2008-01-20"},
        {"season_number": 1, "episode_number": 2, "title": "Cat's in the Bag"},
        {"season_number": 2, "episode_number": 1, "title": "Seven Thirty-Seven"}],
        poster_url="/bb.jpg")
    assert n == 3
    db.add_episodes_to_wishlist(1396, "Breaking Bad", [
        {"season_number": 1, "episode_number": 1, "title": "Pilot"}])   # re-add → no dup
    res = db.query_wishlist("show")
    assert res["pagination"]["total_count"] == 1                        # one show
    show = res["items"][0]
    assert show["tmdb_id"] == 1396 and show["wanted"] == 3 and show["done"] == 0
    assert [s["season_number"] for s in show["seasons"]] == [1, 2]
    assert [e["episode_number"] for e in show["seasons"][0]["episodes"]] == [1, 2]
    counts = db.wishlist_counts()
    assert counts == {"movie": 0, "show": 1, "episode": 3, "total": 3}


def test_wishlist_remove_scopes(db):
    db.add_movie_to_wishlist(603, "The Matrix")
    db.add_episodes_to_wishlist(1396, "Breaking Bad", [
        {"season_number": 1, "episode_number": 1}, {"season_number": 1, "episode_number": 2},
        {"season_number": 2, "episode_number": 1}])
    # remove one episode
    assert db.remove_from_wishlist("episode", tmdb_id=1396, season_number=1, episode_number=2) == 1
    assert db.wishlist_counts()["episode"] == 2
    # remove a whole season
    assert db.remove_from_wishlist("season", tmdb_id=1396, season_number=1) == 1   # only S1E1 left in S1
    assert db.wishlist_counts()["episode"] == 1
    # remove the whole show (its remaining episodes)
    assert db.remove_from_wishlist("show", tmdb_id=1396) == 1
    assert db.wishlist_counts()["show"] == 0
    # movie still there; remove it
    assert db.remove_from_wishlist("movie", tmdb_id=603) == 1
    assert db.wishlist_counts()["total"] == 0


def test_wishlist_movie_and_episode_same_tmdb_dont_collide(db):
    # A movie and a show could share a tmdb id across namespaces — the partial
    # uniques must keep them independent.
    db.add_movie_to_wishlist(42, "Some Movie")
    db.add_episodes_to_wishlist(42, "Some Show", [{"season_number": 1, "episode_number": 1}])
    c = db.wishlist_counts()
    assert c["movie"] == 1 and c["episode"] == 1


def test_wishlist_state_hydration(db):
    db.add_movie_to_wishlist(603, "The Matrix")
    db.add_episodes_to_wishlist(1396, "Breaking Bad", [
        {"season_number": 1, "episode_number": 1}, {"season_number": 2, "episode_number": 3}])
    st = db.wishlist_state(movie_ids=[603, 604], show_tmdb_id=1396)
    assert st["movies"] == {603}
    assert st["episodes"] == {"1_1", "2_3"}


def test_wishlist_query_search_and_paging(db):
    for i in range(5):
        db.add_movie_to_wishlist(100 + i, "Movie %d" % i)
    db.add_movie_to_wishlist(900, "Zebra")
    assert db.query_wishlist("movie", search="zeb")["pagination"]["total_count"] == 1
    p1 = db.query_wishlist("movie", page=1, limit=2)
    assert len(p1["items"]) == 2 and p1["pagination"]["total_pages"] == 3
    assert db.query_wishlist("movie", page=99, limit=2)["pagination"]["page"] == 3   # clamps


def test_wishlist_keys_for_shows(db):
    db.add_episodes_to_wishlist(1396, "BB", [{"season_number": 1, "episode_number": 1},
                                             {"season_number": 2, "episode_number": 3}])
    db.add_episodes_to_wishlist(1399, "GoT", [{"season_number": 1, "episode_number": 1}])
    keys = db.wishlist_keys_for_shows([1396, 1399, 9999])
    assert keys[1396] == {"1_1", "2_3"} and keys[1399] == {"1_1"} and 9999 not in keys
    assert db.wishlist_keys_for_shows([]) == {}


def test_wishlist_query_sort(db):
    db.add_episodes_to_wishlist(1, "Alpha", [{"season_number": 1, "episode_number": 1}])               # 1 ep
    db.add_episodes_to_wishlist(2, "Zeta", [{"season_number": 1, "episode_number": i} for i in range(5)])  # 5 eps
    # CURRENT_TIMESTAMP collides within a second — pin distinct add-times so the
    # FIFO vs newest ordering is deterministic.
    with db.connect() as c:
        c.execute("UPDATE video_wishlist SET date_added='2024-01-01 00:00:00' WHERE tmdb_id=1")
        c.execute("UPDATE video_wishlist SET date_added='2024-01-02 00:00:00' WHERE tmdb_id=2")
        c.commit()
    # most-wanted first
    w = [s["title"] for s in db.query_wishlist("show", sort="wanted")["items"]]
    assert w[0] == "Zeta"
    # FIFO (oldest first) vs recently-added (newest first)
    assert [s["title"] for s in db.query_wishlist("show", sort="oldest")["items"]] == ["Alpha", "Zeta"]
    assert [s["title"] for s in db.query_wishlist("show", sort="added")["items"]] == ["Zeta", "Alpha"]
    # A–Z
    az = [s["title"] for s in db.query_wishlist("show", sort="title")["items"]]
    assert az == ["Alpha", "Zeta"]
    # movies A–Z
    db.add_movie_to_wishlist(10, "Banana"); db.add_movie_to_wishlist(11, "Apple")
    m = [x["title"] for x in db.query_wishlist("movie", sort="title")["items"]]
    assert m == ["Apple", "Banana"]


def test_wishlist_episode_still_roundtrips(db):
    db.add_episodes_to_wishlist(1396, "BB", [
        {"season_number": 1, "episode_number": 1, "title": "Pilot", "still_url": "https://img/e1.jpg"},
        {"season_number": 1, "episode_number": 2}])              # no still
    eps = db.query_wishlist("show")["items"][0]["seasons"][0]["episodes"]
    by = {e["episode_number"]: e for e in eps}
    assert by[1]["still_url"] == "https://img/e1.jpg" and by[2]["still_url"] is None


def test_wishlist_art_backfill_targets_and_set(db):
    db.add_episodes_to_wishlist(1396, "BB", [
        {"season_number": 1, "episode_number": 1, "still_url": "/have.jpg", "season_poster_url": "/s1.jpg"},
        {"season_number": 1, "episode_number": 2},
        {"season_number": 2, "episode_number": 1}])
    tset = {(t["tmdb_id"], t["season_number"]) for t in db.wishlist_art_backfill_targets()}
    assert (1396, 1) in tset and (1396, 2) in tset           # S1 missing a still, S2 missing both
    assert db.set_wishlist_still(1396, 1, 2, "/new.jpg") is True
    assert db.set_wishlist_still(1396, 1, 1, "/x.jpg") is False   # won't clobber an existing still
    assert db.set_wishlist_season_poster(1396, 2, "/s2.jpg") == 1   # fills the season's episodes
    by_season = {s["season_number"]: s for s in db.query_wishlist("show")["items"][0]["seasons"]}
    assert by_season[1]["poster_url"] == "/s1.jpg" and by_season[2]["poster_url"] == "/s2.jpg"


def test_wishlist_episode_overview_roundtrips_and_backfills(db):
    db.add_episodes_to_wishlist(1396, "BB", [
        {"season_number": 1, "episode_number": 1, "overview": "The one where it begins."},
        {"season_number": 1, "episode_number": 2}])
    eps = {e["episode_number"]: e for e in db.query_wishlist("show")["items"][0]["seasons"][0]["episodes"]}
    assert eps[1]["overview"] == "The one where it begins." and eps[2]["overview"] is None
    # episode missing an overview shows up as a backfill target
    assert (1396, 1) in {(t["tmdb_id"], t["season_number"]) for t in db.wishlist_art_backfill_targets()}
    assert db.set_wishlist_episode_overview(1396, 1, 2, "Filled in.") is True
    assert db.set_wishlist_episode_overview(1396, 1, 1, "nope") is False   # won't clobber


# ── YouTube channels (bridged onto watchlist/wishlist) ───────────────────────

from database.video_database import youtube_surrogate_id


def test_youtube_surrogate_id_is_stable_and_distinct():
    a = youtube_surrogate_id("UCPlayStation")
    assert a == youtube_surrogate_id("UCPlayStation")     # deterministic
    assert a > 0 and a < (1 << 63)                        # fits SQLite INTEGER
    assert a != youtube_surrogate_id("UCGoodMythical")    # distinct ids → distinct


def test_follow_channel_lists_and_hydrates(db):
    ch = {"youtube_id": "UCPlay", "title": "PlayStation", "avatar_url": "http://a/p.jpg"}
    assert db.add_channel_to_watchlist(ch) is True
    chans = db.list_watchlist_channels()
    assert len(chans) == 1
    assert chans[0]["youtube_id"] == "UCPlay" and chans[0]["title"] == "PlayStation"
    assert chans[0]["poster_url"] == "http://a/p.jpg" and chans[0]["video_count"] == 0
    # hydration
    assert db.channel_watch_state(["UCPlay", "UCnope"]) == {"UCPlay": True}
    # idempotent re-follow refreshes title, no duplicate
    db.add_channel_to_watchlist({"youtube_id": "UCPlay", "title": "PlayStation US"})
    chans = db.list_watchlist_channels()
    assert len(chans) == 1 and chans[0]["title"] == "PlayStation US"


def test_unfollow_channel_removes_row(db):
    db.add_channel_to_watchlist({"youtube_id": "UCPlay", "title": "PlayStation"})
    assert db.remove_channel_from_watchlist("UCPlay") is True
    assert db.list_watchlist_channels() == []
    assert db.channel_watch_state(["UCPlay"]) == {}


def test_youtube_wishlist_nebula_shape_year_as_season(db):
    """Channel = show, YEAR = season, video = episode — exact TV-nebula shape."""
    ch = {"youtube_id": "UCPlay", "title": "PlayStation", "avatar_url": "http://a/p.jpg"}
    vids = [
        {"youtube_id": "v1", "title": "Old Trailer", "published_at": "2023-01-01",
         "thumbnail_url": "http://t/1.jpg", "description": "older"},
        {"youtube_id": "v2", "title": "New State of Play", "published_at": "2024-06-01",
         "thumbnail_url": "http://t/2.jpg", "description": "newer"},
        {"youtube_id": "v2b", "title": "Mid 2024", "published_at": "2024-02-01", "thumbnail_url": "http://t/2b.jpg"},
        {"youtube_id": "v3", "title": "Undated", "thumbnail_url": "http://t/3.jpg"},
    ]
    assert db.add_videos_to_wishlist(ch, vids) == 4
    res = db.query_youtube_wishlist()
    assert res["pagination"]["total_count"] == 1
    grp = res["items"][0]
    assert grp["kind"] == "channel" and grp["source"] == "youtube"
    assert grp["youtube_id"] == "UCPlay" and grp["title"] == "PlayStation"
    assert grp["poster_url"] == "http://a/p.jpg" and grp["wanted"] == 4
    assert isinstance(grp["tmdb_id"], int)            # surrogate the nebula keys on
    # seasons = years, newest first: 2024, 2023, then 0 (undated)
    assert [se["season_number"] for se in grp["seasons"]] == [2024, 2023, 0]
    y2024 = grp["seasons"][0]
    # newest video in the year is episode 1; its still seeds the season poster
    assert [e["title"] for e in y2024["episodes"]] == ["New State of Play", "Mid 2024"]
    assert [e["episode_number"] for e in y2024["episodes"]] == [1, 2]
    assert y2024["poster_url"] == "http://t/2.jpg"
    e1 = y2024["episodes"][0]
    assert e1["source_id"] == "v2" and e1["overview"] == "newer" and e1["air_date"] == "2024-06-01"


def test_add_videos_is_idempotent_per_video(db):
    ch = {"youtube_id": "UCPlay", "title": "PlayStation"}
    db.add_videos_to_wishlist(ch, [{"youtube_id": "v1", "title": "A", "published_at": "2024-01-01"}])
    db.add_videos_to_wishlist(ch, [{"youtube_id": "v1", "title": "A (updated)", "published_at": "2024-01-01"},
                                   {"youtube_id": "v2", "title": "B", "published_at": "2024-02-01"}])
    grp = db.query_youtube_wishlist()["items"][0]
    assert grp["wanted"] == 2
    titles = {}
    for se in grp["seasons"]:
        for e in se["episodes"]:
            titles[e["source_id"]] = e["title"]
    assert titles == {"v1": "A (updated)", "v2": "B"}


def test_youtube_video_wish_state_hydrates(db):
    db.add_videos_to_wishlist({"youtube_id": "UCx", "title": "X"},
                              [{"youtube_id": "a", "title": "A"}, {"youtube_id": "b", "title": "B"}])
    assert db.youtube_video_wish_state(["a", "b", "c"]) == {"a", "b"}
    assert db.remove_one_video_from_wishlist("a") == 1
    assert db.youtube_video_wish_state(["a", "b"]) == {"b"}


def test_youtube_counts_and_removal_scopes(db):
    a = {"youtube_id": "UCa", "title": "Chan A"}
    b = {"youtube_id": "UCb", "title": "Chan B"}
    db.add_videos_to_wishlist(a, [{"youtube_id": "a1", "title": "A1"},
                                  {"youtube_id": "a2", "title": "A2"}])
    db.add_videos_to_wishlist(b, [{"youtube_id": "b1", "title": "B1"}])
    assert db.youtube_wishlist_counts() == {"channel": 2, "video": 3}
    # remove one video
    assert db.remove_youtube_from_wishlist("video", "a1") == 1
    assert db.youtube_wishlist_counts() == {"channel": 2, "video": 2}
    # remove a whole channel
    assert db.remove_youtube_from_wishlist("channel", "UCa") == 1   # only a2 left
    assert db.youtube_wishlist_counts() == {"channel": 1, "video": 1}


def test_youtube_rows_do_not_disturb_tmdb_counts(db):
    """The bridge must not leak into the existing movie/episode + show/person counts."""
    db.add_movie_to_wishlist(101, "A Movie", year=2020)
    db.add_episodes_to_wishlist(202, "A Show", [{"season_number": 1, "episode_number": 1}])
    db.add_to_watchlist("show", 303, "Watched Show")
    db.add_channel_to_watchlist({"youtube_id": "UCx", "title": "Chan"})
    db.add_videos_to_wishlist({"youtube_id": "UCx", "title": "Chan"},
                              [{"youtube_id": "x1", "title": "X1"}])
    # existing shapes unchanged
    assert db.wishlist_counts() == {"movie": 1, "show": 1, "episode": 1, "total": 2}
    assert db.watchlist_counts() == {"show": 1, "person": 0, "total": 1}
    # youtube counts live on their own surface
    assert db.youtube_wishlist_counts() == {"channel": 1, "video": 1}
    assert db.list_watchlist_channels()[0]["video_count"] == 1


def test_upgrade_from_pre_source_schema(tmp_path):
    """Regression: an existing pre-bridge DB (no source/source_id/parent_source_id
    columns) must upgrade cleanly. The source_id partial indexes can't live in the
    schema executescript (it runs BEFORE the column ALTERs), or init blows up with
    'no such column: source_id' and the whole video DB fails to initialize."""
    path = str(tmp_path / "video_library.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE video_watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, tmdb_id INTEGER NOT NULL,
            title TEXT NOT NULL, poster_url TEXT, library_id INTEGER,
            state TEXT NOT NULL DEFAULT 'follow', date_added TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(kind, tmdb_id));
        CREATE TABLE video_wishlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, tmdb_id INTEGER NOT NULL,
            title TEXT NOT NULL, poster_url TEXT, year INTEGER, season_number INTEGER,
            episode_number INTEGER, episode_title TEXT, still_url TEXT, episode_overview TEXT,
            season_poster_url TEXT, air_date TEXT, status TEXT NOT NULL DEFAULT 'wanted',
            library_id INTEGER, server_source TEXT, date_added TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        """)
    conn.commit()
    conn.close()

    db = VideoDatabase(database_path=path)   # must upgrade in place, no raise
    with db.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(video_wishlist)")}
        assert {"source", "source_id", "parent_source_id"} <= cols
        wcols = {r[1] for r in c.execute("PRAGMA table_info(video_watchlist)")}
        assert {"source", "source_id"} <= wcols
        idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_video_wishlist_video" in idx and "idx_video_wishlist_channel" in idx
    # the youtube path actually works on the upgraded DB
    assert db.add_videos_to_wishlist({"youtube_id": "UCx", "title": "Chan"},
                                     [{"youtube_id": "x1", "title": "X1"}]) == 1
    assert db.youtube_wishlist_counts() == {"channel": 1, "video": 1}


def test_set_wishlist_channel_poster_backfills_avatar(db):
    # videos added without an avatar (flat listing omitted it) → poster_url null
    db.add_videos_to_wishlist({"youtube_id": "UCx", "title": "X"},
                              [{"youtube_id": "a", "title": "A", "published_at": "2024-01-01"},
                               {"youtube_id": "b", "title": "B", "published_at": "2024-02-01"}])
    grp = db.query_youtube_wishlist()["items"][0]
    assert not grp["poster_url"]
    # backfilling the resolved avatar fills every row → the orb gets its poster
    assert db.set_wishlist_channel_poster("UCx", "http://yt/avatar.jpg") == 2
    grp = db.query_youtube_wishlist()["items"][0]
    assert grp["poster_url"] == "http://yt/avatar.jpg"


def test_video_date_cache_roundtrip(db):
    assert db.cache_video_dates([{"youtube_id": "a", "published_at": "2024-06-01"},
                                 {"youtube_id": "b", "published_at": "2023-01-01"},
                                 {"youtube_id": "c", "published_at": ""}]) == 2   # blank skipped
    assert db.get_video_dates(["a", "b", "c", "d"]) == {"a": "2024-06-01", "b": "2023-01-01"}
    # upsert refreshes
    db.cache_video_dates([{"youtube_id": "a", "published_at": "2025-12-31"}])
    assert db.get_video_dates(["a"]) == {"a": "2025-12-31"}


def test_channel_enrichment_tracking(db):
    db.add_videos_to_wishlist({"youtube_id": "UCx", "title": "X"},
                              [{"youtube_id": "a", "title": "A"}, {"youtube_id": "b", "title": "B"}])
    assert set(db.wishlisted_video_ids_for_channel("UCx")) == {"a", "b"}
    assert db.channel_dates_enriched_recently("UCx") is False
    # A THIN run (few dates → proxies were down) is still 'recent' briefly, but
    # retries soon (15-min window) regardless of the full within_hours param.
    db.mark_channel_dates_enriched("UCx", date_count=2)
    assert db.channel_dates_enriched_recently("UCx") is True
    # A GOOD run (>=15 dates) honours the full window param (so within_hours=0 → not recent).
    db.mark_channel_dates_enriched("UCx", date_count=50)
    assert db.channel_dates_enriched_recently("UCx", within_hours=24) is True
    assert db.channel_dates_enriched_recently("UCx", within_hours=0) is False   # window respected


def test_legacy_enrichment_rows_upgrade(db):
    # A pre-InnerTube row (method NULL) with otherwise-good coverage must NOT be
    # locked for 24h — it re-enriches once so it upgrades to the full catalog.
    db.mark_channel_dates_enriched("UCleg", date_count=50, method=None)
    assert db.channel_dates_enriched_recently("UCleg") is False
    # After the InnerTube re-run, the normal window applies again (no churn).
    db.mark_channel_dates_enriched("UCleg", date_count=240, method="innertube")
    assert db.channel_dates_enriched_recently("UCleg") is True


def test_remembered_channel_videos_and_meta(db):
    # Cache a list (out of date order) + a date for one of them.
    db.cache_video_dates([{"youtube_id": "b", "published_at": "2020-05-01"}])
    db.cache_channel_videos("UCc", [
        {"youtube_id": "a", "title": "A", "thumbnail_url": "ta"},          # undated → sorts last
        {"youtube_id": "b", "title": "B", "thumbnail_url": "tb"}])
    got = db.get_channel_videos("UCc")
    assert [v["youtube_id"] for v in got] == ["b", "a"]                    # dated first, undated last
    assert got[0]["published_at"] == "2020-05-01" and got[1]["published_at"] is None
    assert got[0]["title"] == "B" and got[0]["thumbnail_url"] == "tb"
    # Upsert refreshes fields without dropping the row; COALESCE keeps a non-null title.
    db.cache_channel_videos("UCc", [{"youtube_id": "a", "title": "A2", "thumbnail_url": None}])
    a = next(v for v in db.get_channel_videos("UCc") if v["youtube_id"] == "a")
    assert a["title"] == "A2" and a["thumbnail_url"] == "ta"               # title updated, thumb kept
    assert db.get_channel_videos("UNKNOWN") == []

    # Metadata round-trips with tags decoded.
    assert db.get_channel_meta("UCc") is None
    db.cache_channel_meta("UCc", {"title": "Chan", "handle": "@c", "avatar_url": "av",
                                  "subscriber_count": 1234, "tags": ["x", "y"]})
    m = db.get_channel_meta("UCc")
    assert m["title"] == "Chan" and m["subscriber_count"] == 1234 and m["tags"] == ["x", "y"]
