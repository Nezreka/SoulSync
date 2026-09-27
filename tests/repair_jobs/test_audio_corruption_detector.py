"""Corrupt File Detector (#1000): decode-test library FLACs, flag damaged ones
to delete + re-download. The scan only creates findings — never deletes.

Covered:
* check_flac_integrity: clean → ok, non-zero decode → flagged, no decoder → never
  flags (a false positive would delete a good file), ffmpeg fallback.
* scan: corrupt file → one 'corrupt_audio' finding on the track; clean file →
  none; non-FLAC ignored; "modified within N days" narrows; no decoder → no-op.
"""

from __future__ import annotations

import os

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import core.repair_jobs.audio_corruption_detector as mod
from core.repair_jobs.audio_corruption_detector import (
    AudioCorruptionDetectorJob,
    check_flac_integrity,
)
from core.repair_jobs.base import JobContext


# --- check_flac_integrity (decode test) --------------------------------------

def _fake_proc(returncode=0, stderr=""):
    return SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")


def test_integrity_clean_flac(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda b: "/usr/bin/flac" if b == "flac" else None)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _fake_proc(0))
    assert check_flac_integrity("/x.flac") == (True, "")


def test_integrity_corrupt_flac_flagged(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda b: "/usr/bin/flac" if b == "flac" else None)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _fake_proc(1, "x.flac: ERROR while decoding data\nstate = FRAME_CRC_MISMATCH"))
    ok, reason = check_flac_integrity("/x.flac")
    assert ok is False
    assert "ERROR" in reason


def test_integrity_no_decoder_never_flags(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda b: None)  # no flac, no ffmpeg
    assert check_flac_integrity("/x.flac") == (True, "")


def test_integrity_ffmpeg_fallback_flags(monkeypatch):
    # No flac binary, ffmpeg present and reports a decode error.
    monkeypatch.setattr(mod.shutil, "which", lambda b: "/usr/bin/ffmpeg" if b == "ffmpeg" else None)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _fake_proc(1, "[flac @ 0x..] Error decoding frame"))
    ok, reason = check_flac_integrity("/x.flac")
    assert ok is False
    assert "Error decoding" in reason


def test_integrity_timeout_does_not_flag(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda b: "/usr/bin/flac" if b == "flac" else None)

    def _boom(*a, **k):
        raise mod.subprocess.TimeoutExpired(cmd="flac", timeout=1)

    monkeypatch.setattr(mod.subprocess, "run", _boom)
    assert check_flac_integrity("/x.flac") == (True, "")  # our timeout ≠ file corruption


# --- scan -------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_a, **_k):
        return self

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        pass


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def _get_connection(self):
        return _FakeConn(self._rows)


def _row(track_id, title, path):
    return {"id": track_id, "title": title, "artist_name": "Artist",
            "album_title": "Album", "file_path": path}


def _context(rows, tmp_path, settings=None):
    cfg = MagicMock()
    values = settings or {}
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    findings = []
    ctx = JobContext(
        db=_FakeDB(rows),
        transfer_folder=str(tmp_path),
        config_manager=cfg,
        create_finding=lambda **kw: (findings.append(kw) or True),
    )
    return ctx, findings


def _prep(monkeypatch, verdicts):
    """Force a decoder to be 'available' and stub the decode test with a
    path→(ok, reason) mapping."""
    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p, **kw: p)
    monkeypatch.setattr(mod, "check_flac_integrity",
                        lambda path: verdicts.get(path, (True, "")))


def test_scan_flags_corrupt_flac(tmp_path, monkeypatch):
    bad = tmp_path / "01 - Bad.flac"
    bad.write_bytes(b"x")
    _prep(monkeypatch, {str(bad): (False, "FRAME_CRC_MISMATCH")})

    ctx, findings = _context([_row(7, "Bad", str(bad))], tmp_path)
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert result.findings_created == 1
    f = findings[0]
    assert f["finding_type"] == "corrupt_audio"
    assert f["entity_type"] == "track" and f["entity_id"] == "7"
    assert "FRAME_CRC_MISMATCH" in f["description"]


def test_scan_ignores_clean_flac(tmp_path, monkeypatch):
    good = tmp_path / "02 - Good.flac"
    good.write_bytes(b"x")
    _prep(monkeypatch, {str(good): (True, "")})

    ctx, findings = _context([_row(8, "Good", str(good))], tmp_path)
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert result.findings_created == 0 and findings == []


