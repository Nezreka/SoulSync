"""Importer: credit splitting, multi-artist, single-vs-album, idempotency."""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.importer import (
    featured_from_title,
    import_legacy_library,
    split_artist_credits,
)
from core.library2 import queries as Q


# --- credit splitter ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Drake feat. Rihanna", ["Drake", "Rihanna"]),
    ("A ft. B", ["A", "B"]),
    ("A featuring B", ["A", "B"]),
    ("A, B & C", ["A", "B", "C"]),
    ("Calvin Harris x Dua Lipa", ["Calvin Harris", "Dua Lipa"]),
    ("Drake feat. Wizkid & Kyla", ["Drake", "Wizkid", "Kyla"]),
    ("", []),
])
def test_split_artist_credits(raw, expected):
    assert split_artist_credits(raw) == expected


def test_split_dedupes_case_insensitive():
    assert split_artist_credits("Drake, drake & DRAKE") == ["Drake"]


def test_featured_from_title():
    assert featured_from_title("One Dance (feat. Wizkid & Kyla)") == ["Wizkid", "Kyla"]
    assert featured_from_title("Plain Title") == []


# --- full import -------------------------------------------------------------

def _q(conn, sql, *params):
    return conn.execute(sql, params).fetchall()


def test_import_counts(legacy_db):
    stats = import_legacy_library(legacy_db)
    assert stats["artists"] == 1          # only legacy artists (Drake)
    assert stats["albums"] == 2
    assert stats["tracks"] == 3
    assert stats["files"] == 2            # track 101 has no file_path
    assert stats["linked_duplicates"] == 1


def test_album_type_detection(imported_conn):
    rows = {r["title"]: r["album_type"] for r in
            _q(imported_conn, "SELECT title, album_type FROM lib2_albums")}
    assert rows["Views"] == "album"
    assert rows["One Dance"] == "single"   # single-track legacy album


def test_import_prefers_explicit_album_type_over_one_track_heuristic(legacy_db):
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE albums ADD COLUMN album_type TEXT")
    conn.execute("UPDATE albums SET album_type='album' WHERE id=11")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    row = conn.execute(
        "SELECT album_type FROM lib2_albums WHERE legacy_album_id=11"
    ).fetchone()
    conn.close()
    assert row[0] == "album"


def test_multi_artist_split(imported_conn):
    # Track 100 ("One Dance" on Views) credits Drake + featured Wizkid.
    names = [r["name"] for r in _q(
        imported_conn,
        """SELECT ar.name FROM lib2_track_artists ta
           JOIN lib2_artists ar ON ar.id = ta.artist_id
           JOIN lib2_tracks t ON t.id = ta.track_id
           WHERE t.legacy_track_id = 100 ORDER BY ta.position""",
    )]
    assert names == ["Drake", "Wizkid"]
    # Wizkid was created as a new artist (not a legacy mirror row).
    wiz = _q(imported_conn, "SELECT legacy_artist_id FROM lib2_artists WHERE name='Wizkid'")
    assert wiz and wiz[0]["legacy_artist_id"] is None


def test_single_album_linkage(imported_conn):
    # The single's track points its canonical_track_id at the album track.
    single = _q(imported_conn, "SELECT id, canonical_track_id FROM lib2_tracks WHERE legacy_track_id = 102")[0]
    album_track = _q(imported_conn, "SELECT id FROM lib2_tracks WHERE legacy_track_id = 100")[0]
    assert single["canonical_track_id"] == album_track["id"]


def test_idempotent_rerun(legacy_db):
    first = import_legacy_library(legacy_db)
    second = import_legacy_library(legacy_db)
    # Re-run must not duplicate rows.
    conn = sqlite3.connect(legacy_db.path)
    for table, expected in (("lib2_artists", 2), ("lib2_albums", 2), ("lib2_tracks", 3),
                            ("lib2_track_files", 2)):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == expected, f"{table} duplicated on re-run: {count}"
    conn.close()
    assert second["files"] == 0   # nothing new to insert the second time


