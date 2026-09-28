"""Backend-review workers/ops regressions: H16 (watchdog/epoch), H17
(bulk_toggle reschedule), H18 (orphan action reschedule).

H16: the DB-update stall watchdog flips a hung 'running' job to 'error' but
the hung worker thread stays alive on the single-worker executor. The next
automation tick queues worker B behind hung worker A; when A finally
finishes, its finished callback unconditionally overwrote the state to
'finished', so run B's monitor reported 'completed' without worker B ever
scanning. Fix: a run epoch on db_update_state, bumped at run start; the
terminal callbacks only commit when they belong to the current epoch.

H17: bulk_toggle passed the automation DICT to schedule_automation(id) ->
sqlite bind error swallowed -> 200 success but no timer armed.

H18: run_automation's missing-handler path returned before _finish_run, so
an automation with an unregistered action_type fired once and never again.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from database.music_database import MusicDatabase
from core.automation_engine import AutomationEngine
from core.automation import api as automation_api
import api.database_admin as dba


# ── H16: run-epoch gating of the terminal callbacks ──────────────────────────

class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.fixture()
def db_update_harness(monkeypatch):
    """Wire api.database_admin's module globals to an isolated state dict."""
    state = {
        "status": "running", "phase": "Scanning", "progress": 10,
        "current_item": "", "processed": 0, "total": 0, "error_message": "",
        "removed_artists": 0, "removed_albums": 0, "removed_tracks": 0,
        "run_epoch": 1,
    }
    lock = threading.Lock()
    progress = _Recorder()
    resume = _Recorder()
    activity = _Recorder()
    engine = SimpleNamespace(emit=_Recorder(), _running=True)
    config = SimpleNamespace(get=lambda key, default=None: False)

    monkeypatch.setattr(dba, "db_update_state", state)
    monkeypatch.setattr(dba, "db_update_lock", lock)
    monkeypatch.setattr(dba, "db_update_worker", None)
    monkeypatch.setattr(dba, "_db_update_automation_id", 7)
    monkeypatch.setattr(dba, "_update_automation_progress", progress)
    monkeypatch.setattr(dba, "_resume_workers_after_scan", resume)
    monkeypatch.setattr(dba, "add_activity_item", activity)
    monkeypatch.setattr(dba, "automation_engine", engine)
    monkeypatch.setattr(dba, "config_manager", config)
    # raising=False: the attribute only exists after the H16 fix lands; the
    # regression tests must fail (not error) on the unfixed code.
    monkeypatch.setattr(dba, "_active_task_epoch", 1, raising=False)
    return SimpleNamespace(
        state=state, progress=progress, resume=resume,
        activity=activity, engine=engine,
    )


def _begin_run(harness, monkeypatch, epoch):
    """Simulate a new run starting: the epoch bumps, the new worker task has
    not captured it yet (it queues behind the hung worker)."""
    harness.state["run_epoch"] = epoch
    harness.state["status"] = "running"
    harness.state["phase"] = "Initializing..."


def test_stale_finished_callback_ignored_after_new_run(db_update_harness, monkeypatch):
    """REGRESSION (H16): worker A (epoch 1) finishes after run B (epoch 2)
    started — its 'finished' write must not land on run B's state."""
    h = db_update_harness
    _begin_run(h, monkeypatch, epoch=2)  # run B started; worker A still alive

    dba._db_update_finished_callback(50, 0, 0, 50, 0)  # stale worker A finishes

    assert h.state["status"] == "running", (
        "stale worker's finished callback overwrote the new run's state"
    )
    assert h.state["phase"] == "Initializing..."
    assert h.progress.calls == []
    assert h.activity.calls == []
    assert h.engine.emit.calls == []


def test_stale_error_callback_ignored_after_new_run(db_update_harness, monkeypatch):
    """REGRESSION (H16): same gating for the error callback — a stale
    worker's error must not fail the new run."""
    h = db_update_harness
    _begin_run(h, monkeypatch, epoch=2)

    dba._db_update_error_callback("stale boom")

    assert h.state["status"] == "running"
    assert h.state["error_message"] == ""
    assert h.progress.calls == []


def test_queued_task_carries_its_submit_time_epoch(db_update_harness, monkeypatch):
    """A task queued behind a hung worker must carry the epoch assigned at
    submit time — not the epoch current when it eventually starts.

    Run A (epoch 1) is submitted but still queued; the watchdog marks it
    error and run B starts (epoch 2). When task A finally runs, its terminal
    callback must still be dropped as stale.
    """
    h = db_update_harness
    monkeypatch.setattr(dba, "_active_task_epoch", 0)

    # Run A (epoch 1) is submitted but still queued behind a hung worker; the
    # watchdog marks it error and run B starts (epoch 2).
    h.state["run_epoch"] = 1
    h.state["status"] = "error"
    h.state["run_epoch"] = 2
    h.state["status"] = "running"
    h.state["phase"] = "run B working"

    # Task A finally starts, carrying its submit-time epoch (1), not the
    # epoch that is current now (2).
    dba._capture_task_epoch(1)
    assert dba._active_task_epoch == 1

    dba._db_update_finished_callback(50, 0, 0, 50, 0)

    assert h.state["status"] == "running"
    assert h.state["phase"] == "run B working"


