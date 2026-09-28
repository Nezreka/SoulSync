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


def _seed(db: MusicDatabase):
    conn = db._get_connection()
    conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar1', 'A-ha')")
    conn.execute("INSERT INTO albums (id, artist_id, title) VALUES ('al1', 'ar1', 'Hunting High and Low')")
    conn.commit()
    conn.close()


def _track(db: MusicDatabase, tid: int, path: str, **kw):
    conn = db._get_connection()
    cols = ["id", "artist_id", "album_id", "title", "duration", "file_path",
            "spotify_track_id", "track_number"]
    vals = [tid, "ar1", "al1", f"Track {tid}", 200_000, str(path), "sp1",
            kw.get("track_number")]
    conn.execute(
        f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        vals,
    )
    conn.commit()
    conn.close()


def _worker(db, transfer: Path, config=None) -> RepairWorker:
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(transfer)
    w._config_manager = config
    return w


def _track_number(db: MusicDatabase, tid: int):
    conn = db._get_connection()
    row = conn.execute("SELECT track_number FROM tracks WHERE id = ?", (tid,)).fetchone()
    conn.close()
    return row[0] if row else None


# ── H5: _fix_track_number must not write the DB when the file is missing ──

def test_h5_track_number_fix_does_not_write_db_when_file_missing(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db)
    _track(db, 42, "/nonexistent/does-not-exist-xyz-123.flac", track_number=1)

    res = _worker(db, tmp_path)._fix_track_number(
        "track", 42, "/nonexistent/does-not-exist-xyz-123.flac",
        {"correct_track_num": 7},
    )

    assert res["success"] is False
    assert "File not found" in res["error"]
    # The fix failed, so the DB must still say track 1 — not 7.
    assert _track_number(db, 42) == 1


def test_h5_track_number_fix_still_writes_db_when_file_present(tmp_path: Path):
    """Sanity: the moved DB write still happens on the success path."""
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db)
    f = tmp_path / "01 - Song.flac"
    f.write_bytes(b"fake audio")
    _track(db, 42, str(f), track_number=1)

    res = _worker(db, tmp_path)._fix_track_number(
        "track", 42, str(f), {"correct_track_num": 7, "tag_ok": True},
    )

    assert res["success"] is True
    assert _track_number(db, 42) == 7


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


# ── M9: Dead File Cleaner + Short Preview Track must honor UI-saved settings ──

def _m9_ctx(values):
    return SimpleNamespace(config_manager=_FakeConfig(values))


def test_m9_dead_file_cleaner_reads_nested_settings_dict(tmp_path: Path):
    """set_job_settings() stores {'repair.jobs.dead_file_cleaner.settings': {...}};
    the job must see those values, not its defaults."""
    job = DeadFileCleanerJob()
    ctx = _m9_ctx({
        "repair.jobs.dead_file_cleaner.settings": {
            "max_unresolved_fraction": 0.05,
            "min_tracks_for_guard": 10,
        }
    })
    assert job._setting_float(ctx, "max_unresolved_fraction", 0.5) == 0.05
    assert job._setting_int(ctx, "min_tracks_for_guard", 25) == 10


def test_m9_dead_file_cleaner_flat_key_still_works_as_fallback():
    job = DeadFileCleanerJob()
    ctx = _m9_ctx({"repair.jobs.dead_file_cleaner.max_unresolved_fraction": 0.1})
    assert job._setting_float(ctx, "max_unresolved_fraction", 0.5) == 0.1
    # untouched keys keep their defaults
    assert job._setting_int(ctx, "min_tracks_for_guard", 25) == 25


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

def _raising_on_update_db(db: MusicDatabase):
    """Wrap db._get_connection so the UPDATE raises (locked DB), everything
    else works — the proving-test fault injection from the review."""
    real_get = db._get_connection

    class _FailCursor:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, sql, params=()):
            if isinstance(sql, str) and sql.strip().upper().startswith("UPDATE"):
                raise sqlite3.OperationalError("database is locked")
            return self._cur.execute(sql, params)

    class _FailConn:
        def __init__(self, conn):
            self._conn = conn

        def cursor(self):
            return _FailCursor(self._conn.cursor())

        def commit(self):
            return self._conn.commit()

        def close(self):
            return self._conn.close()

    def raising_get():
        return _FailConn(real_get())

    db._get_connection = raising_get
    return real_get


