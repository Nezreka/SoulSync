"""Tests for the automatic idempotent initial-import bootstrap (docs/library-v2.md §78,
docs/library-v2-tool-integration-audit-2026-07-18.md §7 item 7).

On an existing installation, the very first server start with
``features.library_v2`` enabled must trigger ``import_legacy_library()``
without anyone opening the Library v2 UI. That needs a persisted (crash-
surviving) status, a lock against two overlapping runs, and safe retry after
a failure — see ``core/library2/bootstrap.py``.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from core.library2 import bootstrap as lib2_bootstrap


def _enabled(_key, _default=None):
    return True


def _disabled(_key, _default=None):
    return False


def test_get_state_defaults_when_uninitialized(legacy_db):
    state = lib2_bootstrap.get_state(legacy_db)
    assert state["status"] == "pending"
    assert state["attempts"] == 0
    assert state["last_error"] is None


def test_run_bootstrap_if_needed_noop_when_feature_disabled(legacy_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        lib2_bootstrap, "_import_legacy_library",
        lambda *a, **k: calls.append((a, k)),
    )

    result = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _disabled)

    assert result == {"skipped": "disabled"}
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "pending"
    assert calls == []


def test_run_bootstrap_if_needed_first_run_imports_and_marks_done(legacy_db):
    result = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)

    assert result["success"] is True
    assert result["stats"]["artists"] >= 1
    state = lib2_bootstrap.get_state(legacy_db)
    assert state["status"] == "done"
    assert state["finished_at"] is not None

    conn = legacy_db._get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM lib2_artists").fetchone()
    finally:
        conn.close()
    assert row["n"] >= 1


def test_run_bootstrap_if_needed_skips_when_already_done(legacy_db, monkeypatch):
    first = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert first["success"] is True

    calls = []
    real_import = lib2_bootstrap._import_legacy_library

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_import(*args, **kwargs)

    monkeypatch.setattr(lib2_bootstrap, "_import_legacy_library", _spy)

    second = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)

    assert second == {"skipped": "already_done"}
    assert calls == []


def test_run_bootstrap_if_needed_marks_failed_and_is_retryable(legacy_db, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr(lib2_bootstrap, "_import_legacy_library", _boom)

    failed = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert failed["success"] is False
    assert "synthetic import failure" in failed["error"]

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["status"] == "failed"
    assert "synthetic import failure" in state["last_error"]
    assert state["attempts"] == 1

    monkeypatch.undo()

    retried = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert retried["success"] is True
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "done"
    assert lib2_bootstrap.get_state(legacy_db)["attempts"] == 2


def test_try_claim_blocks_concurrent_run_with_fresh_heartbeat(legacy_db):
    assert lib2_bootstrap.try_claim(legacy_db) is True
    lib2_bootstrap.heartbeat(legacy_db, stage="artists", current=1, total=10)

    assert lib2_bootstrap.try_claim(legacy_db) is False


def test_try_claim_reclaims_stale_running_lock(legacy_db):
    assert lib2_bootstrap.try_claim(legacy_db) is True

    conn = legacy_db._get_connection()
    try:
        conn.execute(
            "UPDATE lib2_bootstrap_state SET heartbeat_at = '2000-01-01T00:00:00+00:00' "
            "WHERE id = 1"
        )
        conn.commit()
    finally:
        conn.close()

    assert lib2_bootstrap.try_claim(legacy_db, stale_after_seconds=600) is True


def test_try_claim_can_reclaim_after_done(legacy_db):
    result = lib2_bootstrap.run_bootstrap_if_needed(legacy_db, _enabled)
    assert result["success"] is True
    assert lib2_bootstrap.get_state(legacy_db)["status"] == "done"

    # A manual "reset & reimport" admin action must still be able to acquire
    # the lock even though the bootstrap already completed once — "done"
    # only means "no need to auto-trigger again", never "permanently locked".
    assert lib2_bootstrap.try_claim(legacy_db) is True


def test_heartbeat_persists_progress(legacy_db):
    assert lib2_bootstrap.try_claim(legacy_db) is True
    lib2_bootstrap.heartbeat(legacy_db, stage="tracks", current=3, total=9)

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["stage"] == "tracks"
    assert state["current"] == 3
    assert state["total"] == 9
    assert state["heartbeat_at"] is not None


def test_mark_failed_records_error_and_leaves_state_retryable(legacy_db):
    assert lib2_bootstrap.try_claim(legacy_db) is True
    lib2_bootstrap.mark_failed(legacy_db, "boom")

    state = lib2_bootstrap.get_state(legacy_db)
    assert state["status"] == "failed"
    assert state["last_error"] == "boom"
    assert lib2_bootstrap.try_claim(legacy_db) is True


def test_try_claim_concurrent_race_has_exactly_one_winner(legacy_db):
    results = []
    barrier = threading.Barrier(8)

    def _attempt():
        barrier.wait()
        try:
            results.append(lib2_bootstrap.try_claim(legacy_db))
        except sqlite3.OperationalError:
            results.append(False)

    threads = [threading.Thread(target=_attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert len(results) == 8
