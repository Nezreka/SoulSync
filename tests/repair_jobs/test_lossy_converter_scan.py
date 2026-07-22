"""Regression tests for the Lossy Converter finder (issue #995, the "does not
find all lossless files" half).

The scan silently ``continue``d past files whose DB path could not be located on
disk, so a library with unresolved paths looked like the job had simply missed
lossless files. The scan now COUNTS those skips and surfaces them in the
completion line. These tests pin:

* a FLAC without a lossy sibling still produces a finding (unchanged behavior),
* a FLAC that already has the lossy sibling produces none (unchanged behavior),
* a DB row whose file is missing on disk is counted + surfaced, not silently dropped.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.repair_jobs.base import JobContext
from core.repair_jobs.lossy_converter import LossyConverterJob


@pytest.fixture(autouse=True)
def _native_subject_boundary(monkeypatch):
    """Feed the scanner native subject rows; it must never query old tracks."""

    def subjects(database, _config_manager, **_kwargs):
        result = []
        for row in database._rows:
            track_id, title, artist, album, path, album_image, artist_image = row
            result.append({
                "file_id": track_id,
                "track_id": track_id,
                "album_id": 1,
                "artist_id": 1,
                "title": title,
                "artist_name": artist,
                "album_title": album,
                "path": path,
                "album_image": album_image,
                "artist_image": artist_image,
                "track_source_ids": {},
                "album_source_ids": {},
                "artist_source_ids": {},
            })
        return result

    monkeypatch.setattr(
        "core.library2.maintenance_subjects.active_file_subjects", subjects
    )


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
    # (t.id, t.title, ar.name, al.title, t.file_path, al.thumb_url, ar.thumb_url)
    return (track_id, title, "Artist", "Album", path, None, None)


def _context(rows, tmp_path: Path):
    cfg = MagicMock()
    values = {
        "features.library_v2": True,
        "lossy_copy.enabled": True,
        "lossy_copy.codec": "opus",
        "lossy_copy.bitrate": "256",
        "soulseek.download_path": "",
    }
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)

    findings = []

    def create_finding(**kwargs):
        findings.append(kwargs)
        return True  # inserted

    progress_lines = []

    def report_progress(**kwargs):
        if kwargs.get("log_line"):
            progress_lines.append(kwargs["log_line"])

    ctx = JobContext(
        db=_FakeDB(rows),
        transfer_folder=str(tmp_path),
        config_manager=cfg,
        create_finding=create_finding,
        report_progress=report_progress,
    )
    return ctx, findings, progress_lines


def test_finds_flac_without_lossy_sibling(tmp_path: Path):
    flac = tmp_path / "01 - Song.flac"
    flac.write_bytes(b"x")

    ctx, findings, _ = _context([_row(1, "Song", str(flac))], tmp_path)
    result = LossyConverterJob().scan(ctx)

    assert result.findings_created == 1
    assert findings[0]["finding_type"] == "missing_lossy_copy"


def test_skips_flac_that_already_has_opus_sibling(tmp_path: Path):
    flac = tmp_path / "02 - Song.flac"
    flac.write_bytes(b"x")
    (tmp_path / "02 - Song.opus").write_bytes(b"x")  # lossy copy already present

    ctx, findings, _ = _context([_row(2, "Song", str(flac))], tmp_path)
    result = LossyConverterJob().scan(ctx)

    assert result.findings_created == 0
    assert findings == []


def test_missing_on_disk_is_counted_and_surfaced_not_silently_dropped(tmp_path: Path):
    present = tmp_path / "03 - Present.flac"
    present.write_bytes(b"x")
    missing_path = str(tmp_path / "does_not_exist" / "04 - Gone.flac")

    ctx, findings, progress_lines = _context(
        [_row(3, "Present", str(present)), _row(4, "Gone", missing_path)],
        tmp_path,
    )
    result = LossyConverterJob().scan(ctx)

    # The present file still yields a finding; the missing one does not vanish
    # without a trace — the completion line reports it.
    assert result.findings_created == 1
    completion = progress_lines[-1]
    assert "could not be located on disk" in completion
    assert "1 tracks" in completion