def test_scan_ignores_non_flac(tmp_path, monkeypatch):
    mp3 = tmp_path / "03 - Song.mp3"
    mp3.write_bytes(b"x")
    called = {"n": 0}
    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p, **kw: p)
    monkeypatch.setattr(mod, "check_flac_integrity",
                        lambda p: (called.__setitem__("n", called["n"] + 1), (True, ""))[1])

    ctx, findings = _context([_row(9, "Song", str(mp3))], tmp_path)
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert findings == [] and called["n"] == 0  # mp3 never decode-tested


def test_scan_no_decoder_is_noop(tmp_path, monkeypatch):
    bad = tmp_path / "04 - Bad.flac"
    bad.write_bytes(b"x")
    monkeypatch.setattr(mod, "_decoder_available", lambda: False)
    monkeypatch.setattr(mod, "check_flac_integrity",
                        lambda p: (_ for _ in ()).throw(AssertionError("must not test without a decoder")))

    ctx, findings = _context([_row(10, "Bad", str(bad))], tmp_path)
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert findings == [] and result.scanned == 0


def test_scan_only_modified_within_days_narrows(tmp_path, monkeypatch):
    import os, time
    old = tmp_path / "05 - Old.flac"
    old.write_bytes(b"x")
    old_time = time.time() - 30 * 86400
    os.utime(old, (old_time, old_time))
    _prep(monkeypatch, {str(old): (False, "corrupt")})  # would flag if tested

    # Only test files modified in the last 7 days → the 30-day-old file is skipped.
    ctx, findings = _context([_row(11, "Old", str(old))], tmp_path,
                             settings={"repair.jobs.audio_corruption_detector.only_modified_within_days": 7})
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert findings == [] and result.skipped == 1


def test_scan_resolves_paths_with_the_job_context(tmp_path, monkeypatch):
    """#1000 follow-up (abclive): the scan called the path resolver BARE — no
    transfer folder, no config_manager — so it had zero base directories to
    suffix-walk and every Docker/NAS library path silently resolved to None:
    '6741 FLAC files decode-tested, 0 corrupt, 6741 skipped' in 0.1s. The
    resolver must receive the context's transfer folder + config manager."""
    seen = {}

    def _spy(p, **kw):
        seen.update(kw)
        return p

    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "resolve_library_file_path", _spy)
    monkeypatch.setattr(mod, "check_flac_integrity", lambda p: (True, ""))

    f = tmp_path / "06 - Track.flac"
    f.write_bytes(b"x")
    ctx, _ = _context([_row(12, "Track", str(f))], tmp_path)
    AudioCorruptionDetectorJob().scan(ctx)

    assert seen.get("transfer_folder") == str(tmp_path)
    assert seen.get("config_manager") is ctx.config_manager


def test_scan_surfaces_total_resolution_failure(tmp_path, monkeypatch):
    """When EVERY path fails to resolve (the Docker path-mapping case), the job
    must say so loudly instead of quietly reporting a healthy all-skip run."""
    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p, **kw: None)
    monkeypatch.setattr(mod, "check_flac_integrity",
                        lambda p: (_ for _ in ()).throw(AssertionError("nothing should be tested")))

    reports = []
    ctx, _ = _context([_row(13, "Ghost", "/plex/sees/this.flac")], tmp_path)
    ctx.report_progress = lambda **kw: reports.append(kw)
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert result.skipped == 1 and result.findings_created == 0
    assert any(r.get("log_type") == "error" and "No library paths" in (r.get("log_line") or "")
               for r in reports)


# ── a file that changed under the decode test is not evidence ────────────────
#
# The scan walks the library while the import pipeline is still moving files
# into it. A cross-device move is copy-then-delete, so `flac -t` reading a
# half-written FLAC reports exactly what a genuinely damaged one does
# ("LOST_SYNC after processing N samples"). The finding was then written with a
# path that no longer existed by the time the user looked at it.
#
# The decode verdict only means something if the bytes under it held still.

def test_a_file_that_vanished_during_the_test_is_not_flagged(tmp_path, monkeypatch):
    gone = tmp_path / "01 - Gone.flac"
    gone.write_bytes(b"x")

    def _decode(path):
        os.remove(path)               # the mover finishes mid-test
        return False, "LOST_SYNC after processing 0 samples"

    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p, **kw: p)
    monkeypatch.setattr(mod, "check_flac_integrity", _decode)

    ctx, findings = _context([_row(20, "Gone", str(gone))], tmp_path)
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert findings == [], "flagged a file that no longer exists"
    assert result.findings_created == 0 and result.skipped == 1


