"""Journal hooks: requeue, candidate walk and terminal import paths keep the
persistent retry state in sync with the in-memory walk (docs/library-v2.md §8)."""

from __future__ import annotations

import sqlite3
import types

import pytest

import core.downloads.monitor as monitor
from core.acquisition import ensure_acquisition_schema
from core.acquisition.imports import record_import_failure, record_pipeline_file_completed
from core.acquisition.pipeline_callback import (
    notify_quarantine_approved,
    notify_task_retry_cancelled,
)
from core.acquisition.retry_state import (
    get_retry_state,
    journal_retry_snapshot,
)
from core.downloads import candidates as dc
from core.runtime_state import download_tasks, matched_downloads_context
from tests.acquisition.test_pipeline_callback import _importing_record


IMPORT_ID = "aim1-x"
TRACK_ID = 7
ACQ_TRACK_INFO = {
    "id": f"lib2-track:{TRACK_ID}",
    "name": "Money",
    "artists": [{"name": "Pink Floyd"}],
    "album": "DSOTM",
    "_acquisition_import_id": IMPORT_ID,
    "_acquisition_track_id": TRACK_ID,
}


@pytest.fixture(autouse=True)
def reset_tasks():
    download_tasks.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    matched_downloads_context.clear()


@pytest.fixture()
def journal_db(tmp_path, monkeypatch):
    """File-backed journal DB patched in as the app database."""
    database_path = tmp_path / "journal.sqlite"

    def factory():
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        return conn

    calls = []

    class _FakeDatabase:
        def _get_connection(self):
            calls.append(1)
            return factory()

    monkeypatch.setattr(
        "database.music_database.get_database", lambda: _FakeDatabase())
    return types.SimpleNamespace(factory=factory, calls=calls)


def _wire_requeue(monkeypatch):
    submitted = []

    class _Exec:
        def submit(self, fn, *args):
            submitted.append(args)

    monkeypatch.setattr(monitor, "missing_download_executor", _Exec())
    monkeypatch.setattr(
        monitor, "_download_track_worker", lambda task_id, batch_id: None)
    real_get = monitor.config_manager.get

    def _pinned_get(key, default=None):
        if key == "post_processing.retry_next_candidate_on_mismatch":
            return True
        return real_get(key, default)

    monkeypatch.setattr(monitor.config_manager, "get", _pinned_get)
    return submitted


def _seed_acquisition_task(task_id="acq-aim1-x-7", **extra):
    task = {
        "id": task_id,
        "status": "post_processing",
        "track_info": dict(ACQ_TRACK_INFO),
        "username": "peer1",
        "filename": "a.flac",
        "used_sources": {"peer0_old.flac"},
        "cached_candidates": [
            {"username": "peer2", "filename": "b.flac", "size": 9,
             "quality": "flac", "confidence": 0.8},
        ],
        "query_count": 2,
    }
    task.update(extra)
    download_tasks[task_id] = task
    return task_id


def test_requeue_snapshots_acquisition_walk(monkeypatch, journal_db):
    submitted = _wire_requeue(monkeypatch)
    task_id = _seed_acquisition_task()

    assert monitor.requeue_quarantined_task_for_retry(task_id, "b1", "quality") is True
    assert submitted == [(task_id, "b1")]

    conn = journal_db.factory()
    state = get_retry_state(conn, task_id)
    conn.close()
    assert state is not None and state.status == "active"
    assert state.import_id == IMPORT_ID and state.track_id == TRACK_ID
    # The just-quarantined source is journaled as used BEFORE the worker
    # re-runs, so a restart cannot re-download it.
    assert "peer1_a.flac" in state.used_sources
    assert "peer0_old.flac" in state.used_sources
    assert state.retry_count == 1
    assert state.query_count == 2
    assert [c["username"] for c in state.candidates] == ["peer2"]
    assert "quality quarantine" in (state.last_progress or "")


def test_requeue_budget_exhausted_closes_journal(monkeypatch, journal_db):
    _wire_requeue(monkeypatch)
    task_id = _seed_acquisition_task(
        quarantine_retry_count=monitor.MAX_QUARANTINE_RETRIES)
    conn = journal_db.factory()
    journal_retry_snapshot(
        conn, task_id=task_id, import_id=IMPORT_ID, track_id=TRACK_ID)
    conn.commit()
    conn.close()

    assert monitor.requeue_quarantined_task_for_retry(task_id, "b1", "acoustid") is False

    conn = journal_db.factory()
    state = get_retry_state(conn, task_id)
    conn.close()
    assert state.status == "failed"
    assert "cap reached" in (state.last_error or "")


