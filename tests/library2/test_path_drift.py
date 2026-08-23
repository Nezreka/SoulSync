"""pathdrift25-01 — stale ``lib2_track_files.path`` against a present file.

The user's real case: the row says ``.../Bunny Girl/01-01 - Bunny Girl.flac``
while the file on disk is ``.../Bunny Girl/01 - Bunny Girl.flac``. The shared
resolver only fixes root/mount drift, so the row resolves to ``None``, the scan
counts a miss, ``persist_tag_cache`` is never reached (metadata scan stays
"pending" forever) and after two scans the row is even marked
``missing_confirmed`` — for a file that is physically there.

These tests pin both halves of the contract: the read-only reconcile that
*proposes* a fix without ever guessing between ambiguous candidates, and the
scan's refusal to confirm a miss while such a candidate exists.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.library2 import path_drift as PD
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def drift_db(tmp_path):
    """A lib2 DB whose single file row points at a drifted filename."""
    music = tmp_path / "music" / "1nonly" / "Bunny Girl"
    music.mkdir(parents=True)
    real = music / "01 - Bunny Girl.flac"
    real.write_bytes(b"audio-bytes")

    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('1nonly')")
    artist_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Bunny Girl')",
                (artist_id,))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title, track_number) "
                "VALUES(?, 'Bunny Girl', 1)", (album_id,))
    track_id = cur.lastrowid
    stored = str(music / "01-01 - Bunny Girl.flac")
    cur.execute("INSERT INTO lib2_track_files(track_id, path, size) VALUES(?,?,?)",
                (track_id, stored, len(b"audio-bytes")))
    file_id = cur.lastrowid
    conn.commit()

    class _DB:
        database_path = db_path

        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    yield _DB(), conn, {"file_id": file_id, "track_id": track_id,
                        "stored": stored, "real": str(real), "dir": str(music)}
    conn.close()


# --------------------------------------------------------------- matching ---

def test_disc_prefix_drift_is_matched(tmp_path):
    listing = ["01 - Bunny Girl.flac", "cover.jpg", "folder.png"]
    match = PD.match_drifted_filename("01-01 - Bunny Girl.flac", listing)
    assert match.status == "proposed"
    assert match.basename == "01 - Bunny Girl.flac"


def test_two_equally_plausible_candidates_are_never_auto_picked():
    listing = ["01 - Bunny Girl.flac", "1-01 - Bunny Girl.flac"]
    match = PD.match_drifted_filename("01-01 - Bunny Girl.flac", listing)
    assert match.status == "ambiguous"
    assert match.basename is None
    assert sorted(match.alternatives) == ["01 - Bunny Girl.flac", "1-01 - Bunny Girl.flac"]


def test_size_breaks_a_tie_between_same_titled_candidates():
    listing = ["01 - Bunny Girl.flac", "1-01 - Bunny Girl.flac"]
    match = PD.match_drifted_filename(
        "01-01 - Bunny Girl.flac", listing,
        expected_size=11,
        size_of=lambda name: 11 if name == "1-01 - Bunny Girl.flac" else 999,
    )
    assert match.status == "proposed"
    assert match.basename == "1-01 - Bunny Girl.flac"


def test_a_different_track_number_is_not_a_match():
    """Same title, different track: two real songs, not one drifted name."""
    match = PD.match_drifted_filename("01 - Intro.flac", ["02 - Intro.flac"])
    assert match.status == "no_candidate"


def test_a_different_extension_is_not_a_match():
    """A converted/derivative file is its own row, not this row's file."""
    match = PD.match_drifted_filename("01 - Bunny Girl.flac", ["01 - Bunny Girl.mp3"])
    assert match.status == "no_candidate"


def test_unicode_titles_survive_normalization():
    match = PD.match_drifted_filename("01-01 - 東京.flac", ["01 - 東京.flac"])
    assert match.status == "proposed"


def test_non_audio_files_are_ignored():
    match = PD.match_drifted_filename("01 - Bunny Girl.flac", ["01 - Bunny Girl.txt"])
    assert match.status == "no_candidate"


# ------------------------------------------------------------------- scan ---

