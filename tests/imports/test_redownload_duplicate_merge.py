"""#1810 — Redownload with a changed path leaves two rows for one file.

The import INSERTs a fresh catalog row when the replacement lands at a new
``file_path`` (even a capitalization-only change); the redownload hook then
repoints the ORIGINAL row at the same path. ``fold_imported_duplicate_tracks``
is the merge half of the hook: the original row (id + playlist references)
survives, the import-created duplicate is folded into it and deleted.

The fixture mirrors the production schema's types: track ids are TEXT
(post-migration), ``track_credits.track_id`` is TEXT, ``sync_match_cache``
and ``manual_library_track_matches`` carry INTEGER track-id columns.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from core.imports.redownload_merge import fold_imported_duplicate_tracks

_ROOT = Path(__file__).resolve().parent.parent.parent

OLD_PATH = "/music/Artist/Album/song.flac"
NEW_PATH = "/music/Artist/Album/Song.flac"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY,
            album_id TEXT NOT NULL,
            artist_id TEXT NOT NULL,
            title TEXT NOT NULL,
            track_number INTEGER,
            disc_number INTEGER,
            duration INTEGER,
            file_path TEXT,
            server_path TEXT,
            bitrate INTEGER,
            file_size INTEGER,
            year INTEGER,
            spotify_track_id TEXT,
            musicbrainz_recording_id TEXT,
            recording_disambiguation TEXT,
            musicbrainz_match_status TEXT,
            musicbrainz_last_attempted TEXT,
            quality_profile_id INTEGER DEFAULT NULL,
            acquired_quality_json TEXT,
            retention_json TEXT,
            owner_profile_id INTEGER,
            server_source TEXT DEFAULT 'soulsync',
            title_norm TEXT,
            track_artist TEXT,
            track_artist_norm TEXT,
            created_at TEXT DEFAULT '2024-01-01 00:00:00',
            updated_at TEXT DEFAULT '2024-01-01 00:00:00'
        );
        CREATE TABLE listening_history (id INTEGER PRIMARY KEY, db_track_id TEXT);
        CREATE TABLE track_credits (track_id TEXT, name_norm TEXT,
                                    PRIMARY KEY (track_id, name_norm)) WITHOUT ROWID;
        CREATE TABLE sample_analysis (track_id TEXT PRIMARY KEY, bpm REAL);
        CREATE TABLE sync_match_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            server_source TEXT NOT NULL,
            server_track_id INTEGER NOT NULL,
            UNIQUE(spotify_track_id, server_source)
        );
        CREATE TABLE manual_library_track_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER DEFAULT 1,
            source TEXT NOT NULL,
            source_track_id TEXT NOT NULL,
            server_source TEXT DEFAULT '',
            library_track_id INTEGER NOT NULL,
            UNIQUE(profile_id, source, source_track_id, server_source)
        );
        """
    )
    yield c
    c.close()


def _track(conn, tid, path, **over):
    vals = {
        "album_id": "10", "artist_id": "20", "title": "Song", "track_number": 1,
        "disc_number": 1, "duration": 200000, "bitrate": 900,
        "file_size": 20000, "spotify_track_id": "", "musicbrainz_recording_id": "",
        "acquired_quality_json": None, "retention_json": None,
        "created_at": "2024-06-01 00:00:00",
    }
    vals.update(over)
    conn.execute(
        "INSERT INTO tracks (id, file_path, album_id, artist_id, title, "
        "track_number, disc_number, duration, bitrate, file_size, "
        "spotify_track_id, musicbrainz_recording_id, acquired_quality_json, "
        "retention_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(tid), path, vals["album_id"], vals["artist_id"], vals["title"],
         vals["track_number"], vals["disc_number"], vals["duration"],
         vals["bitrate"], vals["file_size"], vals["spotify_track_id"],
         vals["musicbrainz_recording_id"], vals["acquired_quality_json"],
         vals["retention_json"], vals["created_at"]),
    )
    conn.commit()


def _keeper(conn, tid="1"):
    return conn.execute("SELECT * FROM tracks WHERE id = ?", (str(tid),)).fetchone()


