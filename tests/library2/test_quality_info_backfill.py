"""Tests for the Quality Info Backfill job (review A4, clarified): library
entries whose sample_rate/bit_depth was never probed (legacy imports only
ever got format+bitrate) must be found and re-measured automatically.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.repair_jobs.base import JobContext
from core.repair_jobs.quality_info_backfill import (
    QualityInfoBackfillJob,
    _candidate_file_ids,
)


class _DB:
    def __init__(self, path: str) -> None:
        self.path = path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class _Config:
    def __init__(self, library_v2: bool = True) -> None:
        self.library_v2 = library_v2

    def get(self, key, default=None):
        if key == "features.library_v2":
            return self.library_v2
        return default


def _db_path(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()[2]


def test_legacy_imported_row_is_a_candidate(imported_conn):
    """The importer only ever seeds format+bitrate (docs core.library2.scan)
    — a never-refreshed row must be picked up as needing a re-probe."""
    conn = imported_conn
    row = conn.execute(
        "SELECT sample_rate, bit_depth, format FROM lib2_track_files WHERE path='/m/01.flac'"
    ).fetchone()
    assert row["sample_rate"] is None
    assert row["bit_depth"] is None
    assert row["format"] == "flac"

    candidates = _candidate_file_ids(conn)
    file_id = conn.execute(
        "SELECT id FROM lib2_track_files WHERE path='/m/01.flac'"
    ).fetchone()[0]
    assert file_id in candidates


def test_fully_probed_row_is_not_a_candidate(imported_conn):
    conn = imported_conn
    conn.execute(
        "UPDATE lib2_track_files SET sample_rate=44100, bit_depth=16 WHERE path='/m/01.flac'"
    )
    conn.commit()

    file_id = conn.execute(
        "SELECT id FROM lib2_track_files WHERE path='/m/01.flac'"
    ).fetchone()[0]
    assert file_id not in _candidate_file_ids(conn)


def test_lossy_row_missing_bit_depth_is_not_a_candidate(imported_conn):
    """bit_depth is legitimately NULL forever for lossy formats — must never
    be mistaken for "never probed" (that would loop the job forever)."""
    conn = imported_conn
    conn.execute(
        "UPDATE lib2_track_files SET format='mp3', sample_rate=44100, bit_depth=NULL "
        "WHERE path='/m/01.flac'"
    )
    conn.commit()

    file_id = conn.execute(
        "SELECT id FROM lib2_track_files WHERE path='/m/01.flac'"
    ).fetchone()[0]
    assert file_id not in _candidate_file_ids(conn)


def test_scan_reprobes_and_persists_missing_quality_facts(imported_conn, tmp_path, monkeypatch):
    conn = imported_conn
    db_path = _db_path(conn)
    real_file = tmp_path / "one-dance.flac"
    real_file.write_bytes(b"not real audio; probe is mocked")

    from core.quality.model import AudioQuality

    monkeypatch.setattr(
        "core.library2.paths.resolve_lib2_path", lambda _path: str(real_file)
    )
    monkeypatch.setattr(
        "core.imports.file_ops.probe_audio_quality",
        lambda _path: AudioQuality(format="flac", bitrate=1000, sample_rate=96000, bit_depth=24),
    )
    monkeypatch.setattr(
        "core.tag_writer.read_file_tags",
        lambda _path: {"error": "unreadable"},
    )

    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=_Config(library_v2=True),
    )

    result = QualityInfoBackfillJob().scan(context)

    # The fixture's "One Dance" single (path /m/single.flac) is also a
    # legacy-imported, never-probed row and gets swept up too — this test
    # only cares that /m/01.flac's specific row was correctly backfilled.
    assert result.auto_fixed >= 1
    row = conn.execute(
        "SELECT sample_rate, bit_depth, quality_tier FROM lib2_track_files WHERE path='/m/01.flac'"
    ).fetchone()
    assert row["sample_rate"] == 96000
    assert row["bit_depth"] == 24
    assert row["quality_tier"] == "lossless_hi"


def test_scan_is_noop_when_no_candidates(imported_conn, tmp_path):
    conn = imported_conn
    conn.execute(
        "UPDATE lib2_track_files SET sample_rate=44100, bit_depth=16"
    )
    conn.commit()

    context = JobContext(
        db=_DB(_db_path(conn)),
        transfer_folder=str(tmp_path),
        config_manager=_Config(library_v2=True),
    )

    result = QualityInfoBackfillJob().scan(context)

    assert result.auto_fixed == 0
    assert result.scanned == 0


def test_deprecated_disable_flag_does_not_gate_scan(imported_conn, tmp_path):
    """features.library_v2 is a non-disableable cutover (core.library2.feature):
    the legacy false value is read only to log one deprecation warning and
    must not silence the native repair scan."""
    context = JobContext(
        db=_DB(_db_path(imported_conn)),
        transfer_folder=str(tmp_path),
        config_manager=_Config(library_v2=False),
    )

    result = QualityInfoBackfillJob().scan(context)

    assert result.scanned == 2
