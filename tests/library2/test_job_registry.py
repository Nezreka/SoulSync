"""Concurrent Library-v2 background job registry."""

from __future__ import annotations

import pytest

from core.library2.job_registry import JobAlreadyRunning, JobRegistry


def test_different_job_kinds_run_concurrently_and_keep_independent_state():
    registry = JobRegistry()
    monitor = registry.start("monitor", total=3)
    retag = registry.start("retag", total=8)
    registry.update(monitor["job_id"], current=2, result={"mirrored": 1})
    registry.update(retag["job_id"], current=5)

    assert registry.get(monitor["job_id"])["current"] == 2
    assert registry.get(retag["job_id"])["current"] == 5
    assert registry.latest()["job_id"] == retag["job_id"]
    assert {state["job_id"] for state in registry.list()} == {
        monitor["job_id"], retag["job_id"],
    }


def test_same_kind_is_serialized_until_finished():
    registry = JobRegistry()
    first = registry.start("retag")
    with pytest.raises(JobAlreadyRunning) as exc:
        registry.start("retag")
    assert exc.value.state["job_id"] == first["job_id"]

    finished = registry.finish(first["job_id"], result={"written": 2})
    assert finished["running"] is False
    assert finished["finished_at"] is not None
    assert registry.start("retag")["job_id"] != first["job_id"]


def test_monitor_scopes_use_one_serialized_kind():
    registry = JobRegistry()
    monitor = registry.start("monitor")
    with pytest.raises(JobAlreadyRunning) as exc:
        registry.start("monitor")
    assert exc.value.state["job_id"] == monitor["job_id"]


def test_unknown_job_and_invalid_updates_are_explicit():
    registry = JobRegistry()
    state = registry.start("upgrade-scan")
    assert registry.get("missing") is None
    with pytest.raises(KeyError):
        registry.update("missing", current=1)
    with pytest.raises(ValueError, match="unsupported"):
        registry.update(state["job_id"], running=False)
