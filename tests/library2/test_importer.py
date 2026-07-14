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


def test_import_preloads_row_lookup_maps_instead_of_n_plus_one_selects(legacy_db):
    """The large-library contract: entity writes remain ordered, but lookup
    SELECTs must not scale once per legacy album/track/wishlist row."""
    conn = sqlite3.connect(legacy_db.path)
    for index in range(20, 50):
        conn.execute(
            "INSERT INTO albums VALUES(?,?,?,2024,NULL,NULL,1,NULL)",
            (index, 1, f"Scale Album {index}"),
        )
        conn.execute(
            "INSERT INTO tracks VALUES(?,?,1,?,1,180000,?,1000,5000,NULL)",
            (index + 1000, index, f"Scale Track {index}", f"/m/{index}.flac"),
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
    for index in range(30):
        payload = {
            "id": f"wishlist-track-{index}",
            "name": f"Wishlist Track {index}",
            "artists": [{"id": "wishlist-artist", "name": "Wishlist Artist"}],
            "album": {
                "id": f"wishlist-album-{index}",
                "name": f"Wishlist Album {index}",
                "album_type": "single",
                "total_tracks": 1,
                "artists": [{"id": "wishlist-artist", "name": "Wishlist Artist"}],
            },
        }
        conn.execute(
            "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data) VALUES(?,?)",
            (payload["id"], json.dumps(payload)),
        )
    conn.commit()
    conn.close()

    statements = []
    original_get_connection = legacy_db._get_connection

    def traced_connection():
        traced = original_get_connection()
        traced.set_trace_callback(statements.append)
        return traced

    legacy_db._get_connection = traced_connection
    stats = import_legacy_library(legacy_db)
    normalized = [" ".join(statement.upper().split()) for statement in statements]

    assert stats["albums"] == 32
    assert stats["tracks"] == 33
    assert stats["wishlist_tracks"] == 30
    banned_per_row_reads = (
        "SELECT COUNT(*) AS C FROM TRACKS WHERE ALBUM_ID=",
        "SELECT ID FROM LIB2_TRACK_FILES WHERE TRACK_ID=",  # paired with AND PATH below
        "SELECT ID FROM LIB2_ALBUMS WHERE SPOTIFY_ID=",
        "SELECT ID FROM LIB2_TRACKS WHERE ALBUM_ID=",
    )
    for banned in banned_per_row_reads:
        matches = [statement for statement in normalized if banned in statement]
        if "LIB2_TRACK_FILES" in banned:
            matches = [statement for statement in matches if "AND PATH=" in statement]
        assert not matches, banned


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


def test_rerun_reconciles_legacy_file_path_without_touching_secondary_file(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    track_id = conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, source) VALUES(?, ?, ?)",
        (track_id, "/m/manual-secondary.flac", "manual"),
    )
    conn.execute("UPDATE tracks SET file_path='/m/renamed.flac' WHERE id=100")
    conn.commit()
    conn.close()

    stats = import_legacy_library(legacy_db)

    conn = legacy_db._get_connection()
    files = conn.execute(
        """SELECT path, legacy_track_id, legacy_import_run_id
             FROM lib2_track_files WHERE track_id=? ORDER BY path""",
        (track_id,),
    ).fetchall()
    conn.close()
    assert [row["path"] for row in files] == [
        "/m/manual-secondary.flac",
        "/m/renamed.flac",
    ]
    assert files[0]["legacy_track_id"] is None
    assert files[0]["legacy_import_run_id"] is None
    assert files[1]["legacy_track_id"] == 100
    assert files[1]["legacy_import_run_id"]
    assert stats["files"] == 1
    assert stats["reconciled_files"] == 1


