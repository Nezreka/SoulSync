"""Profile-scoped monitoring derivation: one user profile's watchlist/wishlist
must not leak into another profile's Library v2 view."""

from __future__ import annotations

import json

import pytest

from core.library2.importer import (
    apply_monitoring_from_watchlist_wishlist,
    import_legacy_library,
)


def _seed_legacy_monitor_tables(conn):
    cur = conn.cursor()
    cur.execute("""CREATE TABLE watchlist_artists(
        id INTEGER PRIMARY KEY, artist_name TEXT, spotify_artist_id TEXT,
        musicbrainz_artist_id TEXT, profile_id INTEGER DEFAULT 1)""")
    cur.execute("""CREATE TABLE wishlist_tracks(
        id INTEGER PRIMARY KEY, spotify_track_id TEXT, spotify_data TEXT,
        source_type TEXT, date_added TEXT, profile_id INTEGER DEFAULT 1)""")
    # Profile 1 watches Drake; profile 2 watches Adele.
    cur.execute("INSERT INTO watchlist_artists(artist_name, spotify_artist_id, profile_id) "
                "VALUES('Drake', 'sp-drake', 1)")
    cur.execute("INSERT INTO watchlist_artists(artist_name, spotify_artist_id, profile_id) "
                "VALUES('Adele', 'sp-adele', 2)")
    # Profile 1 wants track sp-t1; profile 2 wants sp-t2.
    cur.execute("INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, profile_id) "
                "VALUES('sp-t1', ?, 1)", (json.dumps({"id": "sp-t1", "name": "T1"}),))
    cur.execute("INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, profile_id) "
                "VALUES('sp-t2', ?, 2)", (json.dumps({"id": "sp-t2", "name": "T2"}),))
    conn.commit()


def _seed_lib2(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name, spotify_id) VALUES('Drake', 'sp-drake')")
    drake = cur.lastrowid
    cur.execute("INSERT INTO lib2_artists(name, spotify_id) VALUES('Adele', 'sp-adele')")
    adele = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'A')", (drake,))
    album = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title, spotify_id, monitored) "
                "VALUES(?, 'T1', 'sp-t1', 0)", (album,))
    cur.execute("INSERT INTO lib2_tracks(album_id, title, spotify_id, monitored) "
                "VALUES(?, 'T2', 'sp-t2', 0)", (album,))
    conn.commit()
    return drake, adele


def test_profile_scope_filters_monitoring(imported_conn):
    conn = imported_conn
    _seed_legacy_monitor_tables(conn)
    drake, adele = _seed_lib2(conn)
    cur = conn.cursor()

    apply_monitoring_from_watchlist_wishlist(cur, profile_id=1)
    conn.commit()

    monitored = {r["name"]: r["monitored"] for r in conn.execute(
        "SELECT name, monitored FROM lib2_artists WHERE id IN (?,?)", (drake, adele))}
    assert monitored["Drake"] == 1
    assert monitored["Adele"] == 0  # profile 2's watchlist must not leak

    tracks = {r["spotify_id"]: r["monitored"] for r in conn.execute(
        "SELECT spotify_id, monitored FROM lib2_tracks WHERE spotify_id IN ('sp-t1','sp-t2')")}
    assert tracks["sp-t1"] == 1
    assert tracks["sp-t2"] == 0  # profile 2's wishlist must not leak


def test_no_profile_defaults_to_admin(imported_conn):
    conn = imported_conn
    _seed_legacy_monitor_tables(conn)
    drake, adele = _seed_lib2(conn)
    cur = conn.cursor()

    apply_monitoring_from_watchlist_wishlist(cur, profile_id=None)
    conn.commit()

    monitored = {r["name"]: r["monitored"] for r in conn.execute(
        "SELECT name, monitored FROM lib2_artists WHERE id IN (?,?)", (drake, adele))}
    assert monitored["Drake"] == 1
    assert monitored["Adele"] == 0


def test_monitoring_helper_rejects_nonadmin_profile(imported_conn):
    conn = imported_conn
    _seed_legacy_monitor_tables(conn)

    with pytest.raises(ValueError, match="admin-only"):
        apply_monitoring_from_watchlist_wishlist(conn.cursor(), profile_id=2)


