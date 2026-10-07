"""Per-profile isolation for the video watchlist/wishlist.

The video watchlist/wishlist were global — a non-admin saw the admin's lists —
while the music side is per-profile. These tests pin the separation: rows carry
a profile_id, reads/writes scope to it, the drain still feeds the shared
library (deduped), and the v49 migration preserves existing rows on profile 1.
"""
import sqlite3

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(str(tmp_path / "v.db"))
    yield d


def test_watchlist_isolated_per_profile(db):
    assert db.add_to_watchlist("show", 1, "Show One", profile_id=1)
    assert db.add_to_watchlist("show", 1, "Show One", profile_id=2)
    assert db.add_to_watchlist("show", 2, "Show Two", profile_id=2)

    p1 = db.list_watchlist("show", profile_id=1)
    p2 = db.list_watchlist("show", profile_id=2)
    assert {r["tmdb_id"] for r in p1} == {1}
    assert {r["tmdb_id"] for r in p2} == {1, 2}

    # counts follow the same scoping
    assert db.watchlist_counts(profile_id=1)["show"] >= 1
    st = db.watchlist_state("show", [1, 2], profile_id=1)
    assert st.get(1) is True
    assert 2 not in st


def test_watchlist_remove_only_touches_own_profile(db):
    db.add_to_watchlist("show", 9, "Show Nine", profile_id=1)
    db.add_to_watchlist("show", 9, "Show Nine", profile_id=2)
    assert db.remove_from_watchlist("show", 9, profile_id=2) is True
    # profile 1 still follows it; profile 2 has a mute tombstone, not a follow
    assert db.watchlist_state("show", [9], profile_id=1).get(9) is True
    assert 9 not in db.watchlist_state("show", [9], profile_id=2)


def test_wishlist_isolated_per_profile(db):
    assert db.add_movie_to_wishlist(101, "Movie A", profile_id=1)
    assert db.add_movie_to_wishlist(101, "Movie A", profile_id=2)
    assert db.add_movie_to_wishlist(102, "Movie B", profile_id=2)

    p1 = db.query_wishlist("movie", profile_id=1)["items"]
    p2 = db.query_wishlist("movie", profile_id=2)["items"]
    assert {r["tmdb_id"] for r in p1} == {101}
    assert {r["tmdb_id"] for r in p2} == {101, 102}
    assert db.wishlist_counts(profile_id=1)["movie"] == 1
    assert db.wishlist_counts(profile_id=2)["movie"] == 2

    # removing from one profile leaves the other's row alone
    assert db.remove_from_wishlist("movie", tmdb_id=101, profile_id=1) == 1
    assert {r["tmdb_id"] for r in db.query_wishlist("movie", profile_id=2)["items"]} == {101, 102}
    # profile_id=None removes across all profiles (the download-landed path)
    assert db.remove_from_wishlist("movie", tmdb_id=101, profile_id=None) == 1
    assert db.query_wishlist("movie", profile_id=2)["items"] == [
        r for r in db.query_wishlist("movie", profile_id=2)["items"] if r["tmdb_id"] != 101]


def test_wishlist_episodes_isolated_per_profile(db):
    eps = [{"season_number": 1, "episode_number": 1, "title": "Pilot"}]
    assert db.add_episodes_to_wishlist(201, "Show", eps, profile_id=1) == 1
    assert db.add_episodes_to_wishlist(201, "Show", eps, profile_id=2) == 1
    p1 = db.query_wishlist("show", profile_id=1)["items"]
    p2 = db.query_wishlist("show", profile_id=2)["items"]
    assert len(p1) == 1 and len(p2) == 1
    # each profile sees only their own episode rows inside the show
    assert len(p1[0]["seasons"][0]["episodes"]) == 1
    assert len(p2[0]["seasons"][0]["episodes"]) == 1


def test_query_wishlist_mute_badge_is_per_profile(db):
    # profile 2 mutes the show; profile 1 must not inherit the muted badge
    db.add_episodes_to_wishlist(501, "Show", [{"season_number": 1, "episode_number": 1}],
                                profile_id=1)
    db.add_episodes_to_wishlist(501, "Show", [{"season_number": 1, "episode_number": 1}],
                                profile_id=2)
    assert db.remove_from_watchlist("show", 501, profile_id=2) is True
    p1 = db.query_wishlist("show", profile_id=1)["items"]
    p2 = db.query_wishlist("show", profile_id=2)["items"]
    assert p1[0]["muted"] is False
    assert p2[0]["muted"] is True


def test_drain_dedupes_across_profiles(db):
    # same movie wished by two profiles → the drain (shared library) sees one row
    db.add_movie_to_wishlist(301, "Dupe Movie", year=2024, profile_id=1)
    db.add_movie_to_wishlist(301, "Dupe Movie", year=2024, profile_id=2)
    rows = db.movie_wishlist_to_download(due_only=False)
    assert [r["tmdb_id"] for r in rows].count(301) == 1
    # ...but scoped to one profile it still returns that profile's row
    rows = db.movie_wishlist_to_download(due_only=False, profile_id=2)
    assert [r["tmdb_id"] for r in rows] == [301]
    rows = db.movie_wishlist_to_download(due_only=False, profile_id=1)
    assert [r["tmdb_id"] for r in rows] == [301]


