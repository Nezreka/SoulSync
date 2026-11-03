"""Enrichment progress for every service comes from one shared snapshot.

Per service the owned-library counts cost ~14s on a 311k-track library, the
status page asked for all 15 services at once (53s) and the status loop asked
again every few seconds. One pass computes every service, readers get the last
snapshot, and a stale one refreshes in the background."""

from __future__ import annotations

import pytest

from core.library2 import worker_queue as wq


@pytest.fixture()
def counted(monkeypatch):
    calls = []
    data = {"artist": {"total": 10, "processed": {"musicbrainz": 4, "deezer": 12}},
            "album": {"total": 0, "processed": {}},
            "track": {"total": 100, "processed": {"musicbrainz": 50}}}
    monkeypatch.setattr(wq, "_compute_progress", lambda conn: calls.append(1) or data)
    monkeypatch.setattr(wq, "_db_file", lambda conn: "/lib.db")
    started = []
    monkeypatch.setattr(wq.threading, "Thread",
                        lambda target, args, **kw: type("T", (), {"start": lambda self: started.append(args)})())
    wq._reset_progress_snapshots()
    yield calls, started
    wq._reset_progress_snapshots()


def test_every_service_reads_one_computation(monkeypatch, counted):
    calls, _started = counted
    monkeypatch.setattr(wq, "PROGRESS_SNAPSHOT_TTL", 120.0)
    mb = wq.progress_breakdown(object(), "musicbrainz")
    dz = wq.progress_breakdown(object(), "Deezer")
    assert len(calls) == 1
    assert mb["artists"] == {"matched": 4, "total": 10, "percent": 40}
    assert mb["tracks"] == {"matched": 50, "total": 100, "percent": 50}
    # processed never exceeds the total; no total is 0%
    assert dz["artists"] == {"matched": 10, "total": 10, "percent": 100}
    assert dz["albums"] == {"matched": 0, "total": 0, "percent": 0}


def test_a_stale_snapshot_is_served_while_it_refreshes_in_the_background(monkeypatch, counted):
    calls, started = counted
    monkeypatch.setattr(wq, "PROGRESS_SNAPSHOT_TTL", 120.0)
    wq.progress_breakdown(object(), "musicbrainz")
    wq._progress_snapshots["/lib.db"]["at"] -= 500
    assert wq.progress_breakdown(object(), "musicbrainz")["tracks"]["matched"] == 50
    assert len(calls) == 1 and started == [("/lib.db",)]
    # one refresh at a time
    wq.progress_breakdown(object(), "musicbrainz")
    assert len(started) == 1


def test_no_sharing_is_always_exact(monkeypatch, counted):
    calls, _started = counted
    monkeypatch.setattr(wq, "PROGRESS_SNAPSHOT_TTL", 0.0)
    wq.progress_breakdown(object(), "musicbrainz")
    wq.progress_breakdown(object(), "musicbrainz")
    assert len(calls) == 2


def test_the_first_snapshot_is_computed_once_while_others_wait(monkeypatch):
    """at start every status call found no snapshot and ran the whole ~30s
    pass itself, all at once. the first one computes, the rest wait for it."""
    import threading

    calls = []
    release = threading.Event()
    data = {"artist": {"total": 1, "processed": {}}, "album": {"total": 0, "processed": {}},
            "track": {"total": 0, "processed": {}}}

    def slow(conn):
        calls.append(1)
        release.wait(5)
        return data

    monkeypatch.setattr(wq, "_compute_progress", slow)
    monkeypatch.setattr(wq, "_db_file", lambda conn: "/lib.db")
    monkeypatch.setattr(wq, "PROGRESS_SNAPSHOT_TTL", 120.0)
    wq._reset_progress_snapshots()
    results = []
    readers = [threading.Thread(target=lambda: results.append(wq._progress_snapshot(object())))
               for _ in range(4)]
    for t in readers:
        t.start()
    for _ in range(100):
        if wq._progress_first.get("/lib.db") is not None:
            break
        threading.Event().wait(0.01)
    threading.Event().wait(0.1)
    release.set()
    for t in readers:
        t.join(5)
    wq._reset_progress_snapshots()

    assert len(calls) == 1
    assert len(results) == 4 and all(r is data for r in results)


@pytest.fixture()
def pending(monkeypatch):
    calls = []
    monkeypatch.setattr(wq, "_pending_count", lambda conn, service, **kw: calls.append(service) or 7)
    monkeypatch.setattr(wq, "_db_file", lambda conn: "/lib.db")
    wq._reset_progress_snapshots()
    yield calls
    wq._reset_progress_snapshots()


def test_a_running_workers_pending_count_is_shared_for_a_while(monkeypatch, pending):
    """the status loop asks a running worker every 2s; that was several
    COUNT(*) passes per worker per tick, 1-15s each on a big library"""
    monkeypatch.setattr(wq, "PENDING_COUNT_TTL", 30.0)
    assert wq.pending_count(object(), "deezer") == 7
    assert wq.pending_count(object(), "Deezer") == 7
    assert wq.pending_count(object(), "spotify") == 7
    assert pending == ["deezer", "spotify"]
    # past the ttl it counts again
    for key in list(wq._pending_cache):
        wq._pending_cache[key] = (wq._pending_cache[key][0] - 31, 7)
    wq.pending_count(object(), "deezer")
    assert pending == ["deezer", "spotify", "deezer"]


def test_pending_count_is_exact_without_sharing(monkeypatch, pending):
    monkeypatch.setattr(wq, "PENDING_COUNT_TTL", 0.0)
    wq.pending_count(object(), "deezer")
    wq.pending_count(object(), "deezer")
    assert pending == ["deezer", "deezer"]
