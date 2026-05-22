"""Regression tests for the orphan file detector."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.repair_jobs.base import JobContext
from core.repair_jobs.orphan_file_detector import OrphanFileDetectorJob


class _DB:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _get_connection(self):
        return sqlite3.connect(self.path)


def _seed_library(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE artists (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE albums (
                id INTEGER PRIMARY KEY,
                artist_id INTEGER NOT NULL,
                title TEXT NOT NULL
            );
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY,
                album_id INTEGER NOT NULL,
                artist_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                file_path TEXT
            );
            INSERT INTO artists (id, name) VALUES
                (1, 'Clouddead89'),
                (2, 'Featured Artist');
            INSERT INTO albums (id, artist_id, title) VALUES
                (10, 1, 'Perfect Match Error');
            INSERT INTO tracks (id, album_id, artist_id, title, file_path) VALUES
                (100, 10, 2, 'Perfect Match', '/old/prefix/elsewhere.mp3');
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_orphan_detector_accepts_picard_albumartist_folder_match(tmp_path: Path) -> None:
    """Picard paths use albumartist/album (year)/track - title.

    Even when the DB track artist is a featured artist, the album artist
    folder should be enough to recognize the file as tracked.
    """
    db_path = tmp_path / "library.sqlite"
    _seed_library(db_path)

    transfer = tmp_path / "Clouddead89" / "Perfect Match Error (2026)"
    transfer.mkdir(parents=True)
    audio_path = transfer / "01 - Perfect Match.mp3"
    audio_path.write_bytes(b"not a real mp3; filename fallback handles this")

    findings = []
    context = JobContext(
        db=_DB(db_path),
        transfer_folder=str(tmp_path),
        config_manager=None,
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )

    result = OrphanFileDetectorJob().scan(context)

    assert result.scanned == 1
    assert result.findings_created == 0
    assert findings == []
