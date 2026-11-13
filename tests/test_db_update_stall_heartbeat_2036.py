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


# ── second cause: scans that work in silence ─────────────────────────────────

def test_every_worker_beats_into_the_watchdog(state):
    """one artist with tens of thousands of tracks reported progress once,
    when it was done. the inner loops beat now, at most once a second."""
    import core.database_update_worker as duw

    assert duw.heartbeat_sink is da._db_update_heartbeat
    worker = duw.DatabaseUpdateWorker.__new__(duw.DatabaseUpdateWorker)
    worker.callbacks = {}
    worker._beat()
    first = state["last_progress_at"]
    assert first > 0
    state["last_progress_at"] = 0
    worker._beat()                      # inside the same second: passed over
    assert state["last_progress_at"] == 0
    worker._last_beat = 0
    worker._beat()
    assert state["last_progress_at"] > 0
    assert state["phase"] == ""         # only the clock moves


def test_the_inner_loops_of_a_scan_all_beat():
    import inspect
    import re
    import core.database_update_worker as duw

    source = inspect.getsource(duw)
    loops = re.findall(r"^[ \t]+for (?:track|album) in (?:tracks|album_tracks|new_tracks|"
                       r"media_tracks|album_batch):\n([ \t]+\S.*)\n", source, flags=re.M)
    assert len(loops) == 8
    assert all(line.strip() == "self._beat()" for line in loops), loops


def _standalone(tmp_path, monkeypatch, files):
    import os
    import core.soulsync_client as sc

    for name in files:
        path = tmp_path / "Artist" / "Album" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        os.utime(path, (1_000_000, 1_000_000))       # long before any scan
    client = sc.SoulSyncClient.__new__(sc.SoulSyncClient)
    client._transfer_path = str(tmp_path)
    client._progress_callback = None
    client._cache = None
    client._cache_time = 0
    client._cache_ttl = 300
    client._last_scan_time = None
    monkeypatch.setattr(sc, "_PROGRESS_EVERY", 0.0)
    return sc, client


def test_a_quick_scan_that_skips_every_file_still_says_where_it_is(tmp_path, monkeypatch):
    """the standalone quick scan walks the whole music folder and skips what
    is older than the last scan. it only spoke per 100 files READ, so a
    library with nothing new was silent for the whole walk."""
    _sc, client = _standalone(tmp_path, monkeypatch, [f"{n}.flac" for n in range(5)])
    said = []
    client.set_progress_callback(said.append)
    assert client.get_recently_added_albums() == []
    walked = [line for line in said if line.startswith("Checking files:")]
    assert len(walked) == 5
    assert walked[-1] == "Checking files: 5 looked at, 0 new or changed..."


def test_a_full_scan_reports_reading_and_building(tmp_path, monkeypatch):
    _sc, client = _standalone(tmp_path, monkeypatch, [f"{n}.flac" for n in range(3)])
    said = []
    client.set_progress_callback(said.append)
    artists = client._scan_transfer()
    assert len(artists) == 1 and len(artists[0].albums()[0].tracks()) == 3
    assert any(line.startswith("Reading tags:") for line in said)
    assert any(line.startswith("Building library:") for line in said)


def test_one_walk_serves_the_calls_of_one_quick_scan(tmp_path, monkeypatch):
    sc, client = _standalone(tmp_path, monkeypatch, ["a.flac"])
    walks = []
    real = client._scan_transfer
    monkeypatch.setattr(client, "_scan_transfer",
                        lambda since_mtime=0: walks.append(since_mtime) or real(since_mtime))
    monkeypatch.setattr(sc.time, "time", lambda: 2_000_000_000.0)
    client.get_recently_added_albums()
    client.get_recently_updated_albums()
    client.get_recently_added_tracks()
    client.get_recently_updated_tracks()
    assert len(walks) == 1
    client.clear_cache()
    client.get_recently_added_albums()
    assert len(walks) == 2


def test_a_quick_scan_listens_to_any_client_that_reports_progress():
    """only Jellyfin's progress was routed on a quick scan."""
    import core.database_update_worker as duw

    class _Client:
        callback = None

        def ensure_connection(self):
            return True

        def set_progress_callback(self, callback):
            self.callback = callback

        def get_recently_added_albums(self, *_a, **_k):
            return []

        get_recently_updated_albums = get_recently_added_albums

    class _Db:
        def get_database_info_for_server(self, _server):
            return {"tracks": 500}

    phases = []
    worker = duw.DatabaseUpdateWorker.__new__(duw.DatabaseUpdateWorker)
    worker.callbacks = {"phase_changed": [phases.append]}
    worker.media_client, worker.database = _Client(), _Db()
    worker.server_type, worker.full_refresh, worker.should_stop = "soulsync", False, False
    worker._get_artists_for_incremental_update()
    assert worker.media_client.callback is not None
    worker.media_client.callback("Checking files: 10 looked at, 0 new or changed...")
    assert phases[-1] == "Incremental: Checking files: 10 looked at, 0 new or changed..."
