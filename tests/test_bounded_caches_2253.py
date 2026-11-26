"""#2253: ttl memos keyed by an id or a search stop at a size."""

from __future__ import annotations

from utils.bounded_dict import put_bounded


def test_put_bounded_keeps_the_newest_entries():
    cache: dict = {}
    for i in range(100):
        put_bounded(cache, i, i * 2, 10)
    assert list(cache) == list(range(90, 100))
    assert cache[99] == 198


def test_a_refreshed_key_counts_as_new():
    cache: dict = {}
    for i in range(5):
        put_bounded(cache, i, i, 5)
    put_bounded(cache, 0, "again", 5)
    put_bounded(cache, 5, 5, 5)
    assert 0 in cache and 1 not in cache


def test_cutoff_unmet_memo_does_not_keep_every_search(monkeypatch):
    from core.library2 import wanted_views as wv

    wv._cutoff_cache.clear()
    monkeypatch.setattr(wv, "_cutoff_fingerprint", lambda conn, pid: "fp")
    monkeypatch.setattr(wv, "iter_cutoff_unmet", lambda conn, search=None, profile_id=None: iter([{"s": search}]))
    monkeypatch.setattr("core.library2.sql_util.owner_clause", lambda column=None: "1=1")

    class _Conn:
        def execute(self, *a, **k):
            class _C:
                def fetchone(self):
                    return (0, "main", "x.db")
            return _C()

    for i in range(200):
        wv._cutoff_unmet_cached(_Conn(), search=f"search {i}", profile_id=1)
    assert len(wv._cutoff_cache) <= 32
    wv._cutoff_cache.clear()