def _count(conn):
    return conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]


# --- the headline bug -------------------------------------------------------

def test_duplicate_at_new_path_is_folded_into_original(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)  # the row the import just created
    folded = fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    assert folded == ["2"]
    conn.commit()
    assert _count(conn) == 1
    assert _keeper(conn) is not None  # original id survives
    # the hook's own UPDATE then moves the keeper onto the new path
    conn.execute("UPDATE tracks SET file_path = ? WHERE id = '1'", (NEW_PATH,))
    rows = conn.execute(
        "SELECT id FROM tracks WHERE file_path = ?", (NEW_PATH,)).fetchall()
    assert [r["id"] for r in rows] == ["1"]


def test_same_path_redownload_has_no_duplicate(conn):
    _track(conn, 1, NEW_PATH)  # import refreshed the original row in place
    assert fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH) == []
    assert _count(conn) == 1


def test_missing_original_leaves_imported_row_alone(conn):
    _track(conn, 2, NEW_PATH)  # original deleted mid-download
    assert fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH) == []
    assert _count(conn) == 1


def test_noop_inputs(conn):
    _track(conn, 1, OLD_PATH)
    assert fold_imported_duplicate_tracks(conn.cursor(), None, NEW_PATH) == []
    assert fold_imported_duplicate_tracks(conn.cursor(), "", NEW_PATH) == []
    assert fold_imported_duplicate_tracks(conn.cursor(), "1", "") == []
    assert _count(conn) == 1


def test_zero_string_id_is_a_real_id(conn):
    _track(conn, 0, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    assert fold_imported_duplicate_tracks(conn.cursor(), "0", NEW_PATH) == ["2"]
    assert _keeper(conn, "0") is not None


# --- scope: whose rows may fold ----------------------------------------------

def test_other_servers_row_at_the_path_is_never_folded(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET server_source = 'plex' WHERE id = '2'")
    conn.commit()
    assert fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH) == []
    assert _count(conn) == 2  # the plex entry is a distinct catalog row


def test_other_profiles_row_at_the_path_is_never_folded(conn):
    _track(conn, 1, OLD_PATH)  # keeper is shared (owner NULL)
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET owner_profile_id = 7 WHERE id = '2'")
    conn.commit()
    assert fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH) == []
    assert _count(conn) == 2


