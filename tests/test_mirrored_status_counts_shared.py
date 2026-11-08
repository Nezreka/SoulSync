"""The mirrored list's status counts are computed once for concurrent asks.

The dashboard band, the sync page and the cards each fetch the list, often at
the same moment; the counts cost ~0.6s each on a real library, and four of
them stacked behind a sync's writes."""

from __future__ import annotations

import threading
import time

import api.mirrored_playlists as mp


class _Db:
    database_path = "x.db"

    def __init__(self):
        self.calls = 0

    def get_all_mirrored_playlist_status_counts(self, profile_id=1):
        self.calls += 1
        time.sleep(0.2)
        return {1: {"total": self.calls}}


def test_concurrent_asks_share_one_computation(monkeypatch):
    monkeypatch.setattr(mp, "STATUS_COUNTS_TTL", 5.0)
    mp._status_counts_cache.clear()
    db = _Db()
    out = []
    threads = [threading.Thread(target=lambda: out.append(mp._status_counts(db, 1)))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert db.calls == 1
    assert out == [{1: {"total": 1}}] * 4
    # inside the reuse window: no new computation
    assert mp._status_counts(db, 1) == {1: {"total": 1}} and db.calls == 1
    mp._status_counts_cache.clear()


def test_without_reuse_every_ask_computes(monkeypatch):
    monkeypatch.setattr(mp, "STATUS_COUNTS_TTL", 0.0)
    mp._status_counts_cache.clear()
    db = _Db()
    mp._status_counts(db, 1)
    mp._status_counts(db, 1)
    assert db.calls == 2


def test_a_failed_computation_does_not_block_the_next(monkeypatch):
    monkeypatch.setattr(mp, "STATUS_COUNTS_TTL", 5.0)
    mp._status_counts_cache.clear()

    class _Bad(_Db):
        def get_all_mirrored_playlist_status_counts(self, profile_id=1):
            raise RuntimeError("locked")
    try:
        mp._status_counts(_Bad(), 1)
    except RuntimeError:
        pass
    assert mp._status_counts_inflight == {}
    assert mp._status_counts(_Db(), 1) == {1: {"total": 1}}
    mp._status_counts_cache.clear()
