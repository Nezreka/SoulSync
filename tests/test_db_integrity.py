"""Tests for core.db_integrity — the post-incident backup-integrity hardening.

Incident: a WAL-mode DB corrupted on an interrupted write; the backup routine
never checked integrity and rotated oldest-by-mtime, so every rolling backup
copied the corruption and evicted the last good one. These tests pin the
guarantees that make that impossible.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from core.db_integrity import (
    DBIntegrityError,
    is_healthy,
    prune_backups,
    quick_check,
    safe_backup,
)


def _make_db(path, rows=50):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    c.executemany("INSERT INTO t (v) VALUES (?)", [(f"row-{i}",) for i in range(rows)])
    c.commit()
    c.close()


def _corrupt_file(path):
    """Physically scribble over the middle of a DB file so SQLite sees a
    malformed image (mirrors real page damage)."""
    size = os.path.getsize(path)
    with open(path, "r+b") as f:
        f.seek(size // 2)
        f.write(b"\x00\xff\x00\xff" * 512)


# ── quick_check / is_healthy ───────────────────────────────────────────────

def test_quick_check_ok_on_healthy_db(tmp_path):
    db = str(tmp_path / "good.db")
    _make_db(db)
    assert quick_check(db) == "ok"
    assert is_healthy(db) is True


def test_missing_file_raises(tmp_path):
    with pytest.raises(DBIntegrityError):
        quick_check(str(tmp_path / "nope.db"))
    assert is_healthy(str(tmp_path / "nope.db")) is False


def test_corrupt_db_is_unhealthy(tmp_path):
    db = str(tmp_path / "bad.db")
    _make_db(db, rows=2000)   # big enough that midpoint hits real pages
    _corrupt_file(db)
    # Either quick_check returns a non-'ok' string OR it raises — both mean bad.
    assert is_healthy(db) is False


# ── safe_backup ────────────────────────────────────────────────────────────

def test_safe_backup_of_healthy_db_succeeds(tmp_path):
    src = str(tmp_path / "src.db"); dst = str(tmp_path / "dst.db")
    _make_db(src)
    safe_backup(src, dst)
    assert os.path.exists(dst)
    assert is_healthy(dst)
    # data really copied
    c = sqlite3.connect(dst)
    assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 50
    c.close()


def test_safe_backup_refuses_corrupt_source(tmp_path):
    """The core fix: never produce a backup from a corrupt DB."""
    src = str(tmp_path / "src.db"); dst = str(tmp_path / "dst.db")
    _make_db(src, rows=2000)
    _corrupt_file(src)
    with pytest.raises(DBIntegrityError):
        safe_backup(src, dst)
    # No poisoned backup left behind.
    assert not os.path.exists(dst)


# ── prune_backups (never evict the last good one) ──────────────────────────

def test_prune_keeps_newest_and_deletes_oldest(tmp_path):
    paths = []
    for i in range(7):
        p = str(tmp_path / f"b{i}.db")
        _make_db(p)               # all healthy
        os.utime(p, (1000 + i, 1000 + i))  # b0 oldest ... b6 newest
        paths.append(p)
    to_delete = prune_backups(paths, max_keep=5)
    # 7 - 5 = 2 oldest deleted
    assert set(to_delete) == {str(tmp_path / "b0.db"), str(tmp_path / "b1.db")}


def test_prune_never_deletes_last_healthy_even_when_newer_are_corrupt(tmp_path):
    """The incident scenario: the newest backups are all corrupt. Pruning to
    max_keep must NOT delete the one older healthy backup."""
    healthy = str(tmp_path / "old_good.db")
    _make_db(healthy)
    os.utime(healthy, (1000, 1000))   # oldest

    corrupt = []
    for i in range(6):
        p = str(tmp_path / f"new_bad{i}.db")
        _make_db(p, rows=2000)
        _corrupt_file(p)
        os.utime(p, (2000 + i, 2000 + i))  # all newer than healthy
        corrupt.append(p)

    all_paths = [healthy] + corrupt   # 7 total, max_keep 5 -> delete 2
    to_delete = prune_backups(all_paths, max_keep=5)

    assert len(to_delete) == 2
    # The single healthy (oldest) backup must be protected despite being oldest.
    assert healthy not in to_delete
    # Only corrupt ones get deleted.
    assert all(p in corrupt for p in to_delete)


def test_prune_noop_under_limit(tmp_path):
    paths = []
    for i in range(3):
        p = str(tmp_path / f"b{i}.db"); _make_db(p); paths.append(p)
    assert prune_backups(paths, max_keep=5) == []
