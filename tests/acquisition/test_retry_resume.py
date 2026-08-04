"""Restart resume: journaled walks are rebuilt into the legacy worker
(docs/library-v2.md §8) — state restoration only, no second retry engine."""

from __future__ import annotations

import pytest

from core.acquisition.imports import record_pipeline_file_completed
from core.acquisition.retry_resume import resume_interrupted_retry_walks
from core.acquisition.retry_state import (
    get_retry_state,
    journal_retry_snapshot,
    list_active_retry_states,
)
from core.runtime_state import download_tasks
from tests.acquisition.test_main_pipeline_bridge import _seed_import


@pytest.fixture(autouse=True)
def reset_tasks():
    download_tasks.clear()
    yield
    download_tasks.clear()


def _seed_walk(tmp_path, **snapshot_overrides):
    source_root = tmp_path / "client"
    source_root.mkdir(exist_ok=True)
    factory, importing, request = _seed_import(tmp_path / "db.sqlite", source_root)
    task_id = f"acq-{importing.id}-101"
    payload = dict(
        task_id=task_id,
        import_id=importing.id,
        track_id=101,
        candidates=[
            {"username": "peer2", "filename": "b.flac", "size": 9,
             "quality": "flac", "bit_depth": 16, "confidence": 0.8},
        ],
        used_sources={"peer1_a.flac"},
        exhausted_sources={"youtube"},
        retry_counts={"soulseek": 2},
        retry_count=2,
        query_count=3,
    )
    payload.update(snapshot_overrides)
    conn = factory()
    journal_retry_snapshot(conn, **payload)
    conn.commit()
    conn.close()
    return factory, importing, request, task_id


def test_resume_rebuilds_walk_and_submits_worker(tmp_path):
    factory, importing, _request, task_id = _seed_walk(tmp_path)
    submitted = []

    resumed = resume_interrupted_retry_walks(
        factory, submit=submitted.append)

    assert resumed == (task_id,)
    assert submitted == [task_id]
    task = download_tasks[task_id]
    assert task["status"] == "searching"
    assert task["_quarantine_retry"] is True
    assert task["_user_manual_pick"] is False
    assert task["used_sources"] == {"peer1_a.flac"}
    assert task["exhausted_download_sources"] == {"youtube"}
    assert task["quarantine_retry_count"] == 2
    assert task["quarantine_retry_counts_by_source"] == {"soulseek": 2}
    assert task["query_count"] == 3
    # Candidates come back as objects the legacy walk can consume.
    candidate = task["cached_candidates"][0]
    assert candidate.username == "peer2"
    assert candidate.confidence == pytest.approx(0.8)
    # The rebuilt context keeps the acquisition identity for the callbacks.
    track_info = task["track_info"]
    assert track_info["_acquisition_import_id"] == importing.id
    assert track_info["_acquisition_track_id"] == 101
    assert track_info["lib2_entity"]["track_id"] == 101

    conn = factory()
    assert get_retry_state(conn, task_id).last_progress == "resumed after restart"
    conn.close()


def test_resume_skips_walks_still_alive_in_this_process(tmp_path):
    factory, _importing, _request, task_id = _seed_walk(tmp_path)
    download_tasks[task_id] = {"id": task_id, "status": "searching"}
    submitted = []

    assert resume_interrupted_retry_walks(factory, submit=submitted.append) == ()
    assert submitted == []
    conn = factory()
    assert get_retry_state(conn, task_id).status == "active"
    conn.close()


def test_resume_closes_row_when_track_already_processed(tmp_path):
    factory, importing, _request, task_id = _seed_walk(tmp_path)
    conn = factory()
    record_pipeline_file_completed(
        conn, importing.id,
        relative_path="01.flac", final_path="/lib/01.flac", track_id=101)
    # Completion already closed the row in-transaction; reopen a stale copy
    # to prove resume itself also detects the processed plan entry.
    conn.execute(
        "UPDATE acquisition_retry_state SET status='active' WHERE task_id=?",
        (task_id,))
    conn.commit()
    conn.close()
    submitted = []

    assert resume_interrupted_retry_walks(factory, submit=submitted.append) == ()
    assert submitted == []
    conn = factory()
    assert get_retry_state(conn, task_id).status == "completed"
    conn.close()


def test_resume_fails_row_when_track_left_the_plan(tmp_path):
    factory, _importing, _request, task_id = _seed_walk(
        tmp_path, track_id=999, task_id="acq-x-999")

    assert resume_interrupted_retry_walks(factory, submit=lambda _t: None) == ()

    conn = factory()
    state = get_retry_state(conn, "acq-x-999")
    conn.close()
    assert state.status == "failed"
    assert "import plan" in (state.last_error or "")
    assert "acq-x-999" not in download_tasks


def test_resume_without_wired_worker_keeps_row_active_and_purges(
        tmp_path, monkeypatch):
    factory, _importing, _request, task_id = _seed_walk(tmp_path)
    conn = factory()
    journal_retry_snapshot(
        conn, task_id="expired", import_id="aim1-old", track_id=55,
        ttl_seconds=3600, now=0.0)
    conn.commit()
    conn.close()

    # No submit injected and the worker pool is unwired (pinned: earlier
    # tests in a full run may have wired the monitor globals for good).
    monkeypatch.setattr(
        "core.acquisition.retry_resume._default_submit", lambda: None)
    assert resume_interrupted_retry_walks(factory) == ()

    conn = factory()
    assert get_retry_state(conn, task_id).status == "active"
    assert get_retry_state(conn, "expired") is None
    conn.close()
    assert task_id not in download_tasks


def test_resume_failed_submit_rolls_the_task_back(tmp_path):
    factory, _importing, _request, task_id = _seed_walk(tmp_path)

    def _broken_submit(_task_id):
        raise RuntimeError("executor is gone")

    assert resume_interrupted_retry_walks(factory, submit=_broken_submit) == ()
    assert task_id not in download_tasks
    conn = factory()
    # Row stays active so the next cycle can try again.
    assert get_retry_state(conn, task_id).status == "active"
    conn.close()


def test_periodic_import_cycle_invokes_resume(tmp_path, monkeypatch):
    from core.acquisition import import_pipeline

    factory, _importing, _request, task_id = _seed_walk(tmp_path)
    submitted = []
    monkeypatch.setattr(
        "core.acquisition.retry_resume._default_submit",
        lambda: submitted.append)

    import_pipeline.advance_open_imports(factory)

    assert submitted == [task_id]
    assert task_id in download_tasks
    conn = factory()
    assert list_active_retry_states(conn)[0].task_id == task_id
    conn.close()