def test_scan_proposes_the_real_file_and_writes_nothing(drift_db):
    # No resolver patching: the stored filename genuinely does not exist while
    # its directory genuinely does. Faking the resolver here would also fake
    # `resolve_lib2_directory`, which is built on it — and the whole point of
    # this scan is that the directory resolves while the file does not.
    db, conn, ids = drift_db

    report = PD.scan_path_drift(db)

    assert report["checked"] == 1
    assert len(report["proposals"]) == 1
    proposal = report["proposals"][0]
    assert proposal["file_id"] == ids["file_id"]
    assert proposal["status"] == "proposed"
    assert proposal["candidate_path"] == ids["real"]
    assert proposal["proposed_stored_path"] == ids["real"]

    # Read-only: the scan itself must not mutate a single row.
    assert conn.execute(
        "SELECT path FROM lib2_track_files WHERE id=?", (ids["file_id"],)
    ).fetchone()["path"] == ids["stored"]


def test_scan_skips_files_that_resolve_normally(drift_db, monkeypatch):
    db, _conn, ids = drift_db
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
                        lambda path, config_manager=None: ids["real"])

    report = PD.scan_path_drift(db)
    assert report["proposals"] == []
    assert report["checked"] == 0


def test_scan_will_not_steal_a_file_another_row_already_owns(drift_db):
    db, conn, ids = drift_db
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
        (ids["track_id"], ids["real"]),
    )
    conn.commit()

    report = PD.scan_path_drift(db)
    assert report["proposals"] == []
    assert [entry["status"] for entry in report["unresolved"]] == ["claimed"]


def test_scan_is_bounded(drift_db, monkeypatch):
    db, conn, ids = drift_db
    for n in range(5):
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
            (ids["track_id"], f"/nowhere/{n}.flac"),
        )
    conn.commit()
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda path, config_manager=None: (
        path if path == ids["real"] else None))

    report = PD.scan_path_drift(db, limit=3)
    assert report["checked"] == 3
    assert report["truncated"] is True


# ------------------------------------------------------------------ apply ---

def test_apply_repoints_the_row_and_clears_the_missing_lifecycle(drift_db, monkeypatch):
    db, conn, ids = drift_db
    conn.execute(
        "UPDATE lib2_track_files SET file_state='missing_confirmed', "
        "missing_scan_count=2, missing_since=CURRENT_TIMESTAMP WHERE id=?",
        (ids["file_id"],),
    )
    conn.commit()
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda path, config_manager=None: (
        path if path == ids["real"] else None))

    result = PD.apply_path_drift_fix(db, ids["file_id"], ids["real"])

    assert result["success"] is True
    row = conn.execute(
        "SELECT path, file_state, missing_scan_count, missing_since "
        "FROM lib2_track_files WHERE id=?", (ids["file_id"],)
    ).fetchone()
    assert row["path"] == ids["real"]
    assert row["file_state"] == "active"
    assert row["missing_scan_count"] == 0
    assert row["missing_since"] is None


def test_apply_refuses_a_candidate_that_is_gone(drift_db, monkeypatch):
    db, conn, ids = drift_db
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda path, config_manager=None: None)

    result = PD.apply_path_drift_fix(db, ids["file_id"], ids["real"])
    assert result["success"] is False
    assert conn.execute(
        "SELECT path FROM lib2_track_files WHERE id=?", (ids["file_id"],)
    ).fetchone()["path"] == ids["stored"]


def test_apply_refuses_a_candidate_another_row_owns(drift_db, monkeypatch):
    db, conn, ids = drift_db
    conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                 (ids["track_id"], ids["real"]))
    conn.commit()
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda path, config_manager=None: (
        path if path == ids["real"] else None))

    result = PD.apply_path_drift_fix(db, ids["file_id"], ids["real"])
    assert result["success"] is False
    assert "already" in result["error"].lower()


# ------------------------------------------------------------ scan bridge ---

def test_rescan_never_confirms_a_miss_while_the_file_is_findable(
        drift_db, monkeypatch):
    """The parent directory exists, so the old code called the miss "healthy"
    and confirmed it on the second scan — for a song sitting right there."""
    from core.library2.scan import rescan_files

    db, conn, ids = drift_db
    # No resolver patching here on purpose: the stored filename genuinely does
    # not exist while its directory genuinely does — the real drift shape.
    monkeypatch.setattr("core.library2.paths.missing_path_root_is_healthy",
                        lambda path: True)

    stats = {}
    for _ in range(3):
        stats = rescan_files(db)

    row = conn.execute(
        "SELECT file_state, missing_scan_count FROM lib2_track_files WHERE id=?",
        (ids["file_id"],),
    ).fetchone()
    assert row["file_state"] == "missing_suspected"
    assert stats["path_drift"] == 1


