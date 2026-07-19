"""Seam tests for the video enrichment TTL+LRU cache (importable core/ module)."""

from __future__ import annotations

import threading

from core.video.enrichment.cache import TTLCache


def test_get_miss_and_hit():
    c = TTLCache(maxsize=4, ttl=100)
    assert c.get("a") is None
    c.put("a", 1)
    assert c.get("a") == 1


def test_ttl_expiry_with_injected_clock():
    t = {"now": 0.0}
    c = TTLCache(maxsize=4, ttl=10, clock=lambda: t["now"])
    c.put("a", 1)
    t["now"] = 9.9
    assert c.get("a") == 1            # still fresh
    t["now"] = 10.1
    assert c.get("a") is None         # expired → evicted
    assert len(c) == 0


def test_per_put_ttl_override():
    t = {"now": 0.0}
    c = TTLCache(ttl=1000, clock=lambda: t["now"])
    c.put("a", 1, ttl=5)
    t["now"] = 6
    assert c.get("a") is None


def test_lru_eviction_not_wholesale():
    c = TTLCache(maxsize=2, ttl=100)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1            # touch 'a' → most-recently-used
    c.put("c", 3)                     # over capacity → evict LRU ('b'), NOT everything
    assert c.get("a") == 1            # survived
    assert c.get("c") == 3            # survived
    assert c.get("b") is None         # the only one evicted
    assert len(c) == 2


def test_thread_safe_under_concurrency():
    c = TTLCache(maxsize=64, ttl=100)

    def worker(n):
        for i in range(500):
            c.put(("k", (n * 500 + i) % 80), i)
            c.get(("k", i % 80))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(c) <= 64               # bound held, no crash/corruption