def test_requeue_cancelled_closes_journal(monkeypatch, journal_db):
    _wire_requeue(monkeypatch)
    task_id = _seed_acquisition_task(status="cancelled")
    conn = journal_db.factory()
    journal_retry_snapshot(
        conn, task_id=task_id, import_id=IMPORT_ID, track_id=TRACK_ID)
    conn.commit()
    conn.close()

    assert monitor.requeue_quarantined_task_for_retry(task_id, "b1", "quality") is False

    conn = journal_db.factory()
    assert get_retry_state(conn, task_id).status == "cancelled"
    conn.close()


def test_requeue_ordinary_task_never_touches_journal(monkeypatch, journal_db):
    submitted = _wire_requeue(monkeypatch)
    download_tasks["legacy1"] = {
        "id": "legacy1",
        "status": "post_processing",
        "track_info": {"name": "Song"},
        "username": "peer1",
        "filename": "a.flac",
        "used_sources": set(),
    }

    assert monitor.requeue_quarantined_task_for_retry("legacy1", "b1", "quality") is True
    assert submitted == [("legacy1", "b1")]
    assert journal_db.calls == []


def test_candidate_attempt_persists_used_sources(monkeypatch, journal_db):
    # A live walk: the task already tried peer0+peer1, the journal mirrors it.
    task_id = _seed_acquisition_task(
        status="searching", used_sources={"peer0_old.flac", "peer1_a.flac"})
    conn = journal_db.factory()
    journal_retry_snapshot(
        conn, task_id=task_id, import_id=IMPORT_ID, track_id=TRACK_ID,
        used_sources={"peer0_old.flac", "peer1_a.flac"})
    conn.commit()
    conn.close()

    from tests.downloads.test_downloads_candidates import (
        _Candidate,
        _Track,
        _build_deps,
    )

    started = dc.attempt_download_with_candidates(
        task_id,
        [_Candidate(username="peer2", filename="b.flac")],
        _Track(),
        "b1",
        _build_deps(),
    )
    assert started is True

    conn = journal_db.factory()
    state = get_retry_state(conn, task_id)
    conn.close()
    # The new pick is journaled BEFORE the external download starts, and the
    # journal keeps mirroring the walk's full used-source memory.
    assert state.used_sources == (
        "peer0_old.flac", "peer1_a.flac", "peer2_b.flac")


def test_pipeline_completion_closes_track_journal_row():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(conn)
    importing, _request = _importing_record(conn)
    journal_retry_snapshot(
        conn, task_id="t-101", import_id=importing.id, track_id=101)
    journal_retry_snapshot(
        conn, task_id="t-102", import_id=importing.id, track_id=102)

    record_pipeline_file_completed(
        conn, importing.id,
        relative_path="01.flac", final_path="/lib/01.flac", track_id=101)

    assert get_retry_state(conn, "t-101").status == "completed"
    assert get_retry_state(conn, "t-102").status == "active"
    conn.close()


def test_import_failure_closes_every_journal_row():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(conn)
    importing, _request = _importing_record(conn)
    journal_retry_snapshot(
        conn, task_id="t-101", import_id=importing.id, track_id=101)
    journal_retry_snapshot(
        conn, task_id="t-102", import_id=importing.id, track_id=102)

    record_import_failure(
        conn, importing.id,
        error="exhausted", failure_kind="candidate",
        reason_code="pipeline_retry_exhausted")

    assert get_retry_state(conn, "t-101").status == "failed"
    assert get_retry_state(conn, "t-102").status == "failed"
    conn.close()


def _journal_with_context(tmp_path):
    database_path = tmp_path / "ctx.sqlite"

    def factory():
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = factory()
    journal_retry_snapshot(
        conn, task_id="t-ctx", import_id=IMPORT_ID, track_id=TRACK_ID)
    conn.commit()
    conn.close()
    return factory


def test_notify_quarantine_approved_closes_journal(tmp_path):
    factory = _journal_with_context(tmp_path)
    context = {"track_info": dict(ACQ_TRACK_INFO)}

    assert notify_quarantine_approved(context, connection_factory=factory) is True

    conn = factory()
    assert get_retry_state(conn, "t-ctx").status == "approved"
    conn.close()
    # Legacy contexts without markers are a clean no-op.
    assert notify_quarantine_approved({}, connection_factory=factory) is False


def test_notify_task_retry_cancelled_closes_journal(tmp_path):
    factory = _journal_with_context(tmp_path)

    assert notify_task_retry_cancelled(
        dict(ACQ_TRACK_INFO), connection_factory=factory) is True

    conn = factory()
    assert get_retry_state(conn, "t-ctx").status == "cancelled"
    conn.close()
    assert notify_task_retry_cancelled(None, connection_factory=factory) is False
    assert notify_task_retry_cancelled(
        {"name": "Song"}, connection_factory=factory) is False
