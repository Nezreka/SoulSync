"""Tests for Download Next queue controls.

These pin the safety contract: promotion reorders only unstarted work, never
interrupts active workers, and never bypasses the scheduler's concurrency gate.
"""

from __future__ import annotations

import threading

import pytest

from core.downloads import lifecycle as lc
from core.downloads.prioritize import (
    PROMOTED_AT_KEY,
    prioritize_batch_next,
    prioritize_task_next,
)
from core.runtime_state import download_batches, download_tasks
from tests.downloads.test_downloads_lifecycle import _build_deps


@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


def _lock(_batch_id):
    return threading.Lock()


def test_prioritize_task_moves_only_the_unstarted_slice():
    download_tasks.update(
        {
            "t1": {"status": "downloading", "batch_id": "b1"},
            "t2": {"status": "downloading", "batch_id": "b1"},
            "t3": {"status": "pending", "batch_id": "b1"},
            "t4": {"status": "queued", "batch_id": "b1"},
        }
    )
    download_batches["b1"] = {
        "queue": ["t1", "t2", "t3", "t4"],
        "queue_index": 2,
        "active_count": 2,
        "max_concurrent": 3,
        "phase": "downloading",
    }

    ok, message, payload = prioritize_task_next("t4", _lock)

    assert ok, message
    assert download_batches["b1"]["queue"] == ["t1", "t2", "t4", "t3"]
    assert download_batches["b1"]["queue_index"] == 2
    assert payload["batch_position"] == 3
    assert download_batches["b1"][PROMOTED_AT_KEY] > 0


def test_prioritize_task_rejects_already_started_track():
    download_tasks["t1"] = {"status": "downloading", "batch_id": "b1"}
    download_batches["b1"] = {
        "queue": ["t1", "t2"],
        "queue_index": 1,
        "active_count": 1,
        "max_concurrent": 3,
        "phase": "downloading",
    }

    ok, message, payload = prioritize_task_next("t1", _lock)

    assert not ok
    assert "queued" in message.lower() or "started" in message.lower()
    assert payload is None
    assert download_batches["b1"]["queue"] == ["t1", "t2"]


def test_prioritize_batch_marks_nonterminal_batch():
    download_batches["b1"] = {
        "queue": ["t1"],
        "queue_index": 0,
        "active_count": 0,
        "max_concurrent": 1,
        "phase": "downloading",
    }

    ok, message, payload = prioritize_batch_next("b1")

    assert ok, message
    assert payload["batch_id"] == "b1"
    assert download_batches["b1"][PROMOTED_AT_KEY] > 0


def test_prioritized_batch_waits_for_current_active_wave_to_drain():
    for task_id, status in {
        "a1": "completed",
        "a2": "downloading",
        "a3": "downloading",
        "a4": "pending",
        "b1": "pending",
    }.items():
        download_tasks[task_id] = {
            "status": status,
            "track_info": {"name": task_id},
            "status_change_time": 0,
        }
    download_batches["A"] = {
        "queue": ["a1", "a2", "a3", "a4"],
        "queue_index": 3,
        "active_count": 3,
        "max_concurrent": 3,
        "phase": "downloading",
        "permanently_failed_tracks": [],
        "cancelled_tracks": set(),
    }
    download_batches["B"] = {
        "queue": ["b1"],
        "queue_index": 0,
        "active_count": 0,
        "max_concurrent": 3,
        "phase": "downloading",
        "permanently_failed_tracks": [],
        "cancelled_tracks": set(),
        PROMOTED_AT_KEY: 100,
    }
    deps, rec = _build_deps(global_max=3)

    lc.on_download_completed("A", "a1", True, deps)
    assert [c for c in rec.calls if c[0] == "submit_dl"] == []
    assert download_batches["A"]["queue_index"] == 3
    assert download_batches["A"]["active_count"] == 2

    download_tasks["a2"]["status"] = "completed"
    lc.on_download_completed("A", "a2", True, deps)
    assert [c for c in rec.calls if c[0] == "submit_dl"] == []
    assert download_batches["A"]["active_count"] == 1

    download_tasks["a3"]["status"] = "completed"
    lc.on_download_completed("A", "a3", True, deps)
    submits = [c for c in rec.calls if c[0] == "submit_dl"]
    assert [call[1] for call in submits] == [("b1", "B")]
    assert download_batches["A"]["queue_index"] == 3
    assert download_batches["B"]["active_count"] == 1


def test_newest_prioritized_batch_wakes_first():
    download_tasks["a1"] = {"status": "completed", "track_info": {"name": "A"}}
    download_batches["A"] = {
        "queue": ["a1"],
        "queue_index": 1,
        "active_count": 1,
        "max_concurrent": 1,
        "phase": "downloading",
        "permanently_failed_tracks": [],
        "cancelled_tracks": set(),
    }
    for batch_id, task_id, promoted_at in (("B", "b1", 100), ("C", "c1", 200)):
        download_tasks[task_id] = {"status": "pending", "status_change_time": 0}
        download_batches[batch_id] = {
            "queue": [task_id],
            "queue_index": 0,
            "active_count": 0,
            "max_concurrent": 1,
            "phase": "downloading",
            "permanently_failed_tracks": [],
            "cancelled_tracks": set(),
            PROMOTED_AT_KEY: promoted_at,
        }
    deps, rec = _build_deps(global_max=1)

    lc.on_download_completed("A", "a1", True, deps)

    submits = [c for c in rec.calls if c[0] == "submit_dl"]
    assert [call[1] for call in submits] == [("c1", "C")]