def test_drain_episodes_dedupes_across_profiles(db):
    eps = [{"season_number": 2, "episode_number": 3, "title": "Ep"}]
    db.add_episodes_to_wishlist(401, "Show", eps, profile_id=1)
    db.add_episodes_to_wishlist(401, "Show", eps, profile_id=2)
    rows = db.episode_wishlist_to_download(due_only=False)
    keys = [(r.get("show_tmdb_id"), r.get("season_number"), r.get("episode_number")) for r in rows]
    assert keys.count((401, 2, 3)) == 1


def test_youtube_wishlist_isolated_per_profile(db):
    ch = {"youtube_id": "UC1", "title": "Chan"}
    vids = [{"youtube_id": "v1", "title": "Vid 1"}]
    assert db.add_channel_to_watchlist(ch, profile_id=1) is True
    assert db.add_videos_to_wishlist(ch, vids, profile_id=1) == 1
    assert db.add_videos_to_wishlist(ch, vids, profile_id=2) == 1
    assert db.list_watchlist_channels(profile_id=1) != []
    assert db.list_watchlist_channels(profile_id=2) == []
    assert db.channel_watch_state(["UC1"], profile_id=2) == {}
    assert db.youtube_video_wish_state(["v1"], profile_id=1) == {"v1"}
    assert db.youtube_video_wish_state(["v1"], profile_id=2) == {"v1"}
    # drain dedupes the doubly-wished video
    rows = db.youtube_wishlist_to_download()
    assert [r["video_id"] for r in rows].count("v1") == 1


def test_migration_v49_preserves_rows_on_admin_profile(tmp_path):
    """An old-schema DB (no profile_id, global uniques) migrates: rows land on
    profile 1 and the new per-profile uniques accept a second profile's copy."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE video_watchlist (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,"
        " tmdb_id INTEGER NOT NULL, title TEXT NOT NULL, poster_url TEXT, library_id INTEGER,"
        " source TEXT NOT NULL DEFAULT 'tmdb', source_id TEXT,"
        " state TEXT NOT NULL DEFAULT 'follow', date_added TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " UNIQUE(kind, tmdb_id))")
    conn.execute(
        "CREATE TABLE video_wishlist (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,"
        " tmdb_id INTEGER NOT NULL, title TEXT NOT NULL, poster_url TEXT, year INTEGER,"
        " season_number INTEGER, episode_number INTEGER, episode_title TEXT, still_url TEXT,"
        " episode_overview TEXT, season_poster_url TEXT, air_date TEXT,"
        " status TEXT NOT NULL DEFAULT 'wanted', library_id INTEGER, server_source TEXT,"
        " source TEXT NOT NULL DEFAULT 'tmdb', source_id TEXT, parent_source_id TEXT,"
        " date_added TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE UNIQUE INDEX idx_video_wishlist_movie ON video_wishlist(tmdb_id) WHERE kind='movie'")
    conn.execute(
        "CREATE UNIQUE INDEX idx_video_wishlist_episode ON "
        "video_wishlist(tmdb_id, season_number, episode_number) WHERE kind='episode'")
    conn.execute("INSERT INTO video_watchlist (kind, tmdb_id, title) VALUES ('show', 7, 'Old Show')")
    conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title) VALUES ('movie', 77, 'Old Movie')")
    conn.execute("PRAGMA user_version = 48")
    conn.commit()
    conn.close()

    db = VideoDatabase(path)
    # rows survived, stamped to the admin profile
    assert db.watchlist_state("show", [7], profile_id=1).get(7) is True
    assert db.watchlist_state("show", [7], profile_id=2) == {}
    assert {r["tmdb_id"] for r in db.query_wishlist("movie", profile_id=1)["items"]} == {77}
    # per-profile uniques now allow the same item on another profile
    assert db.add_to_watchlist("show", 7, "Old Show", profile_id=2) is True
    assert db.add_movie_to_wishlist(77, "Old Movie", profile_id=2) is True
    # and still forbid dupes within one profile
    assert db.add_movie_to_wishlist(77, "Old Movie", profile_id=1) is True  # upsert, no dup
    assert db.wishlist_counts(profile_id=1)["movie"] == 1


def test_channel_library_followed_is_per_profile(db):
    # admin follows a channel; a non-admin's Library -> Channels tab must not
    # show it as followed (the exact reported bug)
    assert db.add_channel_to_watchlist({"youtube_id": "UC9", "title": "Admin Chan"},
                                       profile_id=1) is True
    p1 = db.query_channel_library(profile_id=1)["items"]
    p2 = db.query_channel_library(profile_id=2)["items"]
    assert any(r["id"] == "UC9" and r["followed"] for r in p1)
    assert not any(r["id"] == "UC9" for r in p2)
