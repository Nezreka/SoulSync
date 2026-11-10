"""Stable provider-less IDs (audit P1-12).

Wishlist mirroring for rows without a Spotify ID used ``lib2-track:<rowid>``,
which breaks across a library reset + reimport (new rowids orphan old
wishlist items; reused rowids mis-assign them). The persisted ``stable_id``
is minted deterministically from the natural identity, so a reimport of the
same library reproduces the same wishlist identity.
"""

from __future__ import annotations

from core.library2.stable_ids import (
    compute_album_stable_id,
    compute_track_stable_id,
    ensure_album_stable_id,
    ensure_track_stable_id,
)
from core.library2.wishlist_mirror import track_wishlist_payload


def _seed_providerless_track(conn) -> tuple[int, int]:
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('NoProvider Artist')")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'NP Album')",
        (artist_id,))
    album_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'NP Track', 3)",
        (album_id,))
    track_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                (track_id, artist_id))
    conn.commit()
    return album_id, track_id


def test_compute_is_deterministic_and_normalized():
    a = compute_album_stable_id("Drake", "Views", "album")
    assert a == compute_album_stable_id("  drake ", "VIEWS", "Album")
    assert a != compute_album_stable_id("Drake", "Views", "single")
    t = compute_track_stable_id(a, "One Dance", 1, 1)
    assert t == compute_track_stable_id(a, "one   dance", "1", "1")
    assert t != compute_track_stable_id(a, "One Dance", 1, 2)


def test_backfill_mints_ids_for_imported_rows(imported_conn):
    conn = imported_conn
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_albums WHERE stable_id IS NULL").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE stable_id IS NULL").fetchone()[0] == 0


def test_stable_id_survives_reset_and_reimport(legacy_db):
    from core.library2.importer import import_legacy_library

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    first = conn.execute(
        "SELECT id, stable_id FROM lib2_tracks WHERE title='Hotline Bling'").fetchone()
    # Reset: lib2 rows go away, the wishlist (not modeled here) would stay.
    conn.execute("DELETE FROM lib2_artists")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    second = conn.execute(
        "SELECT id, stable_id FROM lib2_tracks WHERE title='Hotline Bling'").fetchone()
    conn.close()
    assert second["id"] != first["id"], "reimport must produce a fresh rowid for this test to mean anything"
    assert second["stable_id"] == first["stable_id"]


def test_wishlist_payload_uses_stable_id_not_rowid(imported_conn):
    conn = imported_conn
    album_id, track_id = _seed_providerless_track(conn)
    payload = track_wishlist_payload(conn, track_id)
    assert payload is not None
    stable_track = conn.execute(
        "SELECT stable_id FROM lib2_tracks WHERE id=?", (track_id,)).fetchone()[0]
    stable_album = conn.execute(
        "SELECT stable_id FROM lib2_albums WHERE id=?", (album_id,)).fetchone()[0]
    assert stable_track and stable_album
    assert payload["id"] == f"lib2-track:{stable_track}"
    assert payload["album"]["id"] == f"lib2-album:{stable_album}"
    assert payload["id"] != f"lib2-track:{track_id}", "rowid must no longer be the identity"


def test_spotify_id_still_wins(imported_conn):
    conn = imported_conn
    _album_id, track_id = _seed_providerless_track(conn)
    conn.execute("UPDATE lib2_tracks SET spotify_id='sp-xyz' WHERE id=?", (track_id,))
    conn.commit()
    payload = track_wishlist_payload(conn, track_id)
    assert payload["id"] == "sp-xyz"
    assert payload["provider"] == "spotify"


def test_persisted_id_wins_over_later_metadata_edits(imported_conn):
    conn = imported_conn
    album_id, track_id = _seed_providerless_track(conn)
    minted = ensure_track_stable_id(conn, track_id)
    minted_album = ensure_album_stable_id(conn, album_id)
    conn.execute("UPDATE lib2_tracks SET title='Renamed Track' WHERE id=?", (track_id,))
    conn.execute("UPDATE lib2_albums SET title='Renamed Album' WHERE id=?", (album_id,))
    conn.commit()
    # A metadata correction must not silently change an identity wishlist
    # rows already reference.
    assert ensure_track_stable_id(conn, track_id) == minted
    assert ensure_album_stable_id(conn, album_id) == minted_album


def test_two_same_named_providerless_releases_get_their_own_ids(imported_conn):
    """third #1062 review G09-4: "Greatest Hits" 2004 and 2017 by one artist,
    neither with a provider id, minted one id, and a wish for the 2017 track 1
    could resolve to the owned 2004 copy"""
    conn = imported_conn
    artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Hits Band')").lastrowid
    albums, tracks = [], []
    for year in (2004, 2017):
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, album_type, year, release_date)"
            " VALUES(?, 'Greatest Hits', 'compilation', ?, ?)", (artist, year, f"{year}-01-01")).lastrowid
        albums.append(album)
        tracks.append(conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'Intro', 1)",
            (album,)).lastrowid)
    album_ids = [ensure_album_stable_id(conn, a) for a in albums]
    track_ids = [ensure_track_stable_id(conn, t) for t in tracks]
    assert album_ids[0] != album_ids[1]
    assert track_ids[0] != track_ids[1]
    # the first keeps the plain id, so nothing minted before changes
    assert album_ids[0] == compute_album_stable_id("Hits Band", "Greatest Hits", "compilation")


def test_an_id_two_rows_already_share_resolves_to_neither(imported_conn):
    """G09-4: rows minted before the tiebreak may still share one id; the
    wishlist lookup must not pick whichever sqlite returns first"""
    import json
    import pytest
    from core.library2.materialize import materialize_wishlist_row
    conn = imported_conn
    artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Twin Band')").lastrowid
    for _ in range(2):
        album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Same')",
                             (artist,)).lastrowid
        conn.execute("INSERT INTO lib2_tracks(album_id, title, stable_id) VALUES(?, 'Song', 'shared-sid')",
                     (album,))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wishlist_tracks)")}
    if not cols:
        conn.execute("""CREATE TABLE wishlist_tracks(id INTEGER PRIMARY KEY, spotify_track_id TEXT,
            spotify_data TEXT, source_info TEXT, profile_id INTEGER DEFAULT 1, quality_profile_id INTEGER)""")
    row_id = conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_info, profile_id)"
        " VALUES('lib2-track:shared-sid', ?, '{}', 1)",
        (json.dumps({"id": "lib2-track:shared-sid", "name": "Song",
                     "artists": [{"name": "Twin Band"}], "album": {"name": "Same"}}),)).lastrowid
    with pytest.raises(ValueError, match="multiple"):
        materialize_wishlist_row(conn, row_id)
