"""Near-misses leave Review on their own: a task that imports clears the
files it quarantined on the way, an automatic upgrade never sends one to
Review, and old Review items can age out. Cleared files go to the recycle
bin, where restoring puts them back in Review."""

from __future__ import annotations

import json
import os
import time

import pytest

from core.downloads import near_misses as nm
from core.imports.upgrade_intent import CONTEXT_KEY, issue_upgrade_intent


class _Cfg:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _quarantine(qdir, stem, *, age_days=0.0):
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{stem}.flac.quarantined").write_bytes(b"audio")
    stamp = time.time() - age_days * 86400
    from datetime import datetime
    (qdir / f"{stem}.json").write_text(json.dumps({
        "timestamp": datetime.fromtimestamp(stamp).isoformat(),
        "quarantine_reason": "AcoustID mismatch"}), encoding="utf-8")
    return stem


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "downloads" / "ss_quarantine", tmp_path / "Transfer"


# ── which entries a finished task clears ─────────────────────────────────

def test_remember_keeps_each_entry_once():
    task = {}
    nm.remember(task, "a")
    nm.remember(task, "a")
    nm.remember(task, None)
    nm.remember(task, "b")
    assert task[nm.TASK_KEY] == ["a", "b"]


def test_success_takes_the_near_misses_once():
    task = {nm.TASK_KEY: ["a", "b"]}
    assert nm.take_entries_to_clear(task, {}, True, _Cfg()) == ["a", "b"]
    assert nm.take_entries_to_clear(task, {}, True, _Cfg()) == []


def test_success_keeps_them_when_switched_off():
    task = {nm.TASK_KEY: ["a"]}
    cfg = _Cfg(**{"post_processing.clear_near_misses_on_success": False})
    assert nm.take_entries_to_clear(task, {}, True, cfg) == []
    assert task[nm.TASK_KEY] == ["a"]


def test_an_ordinary_failure_leaves_review_alone():
    task = {nm.TASK_KEY: ["a"], "quarantine_entry_id": "b"}
    assert nm.take_entries_to_clear(task, {"auto_initiated": True}, False, _Cfg()) == []
    assert task["quarantine_entry_id"] == "b"


def test_an_automatic_upgrade_that_failed_sends_nothing_to_review():
    task = {nm.TASK_KEY: ["a"], "quarantine_entry_id": "b",
            CONTEXT_KEY: issue_upgrade_intent(5, origin="wishlist")}
    assert nm.take_entries_to_clear(task, {"auto_initiated": True}, False, _Cfg()) == ["a", "b"]
    assert "quarantine_entry_id" not in task


def test_an_upgrade_a_person_started_still_goes_to_review():
    task = {"quarantine_entry_id": "b", CONTEXT_KEY: issue_upgrade_intent(5, origin="wishlist")}
    assert nm.take_entries_to_clear(task, {"auto_initiated": False}, False, _Cfg()) == []
    manual = {"quarantine_entry_id": "b", CONTEXT_KEY: issue_upgrade_intent(5, origin="manual_grab")}
    assert nm.take_entries_to_clear(manual, {"auto_initiated": True}, False, _Cfg()) == []


# ── recycling, and back again ─────────────────────────────────────────────

def test_recycled_entries_land_in_the_recycle_bin_and_restore_to_review(dirs):
    qdir, transfer = dirs
    transfer.mkdir(parents=True)
    keep = _quarantine(qdir, "20261120_120000_keep")
    gone = _quarantine(qdir, "20261120_120001_gone")
    assert nm.recycle_entries([gone, "never-existed"], qdir=str(qdir), transfer=str(transfer)) == 1
    assert sorted(os.listdir(qdir)) == [f"{keep}.flac.quarantined", f"{keep}.json"]

    from core.library import deleted_quarantine as dq
    listed = dq.list_entries(str(transfer))
    ids = [e["id"] for e in listed["entries"]]
    assert len(ids) == 2                                  # the file and its sidecar
    assert dq.restore_entries(str(transfer), ids)["errors"] == []
    assert f"{gone}.flac.quarantined" in os.listdir(qdir)   # back in Review


def test_auto_reject_is_off_by_default(dirs):
    qdir, transfer = dirs
    _quarantine(qdir, "old", age_days=30)
    assert nm.auto_reject_old(_Cfg(), qdir=str(qdir), transfer=str(transfer)) == 0
    assert any(n.endswith(".quarantined") for n in os.listdir(qdir))


def test_auto_reject_moves_only_items_older_than_n_days(dirs):
    qdir, transfer = dirs
    transfer.mkdir(parents=True)
    _quarantine(qdir, "old", age_days=4)
    fresh = _quarantine(qdir, "fresh", age_days=1)
    cfg = _Cfg(**{"post_processing.review_auto_reject_days": 3})
    assert nm.auto_reject_old(cfg, qdir=str(qdir), transfer=str(transfer)) == 1
    assert sorted(os.listdir(qdir)) == [f"{fresh}.flac.quarantined", f"{fresh}.json"]


# ── the wiring: retry remembers, completion clears ───────────────────────

def test_a_quarantine_retry_remembers_the_entry_it_drops(monkeypatch):
    from core.downloads import monitor as dm
    from core.runtime_state import download_tasks, tasks_lock
    monkeypatch.setattr(dm, "config_manager", _Cfg())
    with tasks_lock:
        download_tasks["t-nm"] = {"status": "post_processing", "quarantine_entry_id": "q1",
                                  "username": "peer", "filename": "f.flac", "batch_id": "b",
                                  "track_info": {"name": "Song"}, "used_sources": set()}
    try:
        dm._requeue_decide_and_mark("t-nm", "acoustid")
        task = download_tasks["t-nm"]
        assert "quarantine_entry_id" not in task
        assert task[nm.TASK_KEY] == ["q1"]
    finally:
        with tasks_lock:
            download_tasks.pop("t-nm", None)


def test_completion_recycles_the_tasks_near_misses(monkeypatch):
    from core.downloads import lifecycle
    from core.runtime_state import download_batches, download_tasks, tasks_lock
    cleared = []
    monkeypatch.setattr(nm, "clear_after_task", lambda ids, cfg=None: cleared.extend(ids) or len(ids))
    monkeypatch.setattr(lifecycle, "_on_download_completed", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "_wake_waiting_batches", lambda *a, **k: None)
    with tasks_lock:
        download_batches["b-nm"] = {"queue": ["t-nm2"]}
        download_tasks["t-nm2"] = {"status": "completed", nm.TASK_KEY: ["q1", "q2"]}
    try:
        lifecycle.on_download_completed("b-nm", "t-nm2", True, deps=None)
        assert cleared == ["q1", "q2"]
        lifecycle.on_download_completed("b-nm", "t-nm2", True, deps=None)   # a second call
        assert cleared == ["q1", "q2"]
    finally:
        with tasks_lock:
            download_batches.pop("b-nm", None)
            download_tasks.pop("t-nm2", None)
