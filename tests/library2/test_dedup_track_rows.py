"""§49.4 — two rows in one album for one recording.

The production DB holds 15 such groups (31 rows). Three different causes sit
in there and only one of them is a row this pass may remove:

* a fileless provider row beside the local row that actually holds the file
  (`Stardust`, `Popular Monster`, `Superhero`, `Oblivion`, …) — one of the two
  is pure placeholder, and that is the duplicate;
* a rename left the old file behind (`01 - Fearless Pt. II.flac` next to
  `01-01 - Fearless Pt. II.flac`) — **two real files**. Deleting a row here
  would orphan a file the user still has, so it becomes a finding;
* the provider handed a live cut the studio take's ISRC (`AM`) — genuinely
  different audio, must be left alone entirely.

The rule that separates them: a row is only removable when it has no active
file AND no legacy binding of its own. Files are never deleted here.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.dedup_repair import fold_duplicate_track_rows
from core.library2.editions import backfill_editions


def _artist(conn, name="Geoxor"):
    return conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES(?,?)", (name, name)
    ).lastrowid


def _album(conn, artist_id, title, *, album_type="album"):
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, origin,"
        "                        monitored, external_ids)"
        " VALUES(?,?,?,'library',1,'{}')", (artist_id, title, album_type)
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role)"
        " VALUES(?,?,'primary')", (album_id, artist_id))
    return album_id


def _track(conn, album_id, title, *, track_number=1, isrc=None,
           legacy_track_id=None):
    return conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,"
        "                        isrc, monitored, legacy_track_id)"
        " VALUES(?,?,?,1,?,1,?)",
        (album_id, title, track_number, isrc, legacy_track_id)
    ).lastrowid


def _file(conn, track_id, path):
    return conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, import_status, file_state,"
        "                             is_primary) VALUES(?,?,'imported','active',1)",
        (track_id, path)).lastrowid


@pytest.fixture
def stardust(imported_conn):
    """The common shape: the local row plus the provider's own slot."""
    conn = imported_conn
    artist = _artist(conn)
    album = _album(conn, artist, "Stardust")
    local = _track(conn, album, "Stardust", track_number=2, isrc="QM4TW2312345",
                   legacy_track_id="LEGACY-1")
    placeholder = _track(conn, album, "Stardust", track_number=6,
                         isrc="QM4TW2312345")
    _file(conn, local, "Geoxor/Stardust/02 - Stardust.flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()
    return conn, album, local, placeholder


def test_the_fileless_placeholder_row_is_removed(stardust):
    conn, album, local, _placeholder = stardust

    stats = fold_duplicate_track_rows(conn)
    conn.commit()

    assert stats["tracks_folded"] == 1
    rows = [r[0] for r in conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? ORDER BY id", (album,))]
    assert rows == [local]


def test_the_survivor_keeps_the_file(stardust):
    conn, _album, local, _placeholder = stardust

    fold_duplicate_track_rows(conn)
    conn.commit()

    paths = [r[0] for r in conn.execute(
        "SELECT path FROM lib2_track_files WHERE track_id=?", (local,))]
    assert paths == ["Geoxor/Stardust/02 - Stardust.flac"]


def test_two_real_files_become_a_finding_not_a_deletion(imported_conn):
    """`Fearless`: both rows carry a file left over from the rename."""
    conn = imported_conn
    artist = _artist(conn, "Lost Sky")
    album = _album(conn, artist, "Fearless", album_type="single")
    old = _track(conn, album, "Fearless Pt. II", isrc="GB2LD1700381",
                 legacy_track_id="LEGACY-A")
    new = _track(conn, album, "Fearless Pt. II", isrc="GB2LD1700381",
                 legacy_track_id="LEGACY-B")
    _file(conn, old, "Lost Sky/Fearless/01 - Fearless Pt. II.flac")
    _file(conn, new, "Lost Sky/Fearless/01-01 - Fearless Pt. II.flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()

    stats = fold_duplicate_track_rows(conn)
    conn.commit()

    assert stats["tracks_folded"] == 0
    assert stats["findings"] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (album,)
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_track_files f JOIN lib2_tracks t"
        "  ON t.id=f.track_id WHERE t.album_id=?", (album,)).fetchone()[0] == 2


def test_a_live_cut_sharing_the_studio_isrc_is_not_a_duplicate_row(imported_conn):
    conn = imported_conn
    artist = _artist(conn, "Arctic Monkeys")
    album = _album(conn, artist, "AM")
    studio = _track(conn, album, "Do I Wanna Know?", track_number=1,
                    isrc="GBCEL1300362", legacy_track_id="LEGACY-S")
    live = _track(conn, album, "Do I Wanna Know? (live from the iTunes Festival)",
                  track_number=13, isrc="GBCEL1300362", legacy_track_id="LEGACY-L")
    _file(conn, studio, "Arctic Monkeys/AM/01-01 - Do I Wanna Know?.flac")
    _file(conn, live, "Arctic Monkeys/AM/01-13 - Do I Wanna Know? (live).flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()

    stats = fold_duplicate_track_rows(conn)

    assert (stats["tracks_folded"], stats["findings"]) == (0, 0)
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (album,)
    ).fetchone()[0] == 2


def test_rows_on_different_albums_are_not_folded_into_each_other(imported_conn):
    """Cross-release sharing is what the recording link is for, not a fold —
    both releases are real and both keep their position."""
    conn = imported_conn
    artist = _artist(conn, "Kali Uchis")
    album = _album(conn, artist, "Red Moon In Venus")
    single = _album(conn, artist, "Moonlight", album_type="single")
    _track(conn, album, "Moonlight", track_number=14, isrc="USUM72219486")
    single_track = _track(conn, single, "Moonlight", track_number=1,
                          isrc="USUM72219486", legacy_track_id="LEGACY-M")
    _file(conn, single_track, "Kali Uchis/Moonlight/01 - Moonlight.flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()

    assert fold_duplicate_track_rows(conn)["tracks_folded"] == 0


def test_the_repair_run_folds_track_rows_too(imported_conn, legacy_db):
    """It has to be reachable from the repair the user actually triggers."""
    from core.library2.dedup_repair import repair_duplicate_artists

    conn = imported_conn
    artist = _artist(conn)
    album = _album(conn, artist, "Stardust")
    local = _track(conn, album, "Stardust", track_number=2, isrc="QM4TW2312345",
                   legacy_track_id="LEGACY-1")
    _track(conn, album, "Stardust", track_number=6, isrc="QM4TW2312345")
    _file(conn, local, "Geoxor/Stardust/02 - Stardust.flac")
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["tracks_folded"] == 1