def test_rerun_removes_deleted_legacy_snapshot_rows(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    conn.execute("DELETE FROM tracks WHERE album_id=11")
    conn.execute("DELETE FROM albums WHERE id=11")
    conn.commit()
    conn.close()

    stats = import_legacy_library(legacy_db)

    conn = legacy_db._get_connection()
    assert conn.execute(
        "SELECT 1 FROM lib2_tracks WHERE legacy_track_id=102"
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM lib2_albums WHERE legacy_album_id=11"
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM lib2_track_files WHERE path='/m/single.flac'"
    ).fetchone() is None
    conn.close()
    assert stats["reconciled_files"] == 1
    assert stats["reconciled_tracks"] == 1
    assert stats["reconciled_albums"] == 1


def test_reconcile_detaches_provider_identity_instead_of_deleting_it(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    conn.execute(
        "UPDATE lib2_albums SET spotify_id='provider-album' WHERE legacy_album_id=11"
    )
    conn.execute(
        "UPDATE lib2_tracks SET spotify_id='provider-track' WHERE legacy_track_id=102"
    )
    conn.execute("DELETE FROM tracks WHERE album_id=11")
    conn.execute("DELETE FROM albums WHERE id=11")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)

    conn = legacy_db._get_connection()
    album = conn.execute(
        "SELECT legacy_album_id, origin FROM lib2_albums WHERE spotify_id='provider-album'"
    ).fetchone()
    track = conn.execute(
        "SELECT legacy_track_id FROM lib2_tracks WHERE spotify_id='provider-track'"
    ).fetchone()
    file_row = conn.execute(
        "SELECT 1 FROM lib2_track_files WHERE path='/m/single.flac'"
    ).fetchone()
    conn.close()
    assert dict(album) == {"legacy_album_id": None, "origin": "discography"}
    assert track["legacy_track_id"] is None
    assert file_row is None


def test_reset_rebuilds(legacy_db):
    import_legacy_library(legacy_db)
    stats = import_legacy_library(legacy_db, reset=True)
    assert stats["tracks"] == 3
    conn = sqlite3.connect(legacy_db.path)
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 3
    conn.close()


def test_album_monitor_intent_reprojects_and_survives_reset(legacy_db):
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    album_id = conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    record_rule(conn, "album", album_id, False, PROVENANCE_USER)
    # Simulate compatibility-column drift: the rule remains authoritative.
    conn.execute("UPDATE lib2_albums SET monitored=1 WHERE id=?", (album_id,))
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    row = conn.execute(
        "SELECT monitored FROM lib2_albums WHERE title='Views'"
    ).fetchone()
    assert row["monitored"] == 0
    conn.close()

    stats = import_legacy_library(legacy_db, reset=True)
    conn = legacy_db._get_connection()
    row = conn.execute(
        """SELECT al.monitored, r.monitored AS rule_monitored, r.provenance
             FROM lib2_albums al
             JOIN lib2_monitor_rules r
               ON r.entity_type='album' AND r.entity_id=al.id AND r.profile_id=1
            WHERE al.title='Views'"""
    ).fetchone()
    conn.close()

    assert stats["album_monitor_intent_restored"] == 1
    assert dict(row) == {
        "monitored": 0,
        "rule_monitored": 0,
        "provenance": "user_explicit",
    }


def test_import_uses_live_default_after_profile_one_is_deleted(legacy_db):
    from core.library2.schema import ensure_library_v2_schema

    conn = legacy_db._get_connection()
    ensure_library_v2_schema(conn)
    conn.execute("UPDATE quality_profiles SET is_default=0")
    conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=2")
    conn.execute("DELETE FROM quality_profiles WHERE id=1")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
            profile_ids = {
                row[0] for row in conn.execute(
                    f"SELECT DISTINCT quality_profile_id FROM {table}")
            }
            assert profile_ids == {2}, table
    finally:
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


def test_wishlist_seed_preserves_valid_track_profile_only(legacy_db, caplog):
    from core.quality.schema import ensure_quality_profiles_schema

    conn = sqlite3.connect(legacy_db.path)
    ensure_quality_profiles_schema(conn)
    conn.execute("INSERT INTO quality_profiles(id, name) VALUES(7, 'Wishlist Hi-Res')")
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            profile_id INTEGER DEFAULT 1,
            quality_profile_id INTEGER
        )
    """)

    def _payload(track_id, title):
        return {
            "id": track_id,
            "name": title,
            "artists": [{"id": "sp_artist_q", "name": "Profiled Artist"}],
            "album": {
                "id": "sp_album_q",
                "name": "Profiled Album",
                "album_type": "album",
                "total_tracks": 2,
                "artists": [{"id": "sp_artist_q", "name": "Profiled Artist"}],
            },
        }

    conn.executemany(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, quality_profile_id) "
        "VALUES(?,?,?)",
        [
            ("sp_profiled", json.dumps(_payload("sp_profiled", "Profiled")), 7),
            ("sp_dangling", json.dumps(_payload("sp_dangling", "Dangling")), 999),
        ],
    )
    conn.commit()
    conn.close()

    caplog.set_level("WARNING")
    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM quality_profiles WHERE is_default=1 ORDER BY id LIMIT 1"
    ).fetchone()[0]
    track_profiles = {
        row["title"]: row["quality_profile_id"]
        for row in conn.execute(
            "SELECT title, quality_profile_id FROM lib2_tracks "
            "WHERE spotify_id IN ('sp_profiled', 'sp_dangling')")
    }
    album_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_albums WHERE spotify_id='sp_album_q'"
    ).fetchone()[0]
    artist_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_artists WHERE spotify_id='sp_artist_q'"
    ).fetchone()[0]
    conn.close()

    assert track_profiles == {"Profiled": 7, "Dangling": default_id}
    assert album_profile == default_id
    assert artist_profile == default_id
    assert "invalid quality profile 999" in caplog.text


def test_wishlist_profile_conflict_is_visible_and_latest_row_wins(legacy_db, caplog):
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            profile_id INTEGER DEFAULT 1,
            quality_profile_id INTEGER
        )
    """)
    payload = {
        "id": "sp_conflict",
        "name": "Conflicted",
        "artists": [{"id": "sp_conflict_artist", "name": "Conflict Artist"}],
        "album": {
            "id": "sp_conflict_album",
            "name": "Conflict Album",
            "artists": [{"id": "sp_conflict_artist", "name": "Conflict Artist"}],
        },
    }
    conn.executemany(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, quality_profile_id) "
        "VALUES(?,?,?)",
        [
            ("sp_conflict::first", json.dumps(payload), 1),
            ("sp_conflict::second", json.dumps(payload), 2),
        ],
    )
    conn.commit()
    conn.close()

    caplog.set_level("WARNING")
    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    profile_id = conn.execute(
        "SELECT quality_profile_id FROM lib2_tracks WHERE spotify_id='sp_conflict'"
    ).fetchone()[0]
    conn.close()
    assert profile_id == 2
    assert "assign different quality profiles" in caplog.text