def test_a_file_still_being_written_is_not_flagged(tmp_path, monkeypatch):
    """Same race, caught by size instead of absence: the copy is still growing."""
    growing = tmp_path / "02 - Growing.flac"
    growing.write_bytes(b"x")

    def _decode(path):
        with open(path, "ab") as fh:
            fh.write(b"more bytes arriving")
        return False, "LOST_SYNC after processing 6418432 samples"

    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p, **kw: p)
    monkeypatch.setattr(mod, "check_flac_integrity", _decode)

    ctx, findings = _context([_row(21, "Growing", str(growing))], tmp_path)
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert findings == [], "flagged a file that was still being written"
    assert result.findings_created == 0 and result.skipped == 1


def test_a_file_that_held_still_is_still_flagged(tmp_path, monkeypatch):
    """The guard must not swallow real corruption."""
    bad = tmp_path / "03 - Really Bad.flac"
    bad.write_bytes(b"x")
    _prep(monkeypatch, {str(bad): (False, "FRAME_CRC_MISMATCH")})

    ctx, findings = _context([_row(22, "Really Bad", str(bad))], tmp_path)
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert result.findings_created == 1 and len(findings) == 1


# --- remembering results + parallel decode ------------------------------------

import sqlite3
import time


class _SqliteDB:
    """A real database with just the tables the job reads, so the result
    memory is exercised for real."""

    def __init__(self, path):
        self.path = str(path)
        conn = self._get_connection()
        conn.executescript("""
            CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT, artist_id INTEGER,
                                 album_id INTEGER, file_path TEXT);
            INSERT INTO artists VALUES (1, 'Artist');
            INSERT INTO albums VALUES (1, 'Album');
        """)
        conn.commit()
        conn.close()

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_track(self, track_id, path):
        conn = self._get_connection()
        conn.execute("INSERT INTO tracks VALUES (?, ?, 1, 1, ?)", (track_id, f"Track {track_id}", str(path)))
        conn.commit()
        conn.close()

    def drop_track(self, track_id):
        conn = self._get_connection()
        conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        conn.commit()
        conn.close()

    def remembered(self):
        conn = self._get_connection()
        rows = {r[0] for r in conn.execute("SELECT path FROM audio_integrity_checks")}
        conn.close()
        return rows


def _library(tmp_path, count=3):
    db = _SqliteDB(tmp_path / "library.db")
    files = []
    for n in range(1, count + 1):
        f = tmp_path / f"{n:02d} - Song.flac"
        f.write_bytes(b"x" * n)
        db.add_track(n, f)
        files.append(str(f))
    return db, files


def _run(db, tmp_path, monkeypatch, verdicts=None, settings=None):
    """Run a scan; returns (result, findings, the paths that were decoded)."""
    decoded = []
    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p, **kw: p)

    def fake_check(path):
        decoded.append(path)
        return (verdicts or {}).get(path, (True, ""))

    monkeypatch.setattr(mod, "check_flac_integrity", fake_check)
    cfg = MagicMock()
    values = settings or {}
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    findings = []
    ctx = JobContext(db=db, transfer_folder=str(tmp_path), config_manager=cfg,
                     create_finding=lambda **kw: (findings.append(kw) or True))
    result = AudioCorruptionDetectorJob().scan(ctx)
    return result, findings, decoded


def test_a_file_that_passed_is_not_decoded_again_until_it_changes(tmp_path, monkeypatch):
    db, files = _library(tmp_path)
    _, _, first = _run(db, tmp_path, monkeypatch)
    assert sorted(first) == sorted(files)

    _, _, second = _run(db, tmp_path, monkeypatch)
    assert second == []

    with open(files[1], "ab") as f:
        f.write(b"retagged")
    _, _, third = _run(db, tmp_path, monkeypatch)
    assert third == [files[1]]


def test_a_damaged_file_is_tested_every_run_and_never_remembered(tmp_path, monkeypatch):
    db, files = _library(tmp_path)
    bad = {files[0]: (False, "MD5 mismatch")}
    _, findings, _ = _run(db, tmp_path, monkeypatch, verdicts=bad)
    assert [f["entity_id"] for f in findings] == ["1"]
    assert files[0] not in db.remembered()

    _, _, again = _run(db, tmp_path, monkeypatch, verdicts=bad)
    assert again == [files[0]]


def test_a_pass_older_than_recheck_after_days_is_decoded_again(tmp_path, monkeypatch):
    db, files = _library(tmp_path)
    _run(db, tmp_path, monkeypatch)
    conn = db._get_connection()
    conn.execute("UPDATE audio_integrity_checks SET checked_at = 0 WHERE path = ?", (files[2],))
    conn.commit()
    conn.close()
    _, _, decoded = _run(db, tmp_path, monkeypatch)
    assert decoded == [files[2]]


