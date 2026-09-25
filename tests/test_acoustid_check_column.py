"""The library's "Check" column after the AcoustID tool has run.

Reported: the AcoustID checker processed the whole library, and Michael
Jackson's tracks still read "Not scanned" — after a refresh, and after a
re-scan. They always would have. The column is rendered from
``lib2_track_files.acoustid_status``, and the scanner never wrote that column:
it recorded its verdict in ``verification_status`` (plus ``library_history``)
and left the AcoustID one NULL. In the reporter's database 164 files sit in
exactly that state — verified by a scan, displayed as never checked.

The two columns are not redundant. ``verification_status`` is the file's
overall standing (verified / unverified / force_imported / human_verified);
``acoustid_status`` is what the fingerprint check itself concluded
(pass / skip / fail). A scan produces both, so it has to write both — and
``fail`` in particular has nowhere else to live: a mismatching file is the one
case where "was it checked?" and "did it pass?" give different answers.
"""

from __future__ import annotations

import sqlite3
import types

import pytest

from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
from database.music_database import MusicDatabase


FPATH = "/music/Michael Jackson/Thriller/01 - Wanna Be Startin' Somethin'.flac"


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _seed_file(db, *, verification_status=None, acoustid_status=None) -> int:
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO lib2_artists(id, name) VALUES(1, 'Michael Jackson')"
        )
        conn.execute(
            "INSERT INTO lib2_albums(id, primary_artist_id, title) VALUES(1, 1, 'Thriller')"
        )
        conn.execute(
            "INSERT INTO lib2_tracks(id, album_id, title) VALUES(1, 1, 'Wanna Be Startin'' Somethin''')"
        )
        cursor = conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, is_primary, "
            "verification_status, acoustid_status) VALUES(1, ?, 1, ?, ?)",
            (FPATH, verification_status, acoustid_status),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _file_row(db, file_id) -> sqlite3.Row:
    conn = db._get_connection()
    try:
        return conn.execute(
            "SELECT verification_status, acoustid_status, pipeline_result_json "
            "FROM lib2_track_files WHERE id=?",
            (file_id,),
        ).fetchone()
    finally:
        conn.close()


def _context(db):
    findings = []
    return types.SimpleNamespace(
        db=db,
        config_manager=None,
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
        report_progress=None,
        report_change=None,
        findings=findings,
    )


class _Result:
    findings_created = 0
    findings_skipped_dedup = 0
    errors = 0
    scanned = 0
    skipped = 0


def _acoustid(title, artist, score=0.99):
    """A client that answers, in the structured shape the scanner prefers."""
    return types.SimpleNamespace(
        lookup_with_status=lambda fpath: {
            "status": "ok",
            "best_score": score,
            "recordings": [{"title": title, "artist": artist}],
        },
    )


def _acoustid_no_match():
    """The lookup worked; AcoustID simply has no entry for this audio."""
    return types.SimpleNamespace(
        lookup_with_status=lambda fpath: {
            "status": "no_match", "recordings": [], "best_score": 0.0,
        },
    )


def _acoustid_broken():
    """Missing API key / no chromaprint — an error, not an answer."""
    return types.SimpleNamespace(
        lookup_with_status=lambda fpath: {
            "status": "unavailable", "recordings": [],
            "error": "No AcoustID API key configured",
        },
    )


