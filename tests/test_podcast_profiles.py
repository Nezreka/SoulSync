"""The podcast watchlist belongs to a profile, and every query has to say so.

Podcasts shipped before the profile lessons the audiobook side was built with.
The rows always carried a profile_id and get_watchlist_podcasts always filtered
on it, but every other query matched on feed_url alone, and the table's unique
key was feed_url on its own - so a second person following a show they both
like got a success, no row, and an empty watchlist.

These pin the whole set: the schema, each scoped query, and the timed scan
sweeping everybody rather than profile 1.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from database.music_database import MusicDatabase

FEED = "https://feeds.example.com/shared-show"


@pytest.fixture
def tmp_db(tmp_path):
    return MusicDatabase(str(tmp_path / "test_music.db"))


def _follow(db, profile_id, feed=FEED, title="Shared Show", **kw):
    return db.add_watchlist_podcast(feed_url=feed, title=title,
                                    profile_id=profile_id, **kw)


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def test_the_unique_key_is_the_pair_not_the_feed(tmp_db):
    sql = sqlite3.connect(tmp_db.database_path).execute(
        "SELECT sql FROM sqlite_master WHERE name='watchlist_podcasts'"
    ).fetchone()[0]
    assert "UNIQUE(feed_url, profile_id)" in sql


def test_two_profiles_can_follow_the_same_show(tmp_db):
    assert _follow(tmp_db, 1) is True
    assert _follow(tmp_db, 2) is True
    assert [p["feed_url"] for p in tmp_db.get_watchlist_podcasts(profile_id=1)] == [FEED]
    assert [p["feed_url"] for p in tmp_db.get_watchlist_podcasts(profile_id=2)] == [FEED]


def test_following_twice_on_one_profile_is_still_idempotent(tmp_db):
    _follow(tmp_db, 1)
    _follow(tmp_db, 1, title="Renamed")
    rows = tmp_db.get_watchlist_podcasts(profile_id=1)
    assert len(rows) == 1
    assert rows[0]["title"] == "Renamed"


def test_a_database_with_the_old_shape_migrates_without_losing_rows(tmp_path):
    """The rebuild has to be exact: this is somebody's live subscriptions."""
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE watchlist_podcasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, feed_url TEXT UNIQUE NOT NULL,
        itunes_id INTEGER, title TEXT NOT NULL, author TEXT, description TEXT,
        artwork_url TEXT, website TEXT, auto_download INTEGER NOT NULL DEFAULT 1,
        retention_days INTEGER NOT NULL DEFAULT 14, episode_count INTEGER,
        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_scan_timestamp TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, profile_id INTEGER DEFAULT 1)""")
    con.execute("INSERT INTO watchlist_podcasts (feed_url,title,profile_id,retention_days) "
                "VALUES (?,?,?,?)", ("https://a.example/rss", "Show A", 1, 30))
    con.execute("INSERT INTO watchlist_podcasts (feed_url,title,profile_id) "
                "VALUES (?,?,?)", ("https://b.example/rss", "Show B", 2))
    con.commit()
    before = con.execute("SELECT id, feed_url, title, profile_id, retention_days "
                         "FROM watchlist_podcasts ORDER BY id").fetchall()
    con.close()

    MusicDatabase(path)

    con = sqlite3.connect(path)
    after = con.execute("SELECT id, feed_url, title, profile_id, retention_days "
                        "FROM watchlist_podcasts ORDER BY id").fetchall()
    assert after == before
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='watchlist_podcasts'").fetchone()[0]
    assert "UNIQUE(feed_url, profile_id)" in sql

    # and it does not run a second time
    MusicDatabase(path)
    assert con.execute("SELECT COUNT(*) FROM watchlist_podcasts").fetchone()[0] == 2


def test_a_half_migrated_shape_is_also_rebuilt(tmp_path):
    """A pair constraint next to the old column UNIQUE still refuses profile 2.

    That shape is reachable by anyone who ran a build between the create being
    fixed and the column UNIQUE being dropped, so detection has to catch it
    rather than see the pair constraint and call the table done.
    """
    path = str(tmp_path / "half.db")
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE watchlist_podcasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, feed_url TEXT UNIQUE NOT NULL,
        itunes_id INTEGER, title TEXT NOT NULL, author TEXT, description TEXT,
        artwork_url TEXT, website TEXT, auto_download INTEGER NOT NULL DEFAULT 1,
        retention_days INTEGER NOT NULL DEFAULT 14, episode_count INTEGER,
        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_scan_timestamp TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, profile_id INTEGER DEFAULT 1,
        UNIQUE(feed_url, profile_id))""")
    con.execute("INSERT INTO watchlist_podcasts (feed_url,title,profile_id) VALUES (?,?,?)",
                (FEED, "Shared Show", 1))
    con.commit()
    con.close()

    db = MusicDatabase(path)
    assert _follow(db, 2) is True
    assert len(db.get_watchlist_podcasts(profile_id=2)) == 1


