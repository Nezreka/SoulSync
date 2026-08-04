"""Release edition + recording shadow model (audit P1-04 / ADR-04).

Release groups (lib2_albums) get concrete editions; tracks get recordings.
Recordings merge ONLY on hard IDs (ISRC/MBID/Spotify) — never on titles —
and unverified canonical links become review findings, not merges.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.editions import backfill_editions, default_edition_id


def _counts(conn):
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("lib2_release_editions", "lib2_recordings",
                      "lib2_release_tracks", "lib2_recording_review")
    }


def test_backfill_creates_default_edition_and_release_tracks(imported_conn):
    conn = imported_conn
    albums = conn.execute("SELECT id FROM lib2_albums").fetchall()
    tracks = conn.execute("SELECT id FROM lib2_tracks").fetchall()
    editions = conn.execute(
        "SELECT release_group_id, is_default, signature FROM lib2_release_editions"
    ).fetchall()
    # One default edition per album, each with a matching signature.
    assert len(editions) == len(albums)
    assert all(e["is_default"] == 1 and e["signature"] for e in editions)
    # Every track is materialized on its album's default edition.
    for t in tracks:
        rt = conn.execute(
            "SELECT release_edition_id, recording_id FROM lib2_release_tracks "
            "WHERE track_id=?", (t["id"],)).fetchall()
        assert len(rt) == 1
        assert rt[0]["recording_id"] is not None


def test_backfill_is_idempotent(imported_conn):
    conn = imported_conn
    before = _counts(conn)
    stats = backfill_editions(conn.cursor())
    assert stats["editions"] == 0
    assert stats["release_tracks"] == 0
    assert _counts(conn) == before


def test_same_title_without_hard_ids_stays_separate_recordings(imported_conn):
    """The fixture's One Dance single + album track share a canonical link but
    no hard IDs — they must keep separate recordings and get a review row."""
    conn = imported_conn
    single = conn.execute(
        "SELECT t.id, t.canonical_track_id FROM lib2_tracks t "
        "JOIN lib2_albums al ON al.id = t.album_id "
        "WHERE al.album_type='single' AND t.canonical_track_id IS NOT NULL"
    ).fetchone()
    assert single is not None
    rec_single = conn.execute(
        "SELECT recording_id FROM lib2_release_tracks WHERE track_id=?",
        (single["id"],)).fetchone()[0]
    rec_album = conn.execute(
        "SELECT recording_id FROM lib2_release_tracks WHERE track_id=?",
        (single["canonical_track_id"],)).fetchone()[0]
    assert rec_single != rec_album
    review = conn.execute(
        "SELECT reason FROM lib2_recording_review WHERE track_id=? AND other_track_id=?",
        (single["id"], single["canonical_track_id"])).fetchone()
    assert review is not None and review["reason"] == "canonical_link_unverified"


def test_shared_isrc_merges_recording_and_files_no_review(imported_conn):
    conn = imported_conn
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Isrc Artist')")
    artist = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                "VALUES(?, 'Album A', 'album')", (artist,))
    album_a = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                "VALUES(?, 'Song B', 'single')", (artist,))
    album_b = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title, isrc) "
                "VALUES(?, 'Song B', 'DEX8770001')", (album_a,))
    on_album = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title, isrc, canonical_track_id) "
                "VALUES(?, 'Song B', 'DEX8770001', ?)", (album_b, on_album))
    as_single = cur.lastrowid
    backfill_editions(cur)

    rec_a = conn.execute(
        "SELECT recording_id FROM lib2_release_tracks WHERE track_id=?",
        (on_album,)).fetchone()[0]
    rec_b = conn.execute(
        "SELECT recording_id FROM lib2_release_tracks WHERE track_id=?",
        (as_single,)).fetchone()[0]
    assert rec_a == rec_b
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_recording_review WHERE track_id=?",
        (as_single,)).fetchone()[0] == 0


def test_live_version_never_merges_by_title(imported_conn):
    conn = imported_conn
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Live Artist')")
    artist = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                "VALUES(?, 'Studio', 'album')", (artist,))
    studio = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                "VALUES(?, 'Unplugged', 'live')", (artist,))
    live = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Same Song')",
                (studio,))
    studio_track = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Same Song')",
                (live,))
    live_track = cur.lastrowid
    backfill_editions(cur)
    recs = {
        conn.execute("SELECT recording_id FROM lib2_release_tracks WHERE track_id=?",
                     (tid,)).fetchone()[0]
        for tid in (studio_track, live_track)
    }
    assert len(recs) == 2


def test_two_editions_of_one_group_stay_separate(imported_conn):
    conn = imported_conn
    cur = conn.cursor()
    album = conn.execute("SELECT id FROM lib2_albums LIMIT 1").fetchone()["id"]
    default_ed = default_edition_id(cur, album)
    assert default_ed is not None
    cur.execute(
        "INSERT INTO lib2_release_editions(release_group_id, is_default, "
        "disambiguation, spotify_id, signature) "
        "VALUES(?, 0, 'Deluxe', 'sp-deluxe', 'sig-deluxe')", (album,))
    deluxe = cur.lastrowid
    # The deluxe edition carries its own tracklist rows; the default
    # edition's tracklist is untouched.
    cur.execute("INSERT INTO lib2_recordings(title) VALUES('Bonus Track')")
    bonus_rec = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_release_tracks(release_edition_id, recording_id, "
        "track_number) VALUES(?,?,99)", (deluxe, bonus_rec))
    default_tracks = conn.execute(
        "SELECT COUNT(*) FROM lib2_release_tracks WHERE release_edition_id=?",
        (default_ed,)).fetchone()[0]
    deluxe_tracks = conn.execute(
        "SELECT COUNT(*) FROM lib2_release_tracks WHERE release_edition_id=?",
        (deluxe,)).fetchone()[0]
    assert deluxe_tracks == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_release_tracks WHERE release_edition_id=? "
        "AND track_number=99", (default_ed,)).fetchone()[0] == 0
    assert default_tracks >= 1


def test_only_one_default_edition_per_group(imported_conn):
    conn = imported_conn
    album = conn.execute("SELECT id FROM lib2_albums LIMIT 1").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO lib2_release_editions(release_group_id, is_default, "
            "signature) VALUES(?, 1, 'dup-default')", (album,))


def test_duplicate_hard_ids_rejected_on_recordings(imported_conn):
    conn = imported_conn
    conn.execute(
        "INSERT INTO lib2_recordings(title, isrc) VALUES('A', 'USUM11111111')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO lib2_recordings(title, isrc) VALUES('B', 'USUM11111111')")
    # Empty/NULL hard IDs stay free — no phantom uniqueness.
    conn.execute("INSERT INTO lib2_recordings(title, isrc) VALUES('C', NULL)")
    conn.execute("INSERT INTO lib2_recordings(title, isrc) VALUES('D', NULL)")


def test_prune_removes_shadow_rows_of_deleted_tracks(imported_conn):
    conn = imported_conn
    cur = conn.cursor()
    track = conn.execute(
        "SELECT track_id FROM lib2_release_tracks WHERE track_id IS NOT NULL LIMIT 1"
    ).fetchone()["track_id"]
    recording = conn.execute(
        "SELECT recording_id FROM lib2_release_tracks WHERE track_id=?",
        (track,)).fetchone()["recording_id"]
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (track,))
    conn.execute("DELETE FROM lib2_tracks WHERE id=?", (track,))
    backfill_editions(cur)
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_release_tracks WHERE track_id=?",
        (track,)).fetchone()[0] == 0
    # The recording vanished with its last release track (fixture recordings
    # are 1:1 unless hard IDs merged them).
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_recordings WHERE id=?",
        (recording,)).fetchone()[0] == 0
