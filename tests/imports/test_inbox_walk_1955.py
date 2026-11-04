"""#1955: on a bulk staging load the inbox walked the whole folder (listing
every directory, reading loose files' tags) on every poll. A walk outlasted
the poll interval, polls piled up behind each other and the page timed out.
The walk now runs once per records scan, off the request thread."""

from __future__ import annotations

import threading
import time

import pytest

import core.imports.routes as routes


class _Walker:
    def __init__(self, delay=0.0):
        self.delay, self.calls = delay, 0
        self.lock = threading.Lock()

    def enumerate_candidates(self, staging):
        with self.lock:
            self.calls += 1
            n = self.calls
        time.sleep(self.delay)
        return [f"walk-{n}"], []


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(routes, "_INBOX_WALK_GRACE_SECONDS", 0.5)
    routes._inbox_walk_cache.update({"path": None, "generation": None, "records": None,
                                     "candidates": None, "problems": None})
    routes._inbox_walk_jobs.clear()
    yield
    for job in list(routes._inbox_walk_jobs.values()):
        job.join(5)


def _gen():
    return routes._staging_scan_generation["value"]


def test_polls_of_one_scan_share_one_walk():
    worker, records = _Walker(), object()
    first = routes._inbox_walk(worker, "/s", records, _gen())
    second = routes._inbox_walk(worker, "/s", records, _gen())
    assert first == second == (["walk-1"], [])
    assert worker.calls == 1


def test_concurrent_polls_do_not_each_walk():
    worker, records = _Walker(delay=0.3), object()
    out = []
    threads = [threading.Thread(target=lambda: out.append(routes._inbox_walk(worker, "/s", records, _gen())))
               for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert worker.calls == 1
    assert out == [(["walk-1"], [])] * 5


def test_a_slow_walk_does_not_hold_the_poll():
    worker = _Walker()
    routes._inbox_walk(worker, "/s", object(), _gen())          # walk-1 cached
    worker.delay = 2.0
    started = time.monotonic()
    served = routes._inbox_walk(worker, "/s", object(), _gen())  # new scan, slow walk
    assert time.monotonic() - started < 1.5
    assert served == (["walk-1"], [])                            # the last walk meanwhile


def test_an_import_never_serves_the_old_walk(monkeypatch):
    worker = _Walker()
    routes._inbox_walk(worker, "/s", object(), _gen())
    routes._staging_scan_generation["value"] += 1                # an import moved files
    worker.delay = 2.0
    assert routes._inbox_walk(worker, "/s", object(), _gen()) is None  # "scanning", not walk-1


def test_history_is_looked_up_by_path_through_an_index(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    conn = db._get_connection()
    try:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM auto_import_history WHERE folder_path = ? "
            "ORDER BY created_at DESC LIMIT 1", ("/x",)).fetchall()
    finally:
        conn.close()
    import re
    assert re.search(r"\bidx_aih_folder_path\b", " ".join(str(r[-1]) for r in plan))
