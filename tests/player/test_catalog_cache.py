"""the player's video catalog cache: a cold read of one view must not hold
up the others, and the same view asked twice at once reads once."""

from __future__ import annotations

import threading
import time

import api.player as player_api


class _Conn:
    def close(self):
        pass


class _VDB:
    def _get_connection(self):
        return _Conn()


def test_a_slow_view_does_not_block_another(monkeypatch):
    monkeypatch.setattr(player_api, "_cache", {})
    monkeypatch.setattr(player_api, "_cache_key_locks", {})
    monkeypatch.setattr(player_api, "get_video_db", lambda: _VDB())
    release = threading.Event()
    reads = []

    def read_all(conn, movies, shows, youtube):
        reads.append((movies, shows, youtube))
        if movies:  # the slow view
            release.wait(5)
        return {"view": (movies, shows, youtube)}

    monkeypatch.setattr(player_api.catalog, "read_all", read_all)
    slow = threading.Thread(target=player_api._read_catalog, args=(True, True, True), daemon=True)
    slow.start()
    time.sleep(0.05)
    t0 = time.monotonic()
    fast = player_api._read_catalog(False, True, False)
    assert time.monotonic() - t0 < 1.0
    assert fast == {"view": (False, True, False)}
    release.set()
    slow.join(5)


def test_the_same_view_twice_at_once_reads_once(monkeypatch):
    monkeypatch.setattr(player_api, "_cache", {})
    monkeypatch.setattr(player_api, "_cache_key_locks", {})
    monkeypatch.setattr(player_api, "get_video_db", lambda: _VDB())
    reads = []

    def read_all(conn, movies, shows, youtube):
        reads.append(1)
        time.sleep(0.2)
        return {"n": len(reads)}

    monkeypatch.setattr(player_api.catalog, "read_all", read_all)
    out = []
    threads = [threading.Thread(target=lambda: out.append(player_api._read_catalog(True, False, False)))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(reads) == 1
    assert out == [{"n": 1}] * 4
