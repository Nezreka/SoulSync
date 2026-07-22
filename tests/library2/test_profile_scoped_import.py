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
