"""third #1062 review G12-6 / G12-7: a failure while listing the files."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from core.repair_jobs.base import JobContext


def _boom(*_a, **_k):
    raise RuntimeError("database is locked")


def test_replaygain_filler_counts_a_failed_file_list(monkeypatch):
    """G12-6: it logged a warning and reported a clean run that checked
    nothing; 3.6.1 counted the error"""
    from core.repair_jobs.replaygain_filler import ReplayGainFillerJob
    monkeypatch.setattr("core.replaygain.is_ffmpeg_available", lambda: True)
    monkeypatch.setattr("core.library2.maintenance_subjects.active_file_subjects", _boom)
    ctx = JobContext(db=SimpleNamespace(), transfer_folder="", config_manager=None)
    result = ReplayGainFillerJob().scan(ctx)
    assert result.errors == 1
    assert result.scanned == 0


def test_short_preview_keeps_the_length_when_caching_it_fails(monkeypatch):
    """G12-7: one locked cache write ended the enumeration, and every later
    file was never checked for a preview"""
    from core.repair_jobs.short_preview_track import ShortPreviewTrackJob

    class _Audio:
        info = SimpleNamespace(length=29.5)

    monkeypatch.setitem(sys.modules, "mutagen", SimpleNamespace(File=lambda _p: _Audio()))
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda p, *_a, **_k: p)
    db = SimpleNamespace(_get_connection=_boom)
    ctx = JobContext(db=db, transfer_folder="", config_manager=None)
    subject = {"file_id": 7, "path": "/m/clip.flac", "tags_json": "{}"}
    assert ShortPreviewTrackJob()._file_ms(subject, ctx) == 29500
