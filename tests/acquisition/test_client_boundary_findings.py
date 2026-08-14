"""Regression tests for the §27 Domain-F download-client findings.

dd28-14  Usenet cancel wrote CANCELLED without ever contacting the client
dd28-15  restart adoption could bind a new grab to an old, finished history job
dd28-17  run_async had no timeout and blocked the monitor's cycle lock forever
dd28-47  a dead loop pump left every later run_async blocked with no error
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from core.acquisition.client_monitor import (
    CLIENT_CALL_TIMEOUT_S,
    UsenetJobSnapshot,
    _is_adoptable_job,
)
from utils.async_helpers import AsyncCallTimeout, run_async


# --------------------------------------------------------------------------
# dd28-17 / dd28-47 — the shared loop
# --------------------------------------------------------------------------


def test_run_async_without_a_timeout_still_returns_normally():
    async def _work():
        await asyncio.sleep(0)
        return "done"

    assert run_async(_work()) == "done"


def test_run_async_raises_instead_of_blocking_forever():
    """dd28-17: the monitor holds `_cycle_lock` across these calls."""
    started = threading.Event()

    async def _hang():
        started.set()
        await asyncio.sleep(30)

    began = time.monotonic()
    with pytest.raises(AsyncCallTimeout):
        run_async(_hang(), timeout=0.5)
    elapsed = time.monotonic() - began

    assert started.is_set()
    assert elapsed < 10, "the caller must not have waited for the coroutine"


def test_the_monitor_has_a_client_call_budget():
    assert CLIENT_CALL_TIMEOUT_S > 0


def test_a_dead_loop_thread_is_rebuilt_instead_of_hanging(isolated_async_loop):
    """dd28-47 in its surviving form.

    The finding was that a silently dead bridge left every later run_async
    blocked forever. A loop thread that stops for any reason must be REBUILT on
    the next call rather than leaving callers hanging.

    Runs on a PRIVATE loop: stopping the process-wide one strands every
    coroutine another subsystem has in flight on it, and those callers block on
    the default ``timeout=None`` — hanging the session rather than failing a
    test.
    """
    helpers = isolated_async_loop

    helpers.run_async(asyncio.sleep(0))  # make sure the loop thread exists
    dead = helpers._get_loop()
    dead.call_soon_threadsafe(dead.stop)

    deadline = time.monotonic() + 5
    while helpers._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not helpers._thread.is_alive(), "the loop thread outlived its loop"

    assert helpers.run_async(asyncio.sleep(0, result="alive"), timeout=10) == "alive"
    assert helpers._get_loop() is not dead


# --------------------------------------------------------------------------
# dd28-15 / dd28-44 — adoption candidates
# --------------------------------------------------------------------------


def _job(state: str) -> UsenetJobSnapshot:
    return UsenetJobSnapshot(
        id="job-1", name="Some.Release", state=state,
        category="soulsync", save_path="/data/complete/x", error=None,
    )


@pytest.mark.parametrize("state", ["downloading", "queued", "paused", "extracting"])
def test_in_flight_jobs_stay_adoptable(state):
    assert _is_adoptable_job(_job(state)) is True


@pytest.mark.parametrize(
    "state", ["completed", "complete", "failed", "error", "cancelled", "deleted"],
)
def test_terminal_history_jobs_are_not_adoptable(state):
    """dd28-15: SAB's list includes its history, so finished jobs of EARLIER
    grabs were adoption candidates. Adopting one produced a phantom completion
    on an already-consumed directory, with no error anywhere."""
    assert _is_adoptable_job(_job(state)) is False


def test_state_matching_is_case_insensitive():
    assert _is_adoptable_job(_job("COMPLETED")) is False


def test_unknown_states_stay_adoptable():
    """Never lose a real in-flight job to an unrecognised client vocabulary."""
    assert _is_adoptable_job(_job("verifying")) is True
    assert _is_adoptable_job(_job("")) is True


# --------------------------------------------------------------------------
# dd28-14 — the two-step cancel contract (ADR-07)
# --------------------------------------------------------------------------


class _StubAdapter:
    def __init__(self, result=True, raises=False):
        self.result = result
        self.raises = raises
        self.calls: list = []

    def is_configured(self):
        return True

    async def remove(self, job_id, delete_files=False):
        self.calls.append((job_id, delete_files))
        if self.raises:
            raise RuntimeError("client unreachable")
        return self.result


@pytest.fixture
def plugin(monkeypatch):
    from core.download_plugins.usenet import UsenetDownloadPlugin

    instance = UsenetDownloadPlugin()
    # No DB in this unit test: grab persistence is a no-op, and the persisted
    # job-id lookup finds nothing.
    monkeypatch.setattr(instance, "_update_grab", lambda *a, **k: None)
    monkeypatch.setattr(instance, "_persisted_job_id", lambda _did: None)
    return instance


def test_cancel_without_a_client_job_is_not_reported_as_cancelled(plugin, monkeypatch):
    """dd28-14: the DB said 'cancelled' while the client kept downloading.

    Because 'cancelled' is terminal, `_restore_open_grabs` never adopted the
    job again either — unrecoverable without cleaning the client up by hand.
    """
    import core.download_plugins.usenet as module

    statuses: list = []
    monkeypatch.setattr(plugin, "_update_grab", lambda _did, **kw: statuses.append(kw))
    monkeypatch.setattr(module, "get_active_usenet_adapter", lambda: _StubAdapter())
    plugin.active_downloads["d1"] = {"job_id": None, "state": "Downloading"}

    ok = asyncio.run(plugin.cancel_download("d1"))

    assert ok is False
    written = [kw.get("status") for kw in statuses]
    assert "cancelled" not in written, f"claimed a cancel it never made: {written}"
    assert "cancel_pending" in written, "the retryable intent must be persisted"


def test_cancel_respects_a_client_that_refuses(plugin, monkeypatch):
    """The adapter's return value was ignored; only exceptions counted."""
    import core.download_plugins.usenet as module

    statuses: list = []
    adapter = _StubAdapter(result=False)
    monkeypatch.setattr(plugin, "_update_grab", lambda _did, **kw: statuses.append(kw))
    monkeypatch.setattr(module, "get_active_usenet_adapter", lambda: adapter)
    plugin.active_downloads["d1"] = {"job_id": "sab-9", "state": "Downloading"}

    ok = asyncio.run(plugin.cancel_download("d1"))

    assert adapter.calls == [("sab-9", False)]
    assert ok is False
    assert "cancelled" not in [kw.get("status") for kw in statuses]


def test_a_confirmed_cancel_is_recorded(plugin, monkeypatch):
    import core.download_plugins.usenet as module

    statuses: list = []
    adapter = _StubAdapter(result=True)
    monkeypatch.setattr(plugin, "_update_grab", lambda _did, **kw: statuses.append(kw))
    monkeypatch.setattr(module, "get_active_usenet_adapter", lambda: adapter)
    plugin.active_downloads["d1"] = {"job_id": "sab-9", "state": "Downloading"}

    ok = asyncio.run(plugin.cancel_download("d1"))

    assert ok is True
    assert "cancelled" in [kw.get("status") for kw in statuses]


def test_cancel_recovers_the_job_id_from_the_persisted_grab(plugin, monkeypatch):
    """The in-memory row is gone after a restart; the grab still knows the job."""
    import core.download_plugins.usenet as module

    adapter = _StubAdapter(result=True)
    monkeypatch.setattr(module, "get_active_usenet_adapter", lambda: adapter)
    monkeypatch.setattr(plugin, "_persisted_job_id", lambda _did: "sab-restored")

    ok = asyncio.run(plugin.cancel_download("gone-after-restart"))

    assert ok is True
    assert adapter.calls == [("sab-restored", False)]