def test_current_epoch_finished_callback_still_commits(db_update_harness, monkeypatch):
    """Control: the worker that belongs to the current epoch still drives
    the state to 'finished' and emits the completion event."""
    h = db_update_harness
    monkeypatch.setattr(dba, "_active_task_epoch", 1)  # this task's epoch

    dba._db_update_finished_callback(50, 0, 0, 50, 0)

    assert h.state["status"] == "finished"
    assert "50 artists scanned" in h.state["phase"]
    assert h.engine.emit.calls and h.engine.emit.calls[0][0][0] == "database_update_completed"


def test_current_epoch_error_callback_still_commits(db_update_harness, monkeypatch):
    """Control: the current run's error callback still records the error."""
    h = db_update_harness
    monkeypatch.setattr(dba, "_active_task_epoch", 1)

    dba._db_update_error_callback("disk on fire")

    assert h.state["status"] == "error"
    assert h.state["error_message"] == "disk on fire"


# ── H17: bulk_toggle must reschedule ─────────────────────────────────────────

def _make_engine(tmp_path):
    db = MusicDatabase(str(tmp_path / "music_library.db"))
    engine = AutomationEngine(db)
    engine._running = True
    return db, engine


def _make_hourly_automation(db):
    aid = db.create_automation(
        name="BulkToggleTest",
        trigger_type="schedule",
        trigger_config='{"interval": 1, "unit": "hours"}',
        action_type="notify_only",
        action_config="{}",
        profile_id=1,
    )
    db.update_automation(aid, enabled=0)
    return aid


def test_bulk_toggle_enabling_arms_the_timer(tmp_path):
    """REGRESSION (H17): bulk-enabling must reschedule each automation, like
    the single toggle does — no silent-until-restart."""
    db, engine = _make_engine(tmp_path)
    aid = _make_hourly_automation(db)
    try:
        body, status = automation_api.bulk_toggle(db, engine, [aid], True)

        assert status == 200
        assert db.get_automation(aid)["enabled"] == 1
        assert aid in engine._timers, "bulk_toggle enabled the row but armed no timer"
    finally:
        for timer in list(engine._timers.values()):
            timer.cancel()


def test_bulk_toggle_disabling_cancels_the_timer(tmp_path):
    """Disabling via bulk_toggle cancels the armed timer (was already
    passing the int id on this path — pinned so the H17 fix can't regress
    it)."""
    db, engine = _make_engine(tmp_path)
    aid = _make_hourly_automation(db)
    try:
        db.update_automation(aid, enabled=1)
        engine.schedule_automation(aid)
        assert aid in engine._timers

        body, status = automation_api.bulk_toggle(db, engine, [aid], False)

        assert status == 200
        assert db.get_automation(aid)["enabled"] == 0
        assert aid not in engine._timers
    finally:
        for timer in list(engine._timers.values()):
            timer.cancel()


# ── H18: unregistered action_type must not kill the schedule ─────────────────

def test_missing_handler_reschedules_instead_of_going_silent(tmp_path):
    """REGRESSION (H18): a scheduled automation whose action_type has no
    registered handler must come back via _finish_run — error recorded, next
    timer armed — instead of firing once and going permanently silent."""
    db, engine = _make_engine(tmp_path)
    aid = db.create_automation(
        name="OrphanAction",
        trigger_type="schedule",
        trigger_config='{"interval": 1, "unit": "hours"}',
        action_type="bogus_action_xyz",  # no handler registered, ever
        action_config="{}",
        profile_id=1,
    )
    db.update_automation(aid, enabled=1)
    try:
        engine.schedule_automation(aid)
        assert aid in engine._timers
        timer = engine._timers[aid]

        # Simulate the schedule tick firing: invoke the timer's real target.
        timer.function(*timer.args)

        row = db.get_automation(aid)
        assert "bogus_action_xyz" in (row.get("last_error") or ""), (
            "the missing-handler error should stay visible on the row"
        )
        assert aid in engine._timers and engine._timers[aid] is not timer, (
            "no new timer armed after the tick — the automation will never "
            "fire again until restart"
        )
    finally:
        for t in list(engine._timers.values()):
            t.cancel()