def test_import_without_profile_uses_admin_only(legacy_db):
    conn = legacy_db._get_connection()
    conn.execute(
        "INSERT INTO artists(id, name, spotify_artist_id) "
        "VALUES(2, 'Adele', 'sp-adele')")
    conn.execute("""CREATE TABLE watchlist_artists(
        id INTEGER PRIMARY KEY, artist_name TEXT, spotify_artist_id TEXT,
        musicbrainz_artist_id TEXT, profile_id INTEGER DEFAULT 1)""")
    conn.execute(
        "INSERT INTO watchlist_artists(artist_name, spotify_artist_id, profile_id) "
        "VALUES('Drake', 'sp1', 1)")
    conn.execute(
        "INSERT INTO watchlist_artists(artist_name, spotify_artist_id, profile_id) "
        "VALUES('Adele', 'sp-adele', 2)")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)

    conn = legacy_db._get_connection()
    monitored = {
        row["name"]: row["monitored"]
        for row in conn.execute(
            "SELECT name, monitored FROM lib2_artists WHERE name IN ('Drake', 'Adele')")
    }
    conn.close()
    assert monitored == {"Drake": 1, "Adele": 0}


# ---------------------------------------------------------------------------
# L2-006: the watchlist's own quality profile is part of the intent
# ---------------------------------------------------------------------------


def _seed_profiles(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS quality_profiles(
        id INTEGER PRIMARY KEY, name TEXT, is_default INTEGER DEFAULT 0)""")
    conn.execute("INSERT OR IGNORE INTO quality_profiles(id,name,is_default) "
                 "VALUES(1,'Standard',1)")
    conn.execute("INSERT OR IGNORE INTO quality_profiles(id,name,is_default) "
                 "VALUES(2,'Lossless',0)")
    conn.commit()


def _watchlist_with_profile(conn, profile_id):
    conn.execute("""CREATE TABLE watchlist_artists(
        id INTEGER PRIMARY KEY, artist_name TEXT, spotify_artist_id TEXT,
        quality_profile_id INTEGER, profile_id INTEGER DEFAULT 1)""")
    conn.execute(
        "INSERT INTO watchlist_artists(artist_name, spotify_artist_id, "
        "quality_profile_id, profile_id) VALUES('Drake','sp-drake',?,1)",
        (profile_id,))
    conn.commit()


def _drake(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name, spotify_id, quality_profile_id, "
                "quality_profile_explicit) VALUES('Drake','sp-drake',1,0)")
    artist = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, "
                "quality_profile_id, quality_profile_explicit) VALUES(?,'A',1,0)",
                (artist,))
    conn.commit()
    return artist


def test_watchlist_quality_profile_survives_the_import(imported_conn):
    """The user picked profile 2 for this watched artist before the upgrade;
    importing only ``monitored=1`` dropped them back to the global default, so
    every future release was fetched at the wrong quality."""
    conn = imported_conn
    _seed_profiles(conn)
    _watchlist_with_profile(conn, 2)
    artist = _drake(conn)

    apply_monitoring_from_watchlist_wishlist(conn.cursor(), profile_id=1)
    conn.commit()

    row = conn.execute(
        "SELECT monitored, quality_profile_id, quality_profile_explicit "
        "FROM lib2_artists WHERE id=?", (artist,)).fetchone()
    assert row["monitored"] == 1
    assert row["quality_profile_id"] == 2
    assert row["quality_profile_explicit"] == 1
    # …and the inheritance projection reached the artist's albums, which is
    # what a newly materialised discography release copies from.
    assert conn.execute(
        "SELECT quality_profile_id FROM lib2_albums WHERE primary_artist_id=?",
        (artist,)).fetchone()[0] == 2


def test_a_dangling_watchlist_profile_falls_back_to_the_default(imported_conn):
    conn = imported_conn
    _seed_profiles(conn)
    _watchlist_with_profile(conn, 99)
    artist = _drake(conn)

    apply_monitoring_from_watchlist_wishlist(conn.cursor(), profile_id=1)
    conn.commit()

    row = conn.execute(
        "SELECT monitored, quality_profile_id, quality_profile_explicit "
        "FROM lib2_artists WHERE id=?", (artist,)).fetchone()
    assert row["monitored"] == 1
    assert row["quality_profile_explicit"] == 0


def test_watchlist_rows_that_disagree_keep_the_default(imported_conn):
    conn = imported_conn
    _seed_profiles(conn)
    _watchlist_with_profile(conn, 2)
    conn.execute(
        "INSERT INTO watchlist_artists(artist_name, spotify_artist_id, "
        "quality_profile_id, profile_id) VALUES('Drake','sp-drake',1,1)")
    conn.commit()
    artist = _drake(conn)

    apply_monitoring_from_watchlist_wishlist(conn.cursor(), profile_id=1)
    conn.commit()

    row = conn.execute(
        "SELECT monitored, quality_profile_explicit FROM lib2_artists WHERE id=?",
        (artist,)).fetchone()
    assert row["monitored"] == 1
    assert row["quality_profile_explicit"] == 0