def test_rescan_still_confirms_a_genuinely_absent_file(drift_db, monkeypatch):
    from core.library2.scan import rescan_files

    db, conn, ids = drift_db
    conn.execute("UPDATE lib2_track_files SET path=? WHERE id=?",
                 (ids["dir"] + "/07 - Not Here.flac", ids["file_id"]))
    conn.commit()
    # No resolver patching here on purpose: the stored filename genuinely does
    # not exist while its directory genuinely does — the real drift shape.
    monkeypatch.setattr("core.library2.paths.missing_path_root_is_healthy",
                        lambda path: True)

    for _ in range(2):
        stats = rescan_files(db)

    row = conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE id=?", (ids["file_id"],)
    ).fetchone()
    assert row["file_state"] == "missing_confirmed"
    assert stats["path_drift"] == 0


def test_apply_refuses_when_the_row_resolves_again(drift_db, monkeypatch):
    """Someone else fixed it (or the mount came back) between scan and apply."""
    db, conn, ids = drift_db
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
                        lambda path, config_manager=None: ids["real"])

    result = PD.apply_path_drift_fix(db, ids["file_id"], ids["real"])
    assert result["success"] is False
    assert conn.execute(
        "SELECT path FROM lib2_track_files WHERE id=?", (ids["file_id"],)
    ).fetchone()["path"] == ids["stored"]


def test_post_import_auto_repoints_an_unambiguous_legacy_path(drift_db):
    db, conn, ids = drift_db
    conn.execute("UPDATE lib2_track_files SET legacy_import_run_id='upgrade'")
    conn.commit()

    stats = PD.reconcile_imported_path_drift(db, batch_size=1)

    row = conn.execute(
        "SELECT path, file_state FROM lib2_track_files WHERE id=?", (ids["file_id"],)
    ).fetchone()
    assert (row["path"], row["file_state"]) == (ids["real"], "active")
    assert stats["repointed"] == 1


def test_post_import_leaves_ambiguous_drift_suspected(drift_db):
    db, conn, ids = drift_db
    (Path(ids["dir"]) / "1-01 - Bunny Girl.flac").write_bytes(b"audio-bytes")
    conn.execute("UPDATE lib2_track_files SET legacy_import_run_id='upgrade'")
    conn.commit()

    stats = PD.reconcile_imported_path_drift(db)

    row = conn.execute(
        "SELECT path, file_state FROM lib2_track_files WHERE id=?", (ids["file_id"],)
    ).fetchone()
    assert (row["path"], row["file_state"]) == (ids["stored"], "missing_suspected")
    assert stats["repointed"] == 0 and stats["protected"] == 1


def test_a_manual_refresh_repoints_a_renamed_file_instead_of_losing_it(drift_db):
    """Order of operations is the fix: rename check first, verdict last.

    Refresh & Scan used to *notice* the drift candidate and do nothing with
    it — the row was merely kept at ``missing_suspected`` forever, its tag
    cache stuck on "pending", waiting for a review-only repair job that ships
    disabled. A scan a person asked for now applies the unambiguous proposal
    and rescans the file on its corrected path in the same pass.
    """
    from core.library2.scan import rescan_files

    db, conn, ids = drift_db
    stats = rescan_files(db, manual=True)

    row = conn.execute(
        """SELECT path, file_state, missing_scan_count
             FROM lib2_track_files WHERE id=?""",
        (ids["file_id"],),
    ).fetchone()
    assert row["path"] == ids["real"]
    assert row["file_state"] == "active"
    assert row["missing_scan_count"] == 0
    assert stats["path_repointed"] == 1
    # The repointed row is a present file now and gets the ordinary refresh.
    assert stats["scanned"] == 1
    assert stats["missing"] == 0
    assert stats["missing_confirmed"] == 0


def test_a_manual_refresh_still_refuses_to_guess_between_two_candidates(drift_db):
    """Precision over recall survives the automation.

    Handing one track's file to another is the one failure this must never
    create, so an ambiguous folder is left untouched and merely protected
    from confirmation — exactly what the review-only tool exists for.
    """
    from core.library2.scan import rescan_files

    db, conn, ids = drift_db
    # Same 11 bytes as the real file, so size cannot break the tie either.
    Path(ids["dir"], "1-01 - Bunny Girl.flac").write_bytes(b"other-audio")

    stats = rescan_files(db, manual=True)

    row = conn.execute(
        "SELECT path, file_state FROM lib2_track_files WHERE id=?",
        (ids["file_id"],),
    ).fetchone()
    assert row["path"] == ids["stored"]
    assert row["file_state"] == "missing_suspected"
    assert stats["path_repointed"] == 0
    assert stats["path_drift"] == 1
