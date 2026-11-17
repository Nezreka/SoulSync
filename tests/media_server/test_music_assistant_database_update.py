"""Music Assistant must be available to both database scan paths."""

from __future__ import annotations

import threading

import pytest

import api.database_admin as database_admin


class _Signal:
    def connect(self, _callback):
        pass


@pytest.mark.parametrize("deep_scan", [False, True])
def test_database_update_resolves_music_assistant_client(monkeypatch, deep_scan):
    client = object()
    requested_names = []
    worker_calls = {}

    class _Engine:
        def client(self, name):
            requested_names.append(name)
            return client if name == "music_assistant" else None

    class _Worker:
        progress_updated = _Signal()
        phase_changed = _Signal()
        artist_processed = _Signal()
        finished = _Signal()
        error = _Signal()

        def __init__(self, **kwargs):
            worker_calls["kwargs"] = kwargs

        def run(self):
            worker_calls["run"] = "incremental"

        def run_deep_scan(self):
            worker_calls["run"] = "deep"

    monkeypatch.setattr(database_admin, "media_server_engine", _Engine())
    monkeypatch.setattr(database_admin, "DatabaseUpdateWorker", _Worker)
    monkeypatch.setattr(database_admin, "db_update_lock", threading.RLock())
    monkeypatch.setattr(database_admin, "db_update_state", {"run_epoch": 1})
    monkeypatch.setattr(database_admin, "_pause_workers_for_scan", lambda: None)
    monkeypatch.setattr(database_admin, "_post_scan_hook_with_own_libraries",
                        lambda _server, deep: ("deep" if deep else "incremental"))
    errors = []
    monkeypatch.setattr(database_admin, "_db_update_error_callback", errors.append)

    if deep_scan:
        database_admin._run_deep_scan_task("music_assistant", run_epoch=2)
    else:
        database_admin._run_db_update_task(False, "music_assistant", run_epoch=2)

    assert requested_names == ["music_assistant"]
    assert worker_calls["kwargs"]["media_client"] is client
    assert worker_calls["kwargs"]["server_type"] == "music_assistant"
    assert worker_calls["run"] == ("deep" if deep_scan else "incremental")
    assert errors == []
