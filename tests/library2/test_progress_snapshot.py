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