def test_owner_and_server_source_never_travel(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET owner_profile_id = 7 WHERE id = '1'")
    conn.execute("UPDATE tracks SET owner_profile_id = 7 WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["owner_profile_id"] == 7  # unchanged, not the duplicate's doing
    assert keeper["server_source"] == "soulsync"


# --- what travels --------------------------------------------------------------

def test_file_facts_come_from_the_replacement_row(conn):
    _track(conn, 1, OLD_PATH, duration=180000, bitrate=320, file_size=9000)
    _track(conn, 2, NEW_PATH, duration=201000, bitrate=1411, file_size=21000)
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["duration"] == 201000
    assert keeper["bitrate"] == 1411
    assert keeper["file_size"] == 21000


def test_null_and_zero_file_facts_do_not_erase_keeper(conn):
    # the import writer stores 0 for "unknown" — 0 is not a fact
    _track(conn, 1, OLD_PATH, duration=180000, bitrate=320, file_size=9000)
    _track(conn, 2, NEW_PATH, duration=0, bitrate=0, file_size=None)
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["duration"] == 180000
    assert keeper["bitrate"] == 320
    assert keeper["file_size"] == 9000


def test_enrichment_fills_only_empty_keeper_values(conn):
    _track(conn, 1, OLD_PATH, spotify_track_id="",
           musicbrainz_recording_id="mbid-original")
    _track(conn, 2, NEW_PATH, spotify_track_id="sp-dup",
           musicbrainz_recording_id="mbid-dup")
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["spotify_track_id"] == "sp-dup"          # gap filled
    assert keeper["musicbrainz_recording_id"] == "mbid-original"  # never overwritten


def test_zero_counts_as_empty_for_year_and_track_number(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET year = 0, track_number = 0 WHERE id = '1'")
    conn.execute("UPDATE tracks SET year = 2001, track_number = 3 WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["year"] == 2001
    assert keeper["track_number"] == 3


def test_retention_pair_and_server_path_taken_from_duplicate(conn):
    _track(conn, 1, OLD_PATH, retention_json='{"claim": "old"}',
           acquired_quality_json='{"q": "old"}')
    _track(conn, 2, NEW_PATH, retention_json=None, acquired_quality_json=None)
    conn.execute("UPDATE tracks SET server_path = '/old-server.flac' WHERE id = '1'")
    conn.execute("UPDATE tracks SET server_path = '/new-server.flac' WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["retention_json"] is None       # stale claim cleared
    assert keeper["acquired_quality_json"] is None
    assert keeper["server_path"] == "/new-server.flac"  # companion follows the file


def test_stale_server_path_cleared_when_duplicate_has_none(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET server_path = '/old-server.flac' WHERE id = '1'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    # export prefers server_path over file_path — a stale one would emit
    # the just-deleted old file
    assert _keeper(conn)["server_path"] is None


def test_quality_profile_id_never_travels(conn):
    # NULL means "follow the app-wide default" — a setting, not a gap
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET quality_profile_id = 7 WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    assert _keeper(conn)["quality_profile_id"] is None


def test_match_status_columns_never_travel(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute(
        "UPDATE tracks SET musicbrainz_match_status = 'matched', "
        "musicbrainz_last_attempted = '2026-01-01' WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["musicbrainz_match_status"] is None
    assert keeper["musicbrainz_last_attempted"] is None


def test_recording_pair_fills_as_a_unit_when_keeper_has_no_id(conn):
    _track(conn, 1, OLD_PATH, musicbrainz_recording_id="")
    _track(conn, 2, NEW_PATH, musicbrainz_recording_id="mbid-B")
    conn.execute(
        "UPDATE tracks SET recording_disambiguation = 'B live version' WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["musicbrainz_recording_id"] == "mbid-B"
    assert keeper["recording_disambiguation"] == "B live version"


def test_recording_pair_never_mixes_ids(conn):
    _track(conn, 1, OLD_PATH, musicbrainz_recording_id="mbid-A")
    _track(conn, 2, NEW_PATH, musicbrainz_recording_id="mbid-B")
    conn.execute(
        "UPDATE tracks SET recording_disambiguation = 'B live version' WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["musicbrainz_recording_id"] == "mbid-A"
    assert keeper["recording_disambiguation"] is None  # B's note must not dress A's id


def test_disambiguation_fills_when_both_rows_name_the_same_recording(conn):
    _track(conn, 1, OLD_PATH, musicbrainz_recording_id="mbid-A")
    _track(conn, 2, NEW_PATH, musicbrainz_recording_id="mbid-A")
    conn.execute(
        "UPDATE tracks SET recording_disambiguation = 'live' WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    assert _keeper(conn)["recording_disambiguation"] == "live"


def test_norm_columns_never_carried(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET title = 'Song (Live)', title_norm = 'song (live)' WHERE id = '1'")
    conn.execute("UPDATE tracks SET title_norm = 'song' WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["title"] == "Song (Live)"
    assert keeper["title_norm"] == "song (live)"


def test_filled_track_artist_does_not_carry_its_norm(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET track_artist = 'Someone', track_artist_norm = 'someone' WHERE id = '2'")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    keeper = _keeper(conn)
    assert keeper["track_artist"] == "Someone"   # raw value fills the gap
    assert keeper["track_artist_norm"] is None   # norm left for trigger/backfill


# --- references ----------------------------------------------------------------

def test_listening_history_repointed_and_duplicate_credits_removed(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("INSERT INTO listening_history (db_track_id) VALUES ('2')")
    conn.execute("INSERT INTO listening_history (db_track_id) VALUES ('1')")
    conn.execute("INSERT INTO track_credits VALUES ('2', 'artist')")
    conn.execute("INSERT INTO track_credits VALUES ('1', 'artist')")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    hist = [r[0] for r in conn.execute(
        "SELECT db_track_id FROM listening_history ORDER BY id")]
    assert hist == ["1", "1"]
    credits = [r[0] for r in conn.execute("SELECT track_id FROM track_credits")]
    assert credits == ["1"]


def test_credits_move_when_the_keeper_artist_is_the_duplicates(conn):
    _track(conn, 1, OLD_PATH)  # keeper has no track artist of its own
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET track_artist = 'Someone' WHERE id = '2'")
    conn.execute("INSERT INTO track_credits VALUES ('2', 'someone')")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    credits = {(r["track_id"], r["name_norm"]) for r in conn.execute(
        "SELECT * FROM track_credits")}
    assert credits == {("1", "someone")}  # moved, not orphaned or lost


def test_credits_not_moved_when_artists_differ(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("UPDATE tracks SET track_artist = 'Keeper Artist' WHERE id = '1'")
    conn.execute("UPDATE tracks SET track_artist = 'Someone' WHERE id = '2'")
    conn.execute("INSERT INTO track_credits VALUES ('2', 'someone')")
    conn.execute("INSERT INTO track_credits VALUES ('1', 'keeper artist')")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    credits = {(r["track_id"], r["name_norm"]) for r in conn.execute(
        "SELECT * FROM track_credits")}
    assert credits == {("1", "keeper artist")}


def test_sample_analysis_moves_to_keeper_replacing_stale(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("INSERT INTO sample_analysis VALUES ('1', 90.0)")   # old file
    conn.execute("INSERT INTO sample_analysis VALUES ('2', 120.0)")  # replacement
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    rows = conn.execute("SELECT track_id, bpm FROM sample_analysis").fetchall()
    assert [(r["track_id"], r["bpm"]) for r in rows] == [("1", 120.0)]


def test_sample_analysis_keeper_row_kept_when_duplicate_has_none(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("INSERT INTO sample_analysis VALUES ('1', 90.0)")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    rows = conn.execute("SELECT track_id FROM sample_analysis").fetchall()
    assert [r["track_id"] for r in rows] == ["1"]


def test_sync_pairings_repoint_to_keeper_soulsync_only(conn):
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute(
        "INSERT INTO sync_match_cache (spotify_track_id, server_source, server_track_id) "
        "VALUES ('sp1', 'soulsync', 2)")
    # a plex pairing that numerically collides with the duplicate's id is a
    # different id space — it must not be repointed or deleted
    conn.execute(
        "INSERT INTO sync_match_cache (spotify_track_id, server_source, server_track_id) "
        "VALUES ('sp2', 'plex', 2)")
    conn.execute(
        "INSERT INTO manual_library_track_matches "
        "(profile_id, source, source_track_id, server_source, library_track_id) "
        "VALUES (1, 'spotify', 'sp1', 'soulsync', 2)")
    conn.commit()
    fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    cache = {(r["spotify_track_id"], r["server_source"]): r["server_track_id"]
             for r in conn.execute("SELECT * FROM sync_match_cache")}
    assert cache == {("sp1", "soulsync"): 1, ("sp2", "plex"): 2}
    manual = conn.execute(
        "SELECT library_track_id FROM manual_library_track_matches").fetchall()
    assert [r[0] for r in manual] == [1]


# --- multiple duplicates: the freshest import wins ------------------------------

def test_multiple_duplicates_freshest_wins_regardless_of_hash_id_order(conn):
    # production ids are TEXT hashes with no temporal meaning — '100' is
    # NOT older than '9'. created_at decides: the stale legacy row folds
    # first, the fresh import row folds last and wins.
    _track(conn, 1, OLD_PATH, duration=100000,
           created_at="2024-01-01 00:00:00")
    _track(conn, 9, NEW_PATH, duration=111000,
           retention_json='{"claim": "stale"}',
           created_at="2024-06-01 00:00:00")
    _track(conn, 100, NEW_PATH, duration=222000,
           retention_json=None,
           created_at="2026-10-21 14:46:27")
    _track(conn, 4, "/music/Artist/Album/other.flac")
    folded = fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    assert sorted(folded) == ["100", "9"]
    keeper = _keeper(conn)
    assert keeper["duration"] == 222000            # fresh import's facts
    assert keeper["retention_json"] is None        # fresh import clears the stale claim
    remaining = {r[0] for r in conn.execute("SELECT id FROM tracks")}
    assert remaining == {"1", "4"}  # unrelated row at another path untouched


# --- path forms ------------------------------------------------------------------

def test_normalized_path_form_still_folds(conn):
    # another writer stored the same file with windows separators + a ./
    # prefix; the exact match misses, the normalized fallback must not
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, "./music\\Artist\\Album\\Song.flac")
    folded = fold_imported_duplicate_tracks(
        conn.cursor(), "1", "music/Artist/Album/Song.flac")
    assert folded == ["2"]
    assert _count(conn) == 1


def test_exact_and_normalized_duplicates_both_fold(conn):
    # exact match must not short-circuit the normalized fallback: both the
    # row stored at the target string and the row stored with windows
    # separators for the same file fold in one pass
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    _track(conn, 3, "/music\\Artist\\Album\\Song.flac",
           created_at="2024-05-01 00:00:00")
    folded = fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    assert sorted(folded) == ["2", "3"]
    assert _count(conn) == 1


def test_different_root_does_not_fold(conn):
    # a server-view path under a different root is not provably the same
    # file from the string alone — left alone rather than merged on a guess
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, "/server-view/Artist/Album/Song.flac")
    folded = fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    assert folded == []
    assert _count(conn) == 2


@pytest.mark.skipif(os.name == "nt", reason="normcase folds case on Windows only")
def test_case_only_variant_is_a_different_file_on_posix(conn):
    # on a case-sensitive filesystem Song.flac and song.flac in another
    # directory are different files; merging them would be data loss
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, "/MUSIC/Artist/Album/Song.flac")
    folded = fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    assert folded == []
    assert _count(conn) == 2


# --- failure handling --------------------------------------------------------------

def test_missing_optional_tables_do_not_abort_the_fold():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE tracks (id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, "
        "title TEXT, file_path TEXT, duration INTEGER, bitrate INTEGER, "
        "file_size INTEGER, server_source TEXT DEFAULT 'soulsync', "
        "created_at TEXT, updated_at TEXT)")
    c.execute("INSERT INTO tracks (id, file_path, duration) VALUES ('1', ?, 100)", (OLD_PATH,))
    c.execute("INSERT INTO tracks (id, file_path, duration) VALUES ('2', ?, 200)", (NEW_PATH,))
    c.commit()
    folded = fold_imported_duplicate_tracks(c.cursor(), "1", NEW_PATH)
    assert folded == ["2"]
    assert c.execute("SELECT duration FROM tracks WHERE id = '1'").fetchone()[0] == 200
    c.close()


def test_real_db_error_aborts_instead_of_deleting_anyway(conn):
    # a trigger that aborts the history repoint is a REAL failure, not a
    # missing table — the fold must raise (caller rolls back) and the
    # duplicate must survive rather than be deleted with dangling history
    _track(conn, 1, OLD_PATH)
    _track(conn, 2, NEW_PATH)
    conn.execute("INSERT INTO listening_history (db_track_id) VALUES ('2')")
    conn.execute(
        "CREATE TRIGGER fail_history BEFORE UPDATE ON listening_history "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END")
    conn.commit()
    with pytest.raises(sqlite3.Error):
        fold_imported_duplicate_tracks(conn.cursor(), "1", NEW_PATH)
    conn.rollback()
    assert _count(conn) == 2


def test_pipeline_hook_folds_before_repointing():
    src = (_ROOT / "core" / "imports" / "pipeline.py").read_text(
        encoding="utf-8", errors="replace")
    fold = src.index("_folded = fold_imported_duplicate_tracks(")
    repoint = src.index(
        "UPDATE tracks SET file_path = ?, updated_at = CURRENT_TIMESTAMP",
        src.index("if redownload_ctx:"))
    assert fold < repoint, "duplicate must be folded before the original is repointed"
    # the fold is savepoint-guarded so a fold failure cannot kill the repoint
    assert "SAVEPOINT redownload_fold" in src
    assert "ROLLBACK TO redownload_fold" in src
