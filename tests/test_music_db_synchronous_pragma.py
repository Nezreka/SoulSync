"""Regression tests for the per-connection `synchronous` pragma.

WAL's documented-safe fast path (synchronous=NORMAL, an OS crash loses only
recent commits, never corrupts the db) only holds under WAL. _ensure_wal_mode
logs and carries on if WAL setup ever fails, so a connection can find itself
on a rollback journal; NORMAL there is not the same documented-safe trade and
must not be applied.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from database.music_database import MusicDatabase

SYNCHRONOUS_NORMAL = 1
SYNCHRONOUS_FULL = 2


def test_synchronous_normal_when_wal_active(tmp_path: Path) -> None:
    db = MusicDatabase(str(tmp_path / "library.db"))
    conn = db._get_connection()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(journal_mode).lower() == "wal"
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert synchronous == SYNCHRONOUS_NORMAL
    finally:
        conn.close()


def test_synchronous_stays_full_when_wal_setup_failed(tmp_path: Path, monkeypatch) -> None:
    # _ensure_wal_mode logs a warning and returns rather than raising when it
    # can't switch the file to WAL (e.g. a filesystem that doesn't support
    # it). Simulate that by making it a no-op, leaving the fresh db on
    # SQLite's default rollback journal.
    monkeypatch.setattr(MusicDatabase, "_ensure_wal_mode", lambda self: None)

    db = MusicDatabase(str(tmp_path / "library.db"))
    conn = db._get_connection()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(journal_mode).lower() != "wal"
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert synchronous == SYNCHRONOUS_FULL
    finally:
        conn.close()
