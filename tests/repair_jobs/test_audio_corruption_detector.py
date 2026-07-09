"""Corrupt File Detector (#1000): decode-test library FLACs, flag damaged ones
to delete + re-download. The scan only creates findings — never deletes.

Covered:
* check_flac_integrity: clean → ok, non-zero decode → flagged, no decoder → never
  flags (a false positive would delete a good file), ffmpeg fallback.
* scan: corrupt file → one 'corrupt_audio' finding on the track; clean file →
  none; non-FLAC ignored; "modified within N days" narrows; no decoder → no-op.
"""

from __future__ import annotations

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
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p: p)
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
    monkeypatch.setattr(mod, "resolve_library_file_path", lambda p: p)
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
