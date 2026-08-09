"""`AutoImportWorker._is_already_processed` — the folder-level dedup guard.

The guard is keyed on a CONTENT hash (`_compute_folder_hash` = basename:size of
the folder's audio files), so it stops recognising a folder the moment its
contents change — and importing the matched tracks is exactly such a change.
A folder that is only the remnant of a finished import therefore hashed to
something the history had never seen, got identified as a brand-new album, and
was imported a second time. That path matters more now that leftovers are a
normal outcome of a healthy import (PR #1121 review).
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import List
from unittest.mock import MagicMock

import pytest


@dataclass
class _Candidate:
    path: str
    name: str = "Album"
    folder_hash: str = "hash-now"
    audio_files: List[str] = field(default_factory=list)


class _Db:
    """Minimal stand-in exposing the one method the worker uses."""

    def __init__(self, path: str):
        self._path = path
        conn = self._get_connection()
        conn.execute("""
            CREATE TABLE auto_import_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_name TEXT, folder_path TEXT, folder_hash TEXT,
                status TEXT, match_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def _get_connection(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, *, folder_path, folder_hash, status, unmatched=()):
        conn = self._get_connection()
        conn.execute(
            "INSERT INTO auto_import_history (folder_name, folder_path, folder_hash,"
            " status, match_data) VALUES (?, ?, ?, ?, ?)",
            (os.path.basename(folder_path), folder_path, folder_hash, status,
             json.dumps({'unmatched_files': list(unmatched)})),
        )
        conn.commit()
        conn.close()


@pytest.fixture
def worker(tmp_path):
    from core.auto_import_worker import AutoImportWorker

    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: default
    w = AutoImportWorker(
        database=_Db(str(tmp_path / "history.db")),
        staging_path=str(tmp_path),
        transfer_path=str(tmp_path / "transfer"),
        process_callback=None,
        config_manager=cfg,
        automation_engine=None,
    )
    return w


def test_unseen_folder_returns_false_not_none(worker, tmp_path):
    """Annotated ``-> bool``; ``row and row['status'] in (...)`` returned the
    None from an empty fetchone(), so callers doing `is False` or serialising
    the value got `null`."""
    candidate = _Candidate(path=str(tmp_path / "Album"))
    assert worker._is_already_processed(candidate) is False


@pytest.mark.parametrize(
    "status", ['completed', 'partial', 'failed', 'rejected',
               'pending_review', 'needs_identification'],
)
def test_terminal_row_for_the_same_hash_stops_reprocessing(worker, tmp_path, status):
    folder = str(tmp_path / "Album")
    worker.database.record(folder_path=folder, folder_hash="hash-now", status=status)
    assert worker._is_already_processed(_Candidate(path=folder)) is True


def test_in_flight_row_does_not_block(worker, tmp_path):
    folder = str(tmp_path / "Album")
    worker.database.record(folder_path=folder, folder_hash="hash-now", status='processing')
    assert worker._is_already_processed(_Candidate(path=folder)) is False


def test_remnant_of_a_finished_import_is_not_a_new_album(worker, tmp_path):
    """The imported tracks left the folder, so the hash differs from the one on
    record. What remains was recorded as that run's leftovers — re-identifying
    it imports the same files a second time, under whatever album the leftovers
    happen to match."""
    folder = str(tmp_path / "Album")
    worker.database.record(
        folder_path=folder, folder_hash="hash-before-import", status='completed',
        unmatched=['01 - Track.mp3'],
    )
    candidate = _Candidate(
        path=folder, folder_hash="hash-after-import",
        audio_files=[os.path.join(folder, '01 - Track.mp3')],
    )
    assert worker._is_already_processed(candidate) is True


def test_new_files_in_a_previously_imported_folder_are_processed(worker, tmp_path):
    """The remnant check must not turn a folder path into a graveyard: files
    that were never leftovers of the recorded run are a new drop."""
    folder = str(tmp_path / "Album")
    worker.database.record(
        folder_path=folder, folder_hash="hash-before-import", status='completed',
        unmatched=['01 - Track.mp3'],
    )
    candidate = _Candidate(
        path=folder, folder_hash="hash-after-import",
        audio_files=[os.path.join(folder, '01 - Track.mp3'),
                     os.path.join(folder, '02 - Brand New.flac')],
    )
    assert worker._is_already_processed(candidate) is False


def test_remnant_check_needs_a_recorded_leftover_list(worker, tmp_path):
    """A finished run that left nothing behind says nothing about the files in
    the folder now — those are a new drop."""
    folder = str(tmp_path / "Album")
    worker.database.record(
        folder_path=folder, folder_hash="hash-before-import", status='completed',
    )
    candidate = _Candidate(
        path=folder, folder_hash="hash-after-import",
        audio_files=[os.path.join(folder, '01 - Track.mp3')],
    )
    assert worker._is_already_processed(candidate) is False