# ---------------------------------------------------------------------------
# scoped queries
# ---------------------------------------------------------------------------

def test_one_profile_cannot_unfollow_anothers_show(tmp_db):
    _follow(tmp_db, 1)
    _follow(tmp_db, 2)
    tmp_db.remove_watchlist_podcast(feed_url=FEED, profile_id=2)
    assert len(tmp_db.get_watchlist_podcasts(profile_id=1)) == 1
    assert tmp_db.get_watchlist_podcasts(profile_id=2) == []


def test_following_is_answered_for_the_asking_profile(tmp_db):
    _follow(tmp_db, 1)
    assert tmp_db.is_podcast_in_watchlist(feed_url=FEED, profile_id=1) is True
    assert tmp_db.is_podcast_in_watchlist(feed_url=FEED, profile_id=2) is False


def test_the_row_handed_back_is_the_asking_profiles_own(tmp_db):
    _follow(tmp_db, 1, retention_days=30)
    _follow(tmp_db, 2, retention_days=7)
    assert tmp_db.get_watchlist_podcast(feed_url=FEED, profile_id=1)["retention_days"] == 30
    assert tmp_db.get_watchlist_podcast(feed_url=FEED, profile_id=2)["retention_days"] == 7


def test_changing_your_settings_does_not_rewrite_someone_elses(tmp_db):
    _follow(tmp_db, 1, retention_days=30)
    _follow(tmp_db, 2, retention_days=30)
    tmp_db.update_watchlist_podcast_settings(FEED, retention_days=1, profile_id=2)
    assert tmp_db.get_watchlist_podcast(feed_url=FEED, profile_id=1)["retention_days"] == 30
    assert tmp_db.get_watchlist_podcast(feed_url=FEED, profile_id=2)["retention_days"] == 1


def test_leaving_the_profile_out_still_means_any_profile(tmp_db):
    """Backwards compatible: this is what every one of these did before."""
    _follow(tmp_db, 2)
    assert tmp_db.is_podcast_in_watchlist(feed_url=FEED) is True
    assert tmp_db.get_watchlist_podcast(feed_url=FEED) is not None


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------

def test_profiles_with_rows_lists_everyone_who_follows_something(tmp_db):
    _follow(tmp_db, 1, feed="https://a.example/rss", title="A")
    _follow(tmp_db, 3, feed="https://b.example/rss", title="B")
    _follow(tmp_db, 3, feed="https://c.example/rss", title="C")
    assert tmp_db.profiles_with_podcast_rows() == [1, 3]


def test_profiles_with_rows_never_returns_nothing(tmp_db):
    """An empty table still has to give the scan something to iterate."""
    assert tmp_db.profiles_with_podcast_rows() == [1]


def test_a_timed_scan_sweeps_every_profile_not_just_the_first():
    """A system automation runs as profile 1, so the profile has to come from
    the rows. Without this a second person's shows were never scanned at all."""
    from core.podcast_automation import scan_and_auto_download_podcasts

    db = MagicMock()
    db.profiles_with_podcast_rows.return_value = [1, 2, 7]
    db.get_watchlist_podcasts.return_value = []

    with patch("core.podcast_automation._get_db", return_value=db):
        result = scan_and_auto_download_podcasts()

    assert result["profiles_scanned"] == 3
    assert [c.kwargs["profile_id"] for c in db.get_watchlist_podcasts.call_args_list] == [1, 2, 7]


def test_an_explicit_profile_scans_only_that_one():
    """Scan Now is a person asking about their own shows."""
    from core.podcast_automation import scan_and_auto_download_podcasts

    db = MagicMock()
    db.get_watchlist_podcasts.return_value = []

    with patch("core.podcast_automation._get_db", return_value=db):
        scan_and_auto_download_podcasts(profile_id=4)

    db.profiles_with_podcast_rows.assert_not_called()
    assert db.get_watchlist_podcasts.call_args.kwargs["profile_id"] == 4


def test_a_database_that_cannot_list_profiles_still_scans_the_first():
    from core.podcast_automation import scan_and_auto_download_podcasts

    db = MagicMock()
    db.profiles_with_podcast_rows.side_effect = RuntimeError("db is down")
    db.get_watchlist_podcasts.return_value = []

    with patch("core.podcast_automation._get_db", return_value=db):
        result = scan_and_auto_download_podcasts()

    assert result["profiles_scanned"] == 1