def test_a_passing_scan_records_the_fingerprint_verdict(db, monkeypatch):
    """The reported case. The file was already 'verified' at import, the scan
    agrees — and the Check column has to say so instead of "Not scanned"."""
    file_id = _seed_file(db, verification_status="verified")
    monkeypatch.setattr(
        "core.tag_writer.read_file_tags",
        lambda fpath: {"artist": "Michael Jackson", "title": "Wanna Be Startin' Somethin'"},
    )
    context = _context(db)

    AcoustIDScannerJob()._scan_file(
        FPATH, "1",
        {
            "title": "Wanna Be Startin' Somethin'",
            "artist": "Michael Jackson",
            "lib2_file_id": file_id,
            "file_path": FPATH,
        },
        _acoustid("Wanna Be Startin' Somethin'", "Michael Jackson"),
        context, _Result(),
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    row = _file_row(db, file_id)
    assert row["acoustid_status"] == "pass"
    assert row["verification_status"] == "verified"


def test_a_scan_of_an_untouched_file_records_both_columns(db, monkeypatch):
    file_id = _seed_file(db)
    monkeypatch.setattr(
        "core.tag_writer.read_file_tags",
        lambda fpath: {"artist": "Michael Jackson", "title": "Beat It"},
    )

    AcoustIDScannerJob()._scan_file(
        FPATH, "1",
        {"title": "Beat It", "artist": "Michael Jackson",
         "lib2_file_id": file_id, "file_path": FPATH},
        _acoustid("Beat It", "Michael Jackson"),
        _context(db), _Result(),
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    row = _file_row(db, file_id)
    assert row["acoustid_status"] == "pass"
    assert row["verification_status"] == "verified"


def test_a_mismatch_is_recorded_as_a_failed_check(db, monkeypatch):
    """A file the fingerprint contradicts was very much scanned. Leaving the
    column NULL made the worst case indistinguishable from the untested one."""
    file_id = _seed_file(db, verification_status="verified")
    monkeypatch.setattr(
        "core.tag_writer.read_file_tags",
        lambda fpath: {"artist": "Michael Jackson", "title": "Beat It"},
    )
    context = _context(db)

    AcoustIDScannerJob()._scan_file(
        FPATH, "1",
        {"title": "Beat It", "artist": "Michael Jackson",
         "lib2_file_id": file_id, "file_path": FPATH},
        _acoustid("Smooth Criminal", "Michael Jackson"),
        context, _Result(),
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    assert context.findings, "a genuine mismatch must still raise its finding"
    assert _file_row(db, file_id)["acoustid_status"] == "fail"


def test_a_verdict_the_scan_cannot_reach_leaves_the_column_alone(db, monkeypatch):
    """A lookup that could not run (no API key, no chromaprint) is not a
    verdict. Recording one would paint an unchecked library as checked — the
    exact reason `fingerprint_and_lookup` is not used here: it returns None for
    "no match" AND for "everything is broken"."""
    file_id = _seed_file(db, verification_status="verified")
    silent = _acoustid_broken()

    AcoustIDScannerJob()._scan_file(
        FPATH, "1",
        {"title": "Beat It", "artist": "Michael Jackson",
         "lib2_file_id": file_id, "file_path": FPATH},
        silent, _context(db), _Result(),
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    assert _file_row(db, file_id)["acoustid_status"] is None

# ── the inconclusive outcomes ────────────────────────────────────────────────
#
# Reported after the first fix: "nach dem acoustid scan gibt es immer noch
# tracks wo steht das kein scan durchgeführt wurde". A scan that reaches a file
# and cannot conclude anything about it still LOOKED at it — AcoustID simply
# has no entry, or the fingerprint scored too low to mean anything. Recording
# nothing left those files indistinguishable from files the scan never got to,
# which is the state the user is complaining about.


def test_a_file_acoustid_has_never_heard_of_is_recorded_as_skipped(db):
    file_id = _seed_file(db)
    silent = _acoustid_no_match()

    AcoustIDScannerJob()._scan_file(
        FPATH, "1",
        {"title": "Beat It", "artist": "Michael Jackson",
         "lib2_file_id": file_id, "file_path": FPATH},
        silent, _context(db), _Result(),
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    row = _file_row(db, file_id)
    assert row["acoustid_status"] == "skip"
    # The wording now comes from the shared verifier, so the tooltip says
    # exactly what the download would have said about the same file.
    assert "No match in AcoustID database" in (row["pipeline_result_json"] or "")


def test_a_fingerprint_below_the_threshold_is_recorded_as_skipped(db):
    file_id = _seed_file(db)
    weak = types.SimpleNamespace(
        lookup_with_status=lambda fpath: {
            "status": "ok", "best_score": 0.20,
            "recordings": [{"title": "Beat It", "artist": "Michael Jackson"}],
        },
    )

    AcoustIDScannerJob()._scan_file(
        FPATH, "1",
        {"title": "Beat It", "artist": "Michael Jackson",
         "lib2_file_id": file_id, "file_path": FPATH},
        weak, _context(db), _Result(),
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    assert _file_row(db, file_id)["acoustid_status"] == "skip"


def test_an_inconclusive_scan_does_not_touch_the_verification_standing(db):
    """'skip' is not a verdict about the file's standing — a file verified at
    import must not be demoted because AcoustID has no entry for it."""
    file_id = _seed_file(db, verification_status="verified")
    silent = _acoustid_no_match()

    AcoustIDScannerJob()._scan_file(
        FPATH, "1",
        {"title": "Beat It", "artist": "Michael Jackson",
         "lib2_file_id": file_id, "file_path": FPATH},
        silent, _context(db), _Result(),
        fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6,
    )

    row = _file_row(db, file_id)
    assert row["verification_status"] == "verified"
    assert row["acoustid_status"] == "skip"
