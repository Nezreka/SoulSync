"""Regression: the Lossy Converter fix action surfaces the REAL ffmpeg error in
the finding result shown in the UI notification (issue #995), not ffmpeg's
version banner.

Before the fix, ``_fix_missing_lossy_copy`` returned ``proc.stderr[:200]`` — the
leading banner — so every failed conversion notification read as an identical
"ffmpeg version 7.x ... configuration: ..." blob with no actionable reason.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from core.repair_worker import RepairWorker


# Faithful ffmpeg 7.x failure: banner first, real reason (missing libopus) last.
BANNER_MISSING_ENCODER = (
    "ffmpeg version 7.1.5-0+deb13u1 Copyright (c) 2000-2026 the FFmpeg developers\n"
    "  built with gcc 14 (Debian 14.2.0-19)\n"
    "  configuration: --prefix=/usr --enable-gpl --enable-libmp3lame\n"
    "  libavutil      59.  8.100 / 59.  8.100\n"
    "Input #0, flac, from '/music/track.flac':\n"
    "  Stream #0:0: Audio: flac, 44100 Hz, stereo, s16\n"
    "[aost#0:0 @ 0x55d0aa] Unknown encoder 'libopus'\n"
    "[aost#0:0 @ 0x55d0aa] Error selecting an encoder\n"
    "Error opening output file /music/track.opus.\n"
    "Error opening output files: Encoder not found\n"
)


def _worker(tmp_path: Path) -> RepairWorker:
    worker = RepairWorker(database=SimpleNamespace())
    cfg = SimpleNamespace()
    values = {
        "lossy_copy.codec": "opus",
        "lossy_copy.bitrate": "256",
        "soulseek.download_path": "",
        "repair.jobs.lossy_converter.settings": {},
    }
    cfg.get = lambda key, default=None: values.get(key, default)
    worker._config_manager = cfg
    worker.transfer_folder = str(tmp_path)
    return worker


def test_fix_surfaces_real_ffmpeg_error_not_banner(tmp_path, monkeypatch):
    flac = tmp_path / "01 - Track.flac"
    flac.write_bytes(b"x")

    monkeypatch.setattr(
        "core.quality.selection.load_profile_by_id",
        lambda _profile_id: {
            "lossy_copy_enabled": True,
            "lossy_copy_codec": "opus",
            "lossy_copy_bitrate": "256",
            "lossy_copy_delete_original": False,
        },
    )
    monkeypatch.setattr(shutil, "which", lambda _: "/fake/ffmpeg")
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stderr=BANNER_MISSING_ENCODER, stdout=""),
    )

    result = _worker(tmp_path)._fix_missing_lossy_copy("track", "1", str(flac), {})

    assert result["success"] is False
    err = result["error"]
    # Actionable reason present...
    assert "Unknown encoder 'libopus'" in err
    assert "Encoder not found" in err
    # ...banner absent.
    assert "ffmpeg version" not in err
    assert "configuration:" not in err


def test_fix_resolves_unassigned_track_against_live_default(tmp_path, monkeypatch):
    resolved_ids = []

    def _load(profile_id):
        resolved_ids.append(profile_id)
        return {
            "id": 99,
            "name": "New Default",
            "lossy_copy_enabled": False,
        }

    monkeypatch.setattr("core.quality.selection.load_profile_by_id", _load)

    result = _worker(tmp_path)._fix_missing_lossy_copy(
        "track", "1", str(tmp_path / "track.flac"),
        {"quality_profile_id": None},
    )

    assert resolved_ids == [None]
    assert result == {
        "success": False,
        "error": "Lossy Copy is disabled for this track profile",
    }


def test_fix_re_resolves_native_track_profile_at_apply_time(tmp_path, monkeypatch):
    resolved_track_ids = []

    class _Conn:
        def close(self):
            pass

    def _effective(_conn, track_id):
        resolved_track_ids.append(track_id)
        return {
            "id": 101,
            "name": "New inherited default",
            "lossy_copy_enabled": False,
        }

    monkeypatch.setattr(
        "core.library2.quality_eval.effective_track_profile", _effective,
    )
    monkeypatch.setattr(
        "core.quality.selection.load_profile_by_id",
        lambda _profile_id: (_ for _ in ()).throw(
            AssertionError("native findings must use the live Library-v2 cascade")
        ),
    )
    worker = _worker(tmp_path)
    worker.db = SimpleNamespace(_get_connection=lambda: _Conn())

    result = worker._fix_missing_lossy_copy(
        "track", "lib2:42", str(tmp_path / "track.flac"),
        {
            "quality_profile_id": 77,
            "library_v2": {"track_id": 42},
        },
    )

    assert resolved_track_ids == [42]
    assert result == {
        "success": False,
        "error": "Lossy Copy is disabled for this track profile",
    }
