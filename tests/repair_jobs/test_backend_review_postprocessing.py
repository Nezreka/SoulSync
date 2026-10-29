"""Regression tests for the backend-review post-processing findings (H5, H6, M9,
M10, L5, S1, S2, S3).

Each test proves the bug on unfixed code first (written before the fix), then
guards the fixed behavior. Conventions follow the neighboring repair-job tests:
real temp MusicDatabase, ``RepairWorker.__new__`` with stubbed attributes.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
from core.repair_jobs.base import JobContext
from core.repair_jobs.dead_file_cleaner import DeadFileCleanerJob
from core.repair_jobs.short_preview_track import ShortPreviewTrackJob
from core.repair_jobs.suspect_album_tag import _COMPILATION_PATTERNS
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


# ── shared helpers ──

class _FakeConfig:
    """Dict-backed config manager: get/set only, like the real one for these keys."""

    def __init__(self, values=None):
        self._values = dict(values or {})

    def get(self, key, default=None):
        return self._values.get(key, default)

    def set(self, key, value):
        self._values[key] = value


def _track(db: MusicDatabase, path: str) -> int:
    """One Library v2 track with its file (the catalogue on this branch)."""
    from tests.lib2_seed import track
    conn = db._get_connection()
    try:
        tid = track(conn, "A-ha", "Hunting High and Low", "Track 1", path=str(path))
        conn.commit()
    finally:
        conn.close()
    return tid


def _worker(db, transfer: Path, config=None) -> RepairWorker:
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(transfer)
    w._config_manager = config
    return w


# H5 (track number written only once the file is proven) is this branch's
# iss29-E10 and is covered in tests/library2/test_maintenance_sync.py.

# ── H6: AcoustID checkpoint must be the last COMPLETED track, not the next one ──

def _acoustid_run(tmp_path: Path, cfg: _FakeConfig, stop_during: set, scanned: list):
    """One scan run over 3 stubbed tracks; stop is requested while _scan_file
    processes any track in ``stop_during``."""
    job = AcoustIDScannerJob()
    files = {}
    for tid in (1, 2, 3):
        p = tmp_path / f"t{tid}.flac"
        p.write_bytes(b"fake")
        files[tid] = {"file_path": str(p)}
    job._load_db_tracks = lambda _ctx: dict(files)

    stop_flag = {"stop": False}

    def fake_scan(_fpath, track_id, _expected, _client, _ctx, _result,
                  _fp, _title, _artist):
        scanned.append(track_id)
        if track_id in stop_during:
            stop_flag["stop"] = True

    job._scan_file = fake_scan
    ctx = JobContext(
        db=None,
        transfer_folder=str(tmp_path),
        config_manager=cfg,
        acoustid_client=object(),  # no is_available probe -> treated as usable
        should_stop=lambda: stop_flag["stop"],
    )
    return job.scan(ctx)


def test_h6_acoustid_checkpoint_is_last_completed_track(tmp_path: Path):
    cfg = _FakeConfig()
    scanned = []
    _acoustid_run(tmp_path, cfg, stop_during={1}, scanned=scanned)

    assert scanned == [1]
    # Track 1 finished; the stop was noticed at the top of track 2's iteration.
    # The checkpoint must be 1 (done), not 2 (about to start).
    assert str(cfg.get("repair.jobs.acoustid_scanner.checkpoint_id")) == "1"


def test_h6_acoustid_resume_does_not_skip_unprocessed_track(tmp_path: Path):
    cfg = _FakeConfig()
    _acoustid_run(tmp_path, cfg, stop_during={1}, scanned=[])

    resumed = []
    _acoustid_run(tmp_path, cfg, stop_during=set(), scanned=resumed)

    # Track 2 was never fingerprinted in run 1 — it must not be skipped forever.
    assert resumed == [2, 3]


# ── M9: Short Preview Track must honor UI-saved settings (the Library v2 Dead
#        File Cleaner has no settings) ──

def _m9_ctx(values):
    return SimpleNamespace(config_manager=_FakeConfig(values))


def test_m9_short_preview_track_reads_nested_settings_dict():
    job = ShortPreviewTrackJob()
    ctx = _m9_ctx({
        "repair.jobs.short_preview_track.settings": {
            "max_duration_seconds": 90,
            "verify_zero_length": False,
        },
    })
    assert job._setting_int(ctx, "max_duration_seconds", 30) == 90
    assert job._setting_bool(ctx, "verify_zero_length", True) is False


def test_m9_short_preview_track_flat_key_still_works_as_fallback():
    job = ShortPreviewTrackJob()
    ctx = _m9_ctx({"repair.jobs.short_preview_track.max_duration_seconds": 45})
    assert job._setting_int(ctx, "max_duration_seconds", 30) == 45


# ── M10: suspect-album-tag regex must catch multi-digit NOW/Vol compilations ──

def test_m10_compilation_regex_matches_multi_digit_now_and_vol():
    assert _COMPILATION_PATTERNS.search("NOW 45")
    assert _COMPILATION_PATTERNS.search("Now 100")
    assert _COMPILATION_PATTERNS.search("Vol. 12")
    assert _COMPILATION_PATTERNS.search("Vol 12")
    # single-digit still matches, non-compilations still don't
    assert _COMPILATION_PATTERNS.search("Vol 2")
    assert not _COMPILATION_PATTERNS.search("Revolver")


# ── L5: AcoustID "Batch Size" help text must describe the pause, not a cap ──

def test_l5_batch_size_help_text_describes_pause_behavior():
    help_text = AcoustIDScannerJob.help_text
    assert "tracks per scan run" not in help_text
    assert "pause" in help_text.lower()


# ── S1: lossy converter must not delete the source when the DB update fails ──

def test_s1_lossy_converter_db_failure_keeps_source_and_reports_failure(
        tmp_path: Path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    src = tmp_path / "01 - Song.flac"
    src.write_bytes(b"fake flac bytes")
    tid = _track(db, str(src))

    monkeypatch.setattr(
        "core.quality.selection.load_profile_by_id",
        lambda _pid: {
            "lossy_copy_enabled": True,
            "lossy_copy_codec": "mp3",
            "lossy_copy_bitrate": "320",
            "lossy_copy_delete_original": True,
        },
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/ffmpeg")

    def fake_run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"fake mp3 bytes")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "mutagen.File",
        lambda *a, **k: SimpleNamespace(
            tags=SimpleNamespace(add=lambda *a, **k: None), save=lambda: None),
    )

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(RepairWorker, "_record_lossy_replacement", locked)

    res = _worker(db, tmp_path)._fix_missing_lossy_copy("track", f"lib2:{tid}", str(src), {})

    assert res["success"] is False
    # The source file must survive: the catalogue update failed before any delete.
    assert src.exists()
    conn = db._get_connection()
    row = conn.execute(
        "SELECT path, file_state FROM lib2_track_files WHERE track_id = ?", (tid,)).fetchone()
    conn.close()
    assert (row[0], row[1]) == (str(src), "active")


# S2/S3 (a failed file delete keeps the catalogue row and reports failure) are
# the native journaled delete's contract on this branch:
# tests/library2/test_delete_journal.py, tests/repair_jobs/test_corrupt_audio_fix.py.
