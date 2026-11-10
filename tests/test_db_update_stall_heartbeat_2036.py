"""#2036: scans were called stuck after 5 minutes while still working.

the stall watchdog reads db_update_state['last_progress_at']. the own-library
scans run inside the shared scan but never passed their progress through, so a
profile's library that took over 5 minutes looked hung the whole time.
"""

from __future__ import annotations

import threading

import pytest

import api.database_admin as da


@pytest.fixture()
def state(monkeypatch):
    s = {"status": "running", "phase": "", "last_progress_at": 0}
    monkeypatch.setattr(da, "db_update_state", s)
    monkeypatch.setattr(da, "db_update_lock", threading.Lock())
    monkeypatch.setattr(da, "_update_automation_progress", lambda *a, **k: None)
    monkeypatch.setattr(da, "_db_update_automation_id", None)
    return s


def _fake_worker(calls):
    class _Worker:
        def __init__(self, **kw):
            self.cbs = {}
            self.processed_artists = self.processed_tracks = 0

        def connect_callback(self, name, cb):
            self.cbs[name] = cb

        def _tick(self):
            calls.append(da.db_update_state["last_progress_at"])
            # what the artist loop sends, nothing else
            self.cbs["progress_updated"]("Processing Artist A", 1, 2, 50.0)
            calls.append(da.db_update_state["last_progress_at"])

        def run(self):
            self._tick()

        def run_deep_scan(self):
            self._tick()
    return _Worker


@pytest.mark.parametrize("deep", [False, True])
def test_an_own_library_scan_ticks_the_watchdog(state, monkeypatch, deep):
    calls = []
    monkeypatch.setattr(da, "_own_library_scan_clients",
                        lambda server_type: [({"id": 7, "name": "kim"}, object())])
    monkeypatch.setattr(da, "_db_update_phase_callback", lambda p: None)
    monkeypatch.setattr(da, "DatabaseUpdateWorker", _fake_worker(calls))
    da._run_own_library_scans("plex", deep=deep)
    assert calls[0] == 0
    assert calls[1] > 0
    assert state["current_item"] == "kim: Processing Artist A"
    assert (state["processed"], state["total"]) == (1, 2)


def test_a_finished_artist_counts_as_progress(state):
    da._db_update_artist_callback("Artist A", True, "", 1, 10)
    assert state["last_progress_at"] > 0
