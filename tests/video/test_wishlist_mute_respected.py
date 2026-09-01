"""Un-following a show has to reach the wishlist drain.

`remove_from_watchlist` writes a 'mute' tombstone, and the airing scan already
refuses to ADD episodes for a muted show. Episodes wished BEFORE the mute kept
being searched every hour anyway - Boulder's install has a muted Jimmy Kimmel
Live! with three episodes still sitting in the wishlist. Un-following something
and having it keep costing searches is the opposite of what the user asked for.

They stay ON the wishlist (removing them is a decision the user didn't make);
they just stop being hunted, and the row says so.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


def _wish_episode(db, tmdb_id, season, episode, title="Show"):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO video_wishlist (kind, tmdb_id, title, season_number, episode_number, "
            "status) VALUES ('episode', ?, ?, ?, ?, 'wanted')", (tmdb_id, title, season, episode))
        conn.commit()
    finally:
        conn.close()


def test_a_muted_shows_episodes_are_not_searched(db):
    _wish_episode(db, 1, 1, 1, "Followed Show")
    _wish_episode(db, 2, 7, 16, "Jimmy Kimmel Live!")
    db.remove_from_watchlist("show", 2)          # un-follow → mute tombstone

    ids = {e["show_tmdb_id"] for e in db.episode_wishlist_to_download()}
    assert ids == {1}, "a muted show's episodes are still being hunted"


def test_they_stay_on_the_wishlist(db):
    """Not searching is not the same as deleting. The user removed the show from
    their watchlist, not these episodes from their wishlist."""
    _wish_episode(db, 2, 7, 16, "Jimmy Kimmel Live!")
    db.remove_from_watchlist("show", 2)

    shows = db.query_wishlist(kind="show")["items"]
    assert [s["tmdb_id"] for s in shows] == [2]
    assert shows[0]["muted"] is True, "the row has to say why it stopped searching"


def test_a_followed_show_is_not_marked_muted(db):
    _wish_episode(db, 1, 1, 1, "Followed Show")
    assert db.query_wishlist(kind="show")["items"][0]["muted"] is False


def test_re_following_puts_it_back_in_the_drain(db):
    _wish_episode(db, 2, 7, 16, "Jimmy Kimmel Live!")
    db.remove_from_watchlist("show", 2)
    assert db.episode_wishlist_to_download() == []

    db.add_to_watchlist("show", 2, "Jimmy Kimmel Live!")
    assert {e["show_tmdb_id"] for e in db.episode_wishlist_to_download()} == {2}


def test_a_user_search_still_covers_a_muted_show(db):
    """due_only=False is 'search everything now'. Mute is about the automatic
    tick — an explicit search should still be able to reach it."""
    _wish_episode(db, 2, 7, 16, "Jimmy Kimmel Live!")
    db.remove_from_watchlist("show", 2)
    assert db.episode_wishlist_to_download(due_only=False) == []