def test_recheck_after_zero_days_decodes_everything_every_run(tmp_path, monkeypatch):
    db, files = _library(tmp_path)
    key = "repair.jobs.audio_corruption_detector.settings"
    _run(db, tmp_path, monkeypatch, settings={key: {"recheck_after_days": 0}})
    _, _, decoded = _run(db, tmp_path, monkeypatch, settings={key: {"recheck_after_days": 0}})
    assert sorted(decoded) == sorted(files)


def test_the_tools_page_settings_dict_is_honoured(tmp_path, monkeypatch):
    # The Tools page saves settings as one dict; the job used to read only a
    # flat key, so "only modified within days" set in the UI never applied.
    db, files = _library(tmp_path)
    old = time.time() - 30 * 86400
    for path in files[:2]:
        os.utime(path, (old, old))
    key = "repair.jobs.audio_corruption_detector.settings"
    _, _, decoded = _run(db, tmp_path, monkeypatch,
                         settings={key: {"only_modified_within_days": 7}})
    assert decoded == [files[2]]


def test_several_workers_decode_every_file_and_flag_only_the_bad_one(tmp_path, monkeypatch):
    db, files = _library(tmp_path, count=12)
    key = "repair.jobs.audio_corruption_detector.settings"
    result, findings, decoded = _run(db, tmp_path, monkeypatch,
                                     verdicts={files[5]: (False, "frame CRC mismatch")},
                                     settings={key: {"workers": 4}})
    assert sorted(decoded) == sorted(files)
    assert [f["entity_id"] for f in findings] == ["6"]
    assert result.findings_created == 1
    assert db.remembered() == set(files) - {files[5]}


def test_a_full_pass_forgets_files_that_left_the_library(tmp_path, monkeypatch):
    db, files = _library(tmp_path)
    _run(db, tmp_path, monkeypatch)
    db.drop_track(3)
    _run(db, tmp_path, monkeypatch)
    assert db.remembered() == set(files[:2])


def test_a_run_stopped_partway_keeps_what_it_already_decoded(tmp_path, monkeypatch):
    db, files = _library(tmp_path, count=120)
    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p, **kw: p)
    decoded = []
    monkeypatch.setattr(mod, "check_flac_integrity", lambda p: decoded.append(p) or (True, ""))
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: (
        {"workers": 1} if key.endswith(".settings") else default)
    ctx = JobContext(db=db, transfer_folder=str(tmp_path), config_manager=cfg,
                     should_stop=lambda: len(decoded) >= 75)

    AudioCorruptionDetectorJob().scan(ctx)

    assert 75 <= len(decoded) < 120
    assert db.remembered() == set(decoded)          # nothing decoded was lost


def test_an_unreachable_library_does_not_wipe_the_memory(tmp_path, monkeypatch):
    db, files = _library(tmp_path)
    _run(db, tmp_path, monkeypatch)
    assert db.remembered() == set(files)

    monkeypatch.setattr(mod, "_decoder_available", lambda: True)
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p, **kw: None)
    monkeypatch.setattr(mod.os.path, "isfile", lambda p: False)
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: default
    AudioCorruptionDetectorJob().scan(JobContext(db=db, transfer_folder=str(tmp_path),
                                                 config_manager=cfg))
    assert db.remembered() == set(files)


def test_the_finding_says_the_file_is_quarantined_not_deleted(tmp_path, monkeypatch):
    db, files = _library(tmp_path, count=1)
    _, findings, _ = _run(db, tmp_path, monkeypatch, verdicts={files[0]: (False, "bad")})
    assert "deleted-files folder" in findings[0]["description"]
    assert "DELETES" not in AudioCorruptionDetectorJob.help_text


def test_the_summary_counts_a_reconfirmed_corrupt_file(tmp_path, monkeypatch):
    # A second run re-tests a damaged file whose finding already exists: no NEW
    # finding, but it is still corrupt and the summary must say so.
    db, files = _library(tmp_path, count=2)
    bad = {files[0]: (False, "bad")}
    _run(db, tmp_path, monkeypatch, verdicts=bad)

    lines = []
    monkeypatch.setattr(mod.logger, "info", lambda msg, *args: lines.append(msg % args))
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: default
    ctx = JobContext(db=db, transfer_folder=str(tmp_path), config_manager=cfg,
                     create_finding=lambda **kw: False)          # already has a finding
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert result.findings_created == 0
    summary = [line for line in lines if "decode-tested" in line][-1]
    assert "1 corrupt (0 new findings)" in summary
