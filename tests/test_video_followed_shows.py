"""followed_shows(): explicit show follows + their library status — feeds the
watchlist-prune pass that drops ended/canceled shows."""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_lists_explicit_follows_with_library_status(db):
    # a tmdb-only follow (no library row → status None)
    db.add_to_watchlist("show", 100, "TMDB Only")
    # a follow backed by a library show carrying a status
    conn = db._get_connection()
    conn.execute("INSERT INTO shows (id, server_source, title, tmdb_id, status) "
                 "VALUES (5, 'plex', 'Owned Show', 200, 'Ended')")
    conn.commit(); conn.close()
    db.add_to_watchlist("show", 200, "Owned Show", library_id=5)

    rows = {r["tmdb_id"]: r for r in db.followed_shows()}
    assert rows[100]["status"] is None                 # tmdb-only → no local status
    assert rows[200]["status"] == "Ended"              # owned → carries the status


def test_server_scoped_library_id_resolution(db):
    # a show owned ONLY on jellyfin (the non-default server), followed on the
    # watchlist. the airing automation's calendar pass is plex-scoped, so the
    # unowned-follow pass must resolve library_id for plex too — otherwise the
    # show is skipped by BOTH passes and never wishlisted, silently.
    conn = db._get_connection()
    conn.execute("INSERT INTO shows (id, server_source, title, tmdb_id, status) "
                 "VALUES (7, 'jellyfin', 'Jellyfin Only', 700, 'Returning Series')")
    conn.commit(); conn.close()
    db.add_to_watchlist("show", 700, "Jellyfin Only")

    unscoped = {r["tmdb_id"]: r for r in db.followed_shows()}
    assert unscoped[700]["library_id"] == 7          # old behavior preserved
    jelly = {r["tmdb_id"]: r for r in db.followed_shows(server_source="jellyfin")}
    assert jelly[700]["library_id"] == 7             # owned on jellyfin → claimed
    plex = {r["tmdb_id"]: r for r in db.followed_shows(server_source="plex")}
    assert plex[700]["library_id"] is None          # unowned for plex → TMDB pass


def test_excludes_muted_and_people(db):
    db.add_to_watchlist("show", 1, "A")
    db.remove_from_watchlist("show", 1)                 # mute (tombstone)
    db.add_to_watchlist("person", 2, "Someone")
    assert db.followed_shows() == []                    # neither muted shows nor people