def test_reset_rebuilds(legacy_db):
    import_legacy_library(legacy_db)
    stats = import_legacy_library(legacy_db, reset=True)
    assert stats["tracks"] == 3
    conn = sqlite3.connect(legacy_db.path)
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 3
    conn.close()


def test_wishlist_only_track_seeds_missing_monitored_library_rows(legacy_db):
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    payload = {
        "id": "sp_track_1",
        "name": "Only Wanted Song",
        "artists": [{"id": "sp_artist_1", "name": "Wishlist Artist"}],
        "album": {
            "id": "sp_album_1",
            "name": "Wishlist Album",
            "album_type": "single",
            "total_tracks": 3,
            "release_date": "2026-01-01",
            "images": [{"url": "http://cover"}],
            "artists": [{"id": "sp_artist_1", "name": "Wishlist Artist"}],
        },
        "track_number": 1,
        "disc_number": 1,
        "duration_ms": 123000,
    }
    conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_type) VALUES(?,?,?)",
        ("sp_track_1", json.dumps(payload), "manual"),
    )
    conn.commit()
    conn.close()

    stats = import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    artist = conn.execute("SELECT * FROM lib2_artists WHERE name='Wishlist Artist'").fetchone()
    album = conn.execute("SELECT * FROM lib2_albums WHERE title='Wishlist Album'").fetchone()
    track = conn.execute("SELECT * FROM lib2_tracks WHERE title='Only Wanted Song'").fetchone()
    file_count = conn.execute(
        "SELECT COUNT(*) FROM lib2_track_files WHERE track_id=?", (track["id"],)
    ).fetchone()[0]
    conn.close()

    assert stats["wishlist_tracks"] == 1
    assert artist["monitored"] == 0
    assert album["monitored"] == 0
    assert album["track_count"] == 1
    assert album["expected_track_count"] == 1
    assert track["monitored"] == 1
    assert file_count == 0

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    detail = Q.get_album(conn, album["id"])
    conn.close()

    assert detail["track_count"] == 1
    assert detail["tracks_missing"] == 1
    assert [t["title"] for t in detail["tracks"]] == ["Only Wanted Song"]
    assert detail["monitored"] is False
    assert detail["tracks"][0]["monitored"] is True


def test_watchlist_artist_monitoring_is_independent_from_wishlist_tracks(legacy_db):
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("""
        CREATE TABLE watchlist_artists(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_name TEXT NOT NULL,
            spotify_artist_id TEXT,
            musicbrainz_artist_id TEXT
        )
    """)
    conn.execute(
        "INSERT INTO watchlist_artists(artist_name, spotify_artist_id) VALUES(?, ?)",
        ("Drake", "sp1"),
    )
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    payload = {
        "id": "sp_track_2",
        "name": "Wishlist Only",
        "artists": [{"id": "sp_artist_2", "name": "Other Wishlist Artist"}],
        "album": {
            "id": "sp_album_2",
            "name": "Wishlist Only Single",
            "album_type": "single",
            "total_tracks": 1,
            "artists": [{"id": "sp_artist_2", "name": "Other Wishlist Artist"}],
        },
        "track_number": 1,
    }
    conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_type) VALUES(?,?,?)",
        ("sp_track_2", json.dumps(payload), "manual"),
    )
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    drake = conn.execute("SELECT monitored FROM lib2_artists WHERE name='Drake'").fetchone()
    wishlist_artist = conn.execute(
        "SELECT monitored FROM lib2_artists WHERE name='Other Wishlist Artist'"
    ).fetchone()
    wishlist_track = conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE title='Wishlist Only'"
    ).fetchone()
    conn.close()

    assert drake["monitored"] == 1
    assert wishlist_artist["monitored"] == 0
    assert wishlist_track["monitored"] == 1