def test_wishlist_seed_does_not_clamp_discography_expected_count(legacy_db):
    """A wishlist track that lands on a provider-only (discography) release must
    not shrink the release's expected_track_count to the wishlisted rows — the
    later tracklist materialization trims to expected, so a clamp would
    truncate the whole release to one track."""
    import_legacy_library(legacy_db)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, spotify_id) "
        "VALUES('Wishlist Artist','Wishlist Artist','sp_artist_1')")
    artist_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id, "
        "origin, monitored, track_count, expected_track_count) "
        "VALUES(?, 'Big Release', 'album', 'sp_album_1', 'discography', 0, 12, 12)",
        (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                 (album_id, artist_id))
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
        "id": "sp_track_9",
        "name": "Wanted Album Cut",
        "artists": [{"id": "sp_artist_1", "name": "Wishlist Artist"}],
        "album": {
            "id": "sp_album_1",
            "name": "Big Release",
            "album_type": "album",
            "total_tracks": 12,
            "artists": [{"id": "sp_artist_1", "name": "Wishlist Artist"}],
        },
        "track_number": 3,
    }
    conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_type) VALUES(?,?,?)",
        ("sp_track_9", json.dumps(payload), "manual"))
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    album = conn.execute("SELECT * FROM lib2_albums WHERE spotify_id='sp_album_1'").fetchone()
    conn.close()
    assert album["expected_track_count"] == 12
    assert album["origin"] == "discography"


def test_full_band_name_credit_is_not_split_into_ghost_artists(legacy_db):
    """'Simon & Garfunkel' as a track credit must reuse the existing artist row,
    not be split at '&' into two invented artists."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute(
        "INSERT INTO artists VALUES(2,'Simon & Garfunkel',NULL,NULL,NULL,NULL,NULL)")
    conn.execute(
        "INSERT INTO albums VALUES(20,2,'Bookends',1968,NULL,NULL,1,NULL)")
    conn.execute(
        "INSERT INTO tracks VALUES(200,20,2,'Mrs. Robinson',1,240000,'/m/mrs.flac',900,4000,"
        "'Simon & Garfunkel')")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    names = {r["name"] for r in conn.execute("SELECT name FROM lib2_artists")}
    conn.close()
    assert "Simon & Garfunkel" in names
    assert "Simon" not in names
    assert "Garfunkel" not in names


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