def test_s1_lossy_converter_db_failure_keeps_source_and_reports_failure(
        tmp_path: Path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db)
    src = tmp_path / "01 - Song.flac"
    src.write_bytes(b"fake flac bytes")
    _track(db, 1, str(src))

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
    real_get = _raising_on_update_db(db)

    res = _worker(db, tmp_path)._fix_missing_lossy_copy("track", 1, str(src), {})

    assert res["success"] is False
    # The source file must survive: the DB update failed before any delete.
    assert src.exists()
    conn = real_get()
    row = conn.execute("SELECT file_path FROM tracks WHERE id = 1").fetchone()
    conn.close()
    assert row[0] == str(src)


# ── S2: single/EP dedup + unwanted-content must not commit DB deletes when the
#        file delete fails ──

def _fail_remove(monkeypatch):
    def _raise(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(os, "remove", _raise)


def test_s2_single_dedup_file_delete_failure_keeps_db_row(tmp_path: Path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db)
    single = tmp_path / "single.flac"
    single.write_bytes(b"fake")
    _track(db, 1, str(single))
    _track(db, 2, str(tmp_path / "album.flac"))
    _fail_remove(monkeypatch)

    res = _worker(db, tmp_path)._fix_single_album_redundant(
        "track", 1, str(single),
        {"single_track": {"id": 1, "file_path": str(single)},
         "album_track": {"id": 2, "album": "Hunting High and Low"}},
    )

    assert res["success"] is False
    assert single.exists()
    conn = db._get_connection()
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id = 1").fetchone()[0] == 1
    conn.close()


def test_s2_unwanted_content_file_delete_failure_keeps_db_row(tmp_path: Path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db)
    live = tmp_path / "live.flac"
    live.write_bytes(b"fake")
    _track(db, 1, str(live))
    _fail_remove(monkeypatch)

    res = _worker(db, tmp_path)._fix_unwanted_content(
        "track", 1, str(live),
        {"track": {"id": 1, "file_path": str(live), "album_id": "al1"},
         "type_label": "Live"},
    )

    assert res["success"] is False
    assert live.exists()
    conn = db._get_connection()
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id = 1").fetchone()[0] == 1
    # the album row must survive too (it is only cleaned up after a real removal)
    assert conn.execute("SELECT COUNT(*) FROM albums WHERE id = 'al1'").fetchone()[0] == 1
    conn.close()


# ── S3: preview-clip / corrupt-audio fixes must report failure when the file
#        removal/quarantine fails (finding stays actionable) ──

def test_s3_preview_clip_delete_failure_returns_failure_and_keeps_row(
        tmp_path: Path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db)
    preview = tmp_path / "preview.flac"
    preview.write_bytes(b"fake audio bytes")
    _track(db, 1, str(preview), track_number=3)
    wished = []
    db.add_to_wishlist = lambda data, **kw: wished.append(data) or True
    monkeypatch.setattr(
        "core.repair_worker._delete_file_if_present",
        lambda *a, **k: (False, "permission denied"),
    )

    res = _worker(db, tmp_path)._fix_short_preview_track(
        "track", "1", str(preview), {"expected_duration_s": 225.0},
    )

    assert res["success"] is False
    assert "permission denied" in res["error"]
    assert preview.exists()
    conn = db._get_connection()
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id = 1").fetchone()[0] == 1
    conn.close()


def test_s3_preview_clip_already_gone_still_succeeds(tmp_path: Path):
    """The benign case (file already gone) keeps the old success behavior."""
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db)
    _track(db, 1, str(tmp_path / "gone.flac"))
    db.add_to_wishlist = lambda data, **kw: True

    res = _worker(db, tmp_path)._fix_short_preview_track(
        "track", "1", str(tmp_path / "gone.flac"), {},
    )

    assert res["success"] is True


def test_s3_corrupt_audio_quarantine_failure_returns_failure_and_keeps_row(
        tmp_path: Path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed(db)
    damaged = tmp_path / "01 - Song.flac"
    damaged.write_bytes(b"damaged audio bytes")
    _track(db, 1, str(damaged))
    db.add_to_wishlist = lambda data, **kw: True
    monkeypatch.setattr(
        "core.repair_worker._quarantine_file_if_present",
        lambda *a, **k: (False, "permission denied", None),
    )

    res = _worker(db, tmp_path)._fix_corrupt_audio("track", "1", str(damaged), {})

    assert res["success"] is False
    assert "permission denied" in res["error"]
    assert damaged.exists()
    conn = db._get_connection()
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id = 1").fetchone()[0] == 1
    conn.close()
