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

import pytest

import core.repair_jobs.audio_corruption_detector as mod
from core.repair_jobs.audio_corruption_detector import (
    AudioCorruptionDetectorJob,
    check_flac_integrity,
)
from core.repair_jobs.base import JobContext


@pytest.fixture(autouse=True)
def _native_subject_boundary(monkeypatch):
    """Feed the scanner native subject rows; it must never query old tracks."""

    def subjects(database, _config_manager, **_kwargs):
        return [
            {
                "file_id": row["id"],
                "track_id": row["id"],
                "album_id": 1,
                "artist_id": 1,
                "title": row["title"],
                "artist_name": row["artist_name"],
                "album_title": row["album_title"],
                "path": row["file_path"],
                "track_source_ids": {},
                "album_source_ids": {},
                "artist_source_ids": {},
            }
            for row in database._rows
        ]

    monkeypatch.setattr(
        "core.library2.maintenance_subjects.active_file_subjects", subjects
    )


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
    assert f["entity_type"] == "track" and f["entity_id"] == "lib2:7"
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


def test_a_file_outside_the_catalogue_is_not_promised_a_re_download(tmp_path, monkeypatch):
    """The walk also finds audio no lib2 row points at. Those findings carry
    `entity_type='file'` and no id, so there is no track to put back on the
    wishlist — and the copy must not say there is. The reported symptom was a
    row reading "approve to delete it and re-download the real version" whose
    fix could only answer "No track ID associated with this finding"."""
    stray = tmp_path / "EKKSTACY" / "NEGATIVE" / "01 - i walk this earth.flac"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x")
    _prep(monkeypatch, {str(stray): (False, "LOST_SYNC after processing 6418432 samples")})

    ctx, findings = _context([], tmp_path)
    result = AudioCorruptionDetectorJob().scan(ctx)

    assert result.findings_created == 1
    f = findings[0]
    assert f["entity_type"] == "file" and f["entity_id"] is None
    assert "LOST_SYNC" in f["description"]
    assert "re-download" not in f["description"].lower()
    assert "delete" in f["description"].lower()


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
