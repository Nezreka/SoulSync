"""a scoped maintenance run doesn't pick up every loose file on disk.

the lossy converter, ReplayGain and the AAC fixer add unindexed files from a
walk of the library folders. a run scoped to one artist got every loose file
too, and Fix All applied them (the converter with delete-original).
"""

from __future__ import annotations

from types import SimpleNamespace

from core.repair_jobs import filesystem_subjects


def _context(tmp_path, scope):
    return SimpleNamespace(db=None, config_manager=None, scope=scope,
                           transfer_folder=str(tmp_path), check_stop=lambda: False)


def test_a_scoped_run_keeps_to_its_files(tmp_path, monkeypatch):
    (tmp_path / "loose.flac").write_bytes(b"x")
    (tmp_path / "named.flac").write_bytes(b"x")
    monkeypatch.setattr(filesystem_subjects, "repair_scan_roots", lambda context: [str(tmp_path)])
    whole = filesystem_subjects.filesystem_audio_files(_context(tmp_path, None), include_indexed=True)
    assert len(whole) == 2
    scoped = filesystem_subjects.filesystem_audio_files(
        _context(tmp_path, {"file_paths": [str(tmp_path / "named.flac")]}), include_indexed=True)
    assert scoped == [str(tmp_path / "named.flac")]
