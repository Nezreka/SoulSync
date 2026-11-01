"""A playlist sync pauses the background workers the way a library scan does,
and the two share one count so neither wakes workers the other needs quiet."""

from __future__ import annotations

import pytest

from core import background_pause


class _Worker:
    def __init__(self, paused=False):
        self.paused = paused
        self.calls = []

    def pause(self):
        self.paused = True
        self.calls.append("pause")

    def resume(self):
        self.paused = False
        self.calls.append("resume")


@pytest.fixture()
def workers():
    background_pause._reset_for_tests()
    ws = {"mb": _Worker(), "repair": _Worker(), "manual": _Worker(paused=True)}
    background_pause.register_workers(lambda: ws)
    yield ws
    background_pause._reset_for_tests()


def test_a_sync_pauses_and_resumes_only_what_it_paused(workers):
    with background_pause.paused_for("playlist sync"):
        assert workers["mb"].paused and workers["repair"].paused
    assert not workers["mb"].paused and not workers["repair"].paused
    # paused by hand before: never touched
    assert workers["manual"].paused and workers["manual"].calls == []


def test_a_sync_ending_during_a_scan_leaves_the_workers_paused(workers):
    background_pause.hold("database scan")
    with background_pause.paused_for("playlist sync"):
        pass
    assert workers["mb"].paused, "the scan still holds them"
    background_pause.release("database scan")
    assert not workers["mb"].paused
    assert workers["mb"].calls == ["pause", "resume"]


def test_two_syncs_at_once_share_the_hold(workers):
    background_pause.hold("playlist sync")
    background_pause.hold("playlist sync")
    background_pause.release("playlist sync")
    assert workers["repair"].paused
    background_pause.release("playlist sync")
    assert not workers["repair"].paused


def test_an_extra_release_does_nothing(workers):
    background_pause.release("database scan")
    assert background_pause.holders() == {}
    assert workers["mb"].calls == []


def test_a_failing_sync_still_resumes(workers):
    with pytest.raises(RuntimeError):
        with background_pause.paused_for("playlist sync"):
            raise RuntimeError("boom")
    assert not workers["mb"].paused


def test_no_workers_registered_is_fine():
    background_pause._reset_for_tests()
    with background_pause.paused_for("playlist sync"):
        pass
    assert background_pause.holders() == {}


def test_run_sync_task_holds_the_pause(workers, monkeypatch):
    from core.discovery import sync
    seen = {}

    def body(*a, **k):
        seen["paused"] = workers["mb"].paused
        return "done"
    monkeypatch.setattr(sync, "_run_sync_task", body)
    assert sync.run_sync_task("p", "n", []) == "done"
    assert seen["paused"] is True and not workers["mb"].paused


class _HoldWorker(_Worker):
    """repair's shape: pause() saves a setting, so the hold uses its own."""

    def __init__(self, paused=False):
        super().__init__(paused)
        self.held = False

    def hold_for_foreground(self):
        self.held = True
        self.calls.append("hold")

    def release_foreground(self):
        self.held = False
        self.calls.append("release")


def test_a_worker_with_its_own_hold_never_has_its_setting_touched():
    background_pause._reset_for_tests()
    # switched off by its owner, yet Run Now jobs still run on it: held anyway
    repair = _HoldWorker(paused=True)
    background_pause.register_workers(lambda: {"repair": repair})
    with background_pause.paused_for("playlist sync"):
        assert repair.held
    assert not repair.held
    assert repair.calls == ["hold", "release"], "pause()/resume() would save the master toggle"
    background_pause._reset_for_tests()


def test_the_repair_worker_hold_is_in_memory_and_pauses_run_now_jobs(monkeypatch):
    from core.repair_worker import RepairWorker
    saved = []
    worker = RepairWorker.__new__(RepairWorker)
    worker.enabled = False
    worker._held = False
    worker._config_manager = type("C", (), {"set": lambda self, k, v: saved.append((k, v))})()
    worker.hold_for_foreground()
    assert worker._held and saved == []
    assert worker.enabled is False, "the master toggle is the user's"
    worker.release_foreground()
    assert not worker._held and saved == []
