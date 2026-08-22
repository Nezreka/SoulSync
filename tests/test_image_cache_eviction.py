"""The image cache has to give disk back.

Raised as a feature request against a 5,567-album library: "stores them locally
with bounded disk usage and a TTL". SoulSync already had a disk cache — what it
did not have was any way to shrink. `expires_at` was written on every row and
read on every serve, but nothing ever deleted a row or a file, and there was no
size ceiling at all. Browsing Discover on a large library grew
`storage/image_cache` forever.

That is a defect rather than a missing feature, so pruning is not behind the
opt-in toggle: a cache that only grows will eventually fill somebody's disk
whether or not they asked for thumbnails.
"""

from __future__ import annotations

from core.image_cache import ImageCache


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.status_code = 200
        self.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(body))}

    def iter_content(self, chunk_size=65536):
        yield self.body

    def close(self):
        pass


def _cache(tmp_path, **kw):
    return ImageCache(tmp_path, fetcher=lambda url, **_: FakeResponse(b"x" * 1000), **kw)


def _store(cache, n):
    """n distinct cached images, 1000 bytes each."""
    for i in range(n):
        cache.get_url(f"https://img.example.test/{i}.jpg")


# ── stats ────────────────────────────────────────────────────────────────────

def test_stats_reports_what_is_held(tmp_path):
    cache = _cache(tmp_path)
    _store(cache, 3)

    stats = cache.stats()
    assert stats["entries"] == 3
    assert stats["ok"] == 3
    assert stats["bytes"] == 3000


# ── TTL ──────────────────────────────────────────────────────────────────────

def test_prune_drops_expired_entries_and_their_files(tmp_path):
    """The TTL was decorative before this: stored, read, never acted on."""
    cache = _cache(tmp_path, ttl_seconds=60)
    _store(cache, 2)
    paths = list(tmp_path.rglob("*.jpg"))
    assert len(paths) == 2

    result = cache.prune(now=_far_future())

    assert result["expired"] == 2
    assert cache.stats()["entries"] == 0
    assert [p for p in paths if p.exists()] == [], "files were left behind on disk"


def test_prune_keeps_entries_that_are_still_fresh(tmp_path):
    cache = _cache(tmp_path, ttl_seconds=3600)
    _store(cache, 2)

    assert cache.prune()["expired"] == 0
    assert cache.stats()["entries"] == 2


def _far_future():
    import time
    return time.time() + 10 * 365 * 24 * 60 * 60


# ── size cap ─────────────────────────────────────────────────────────────────

def test_prune_evicts_least_recently_used_until_it_fits(tmp_path):
    """Eviction order is last_accessed, which the serve path already maintains,
    so the art actually being browsed is the art that survives."""
    cache = _cache(tmp_path, max_cache_bytes=2500)   # room for 2 of 3
    _store(cache, 3)                                  # 3000 bytes total
    # Touch the oldest so it is no longer the coldest.
    cache.get_url("https://img.example.test/0.jpg")

    result = cache.prune()

    assert result["evicted"] == 1
    assert cache.stats()["bytes"] <= 2500
    remaining = _urls(cache)
    assert "https://img.example.test/0.jpg" in remaining, "the recently-used entry was evicted"
    assert "https://img.example.test/1.jpg" not in remaining, "the coldest entry should have gone"


def test_a_zero_cap_means_unlimited(tmp_path):
    cache = _cache(tmp_path, max_cache_bytes=0)
    _store(cache, 5)

    assert cache.prune()["evicted"] == 0
    assert cache.stats()["entries"] == 5


def test_the_cap_is_not_enforced_while_it_fits(tmp_path):
    cache = _cache(tmp_path, max_cache_bytes=10_000)
    _store(cache, 3)

    assert cache.prune()["evicted"] == 0
    assert cache.stats()["entries"] == 3


def _urls(cache):
    conn = cache._connect()
    try:
        return {r["original_url"] for r in conn.execute("SELECT original_url FROM image_cache")}
    finally:
        conn.close()


# ── clear ────────────────────────────────────────────────────────────────────

def test_clear_empties_the_cache_completely(tmp_path):
    cache = _cache(tmp_path)
    _store(cache, 4)

    result = cache.clear()

    assert result["removed"] == 4
    assert cache.stats() == {**cache.stats(), "entries": 0, "bytes": 0}
    assert list(tmp_path.rglob("*.jpg")) == [], "files survived a clear"


def test_the_cache_still_works_after_being_cleared(tmp_path):
    """A cleared cache must refill, not wedge."""
    cache = _cache(tmp_path)
    _store(cache, 1)
    cache.clear()

    served = cache.get_url("https://img.example.test/0.jpg")

    assert served.path.exists()
    assert cache.stats()["entries"] == 1


# ── automatic upkeep ─────────────────────────────────────────────────────────

def test_storing_images_eventually_prunes_without_being_asked(tmp_path):
    """Nobody is going to press a button on a schedule. Storing has to reclaim,
    or the cap is theatre — which is exactly what the TTL was."""
    from core import image_cache as mod

    original = mod._PRUNE_EVERY_N_STORES
    mod._PRUNE_EVERY_N_STORES = 5
    try:
        cache = _cache(tmp_path, max_cache_bytes=3000)
        _store(cache, 10)
        assert cache.stats()["bytes"] <= 3000, "the cache blew past its cap unattended"
    finally:
        mod._PRUNE_EVERY_N_STORES = original


def test_a_prune_failure_never_breaks_the_image_being_served(tmp_path, monkeypatch):
    """Housekeeping is not worth a broken page."""
    from core import image_cache as mod

    original = mod._PRUNE_EVERY_N_STORES
    mod._PRUNE_EVERY_N_STORES = 1
    try:
        cache = _cache(tmp_path)
        monkeypatch.setattr(cache, "prune", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

        served = cache.get_url("https://img.example.test/9.jpg")

        assert served.path.exists()
    finally:
        mod._PRUNE_EVERY_N_STORES = original


# --- pending registrations -------------------------------------------------
# A `pending` row is written by `cache_url_for` every time a URL is handed to a
# browser; it only ever becomes ok/failed if something actually requests it.
# Those rows were created with `expires_at = 0` and `prune()` only ever deleted
# rows with `expires_at > 0`, so a registration nobody loaded was immortal —
# and with the size cap disabled (`max_cache_mb = 0`) nothing else could reclaim
# it either. A production cache held 857 rows of which 602 were pending.

def test_a_registration_nobody_loads_expires(tmp_path):
    cache = _cache(tmp_path, pending_ttl_seconds=100)
    cache.cache_url_for("https://img.example.test/never-loaded.jpg")
    assert cache.stats()["pending"] == 1

    assert cache.prune(now=_now(cache) + 1000)["expired"] == 1
    assert cache.stats()["entries"] == 0


def test_a_fresh_registration_survives(tmp_path):
    cache = _cache(tmp_path, pending_ttl_seconds=100_000)
    cache.cache_url_for("https://img.example.test/soon.jpg")
    cache.prune()
    assert cache.stats()["pending"] == 1


def test_pending_rows_are_reclaimed_even_with_the_size_cap_disabled(tmp_path):
    """`max_cache_mb = 0` is a supported setting ("no size limit"), and it was
    the production configuration. It must not also mean "never reclaim"."""
    cache = _cache(tmp_path, max_cache_bytes=0, pending_ttl_seconds=1)
    for i in range(5):
        cache.cache_url_for(f"https://img.example.test/p{i}.jpg")
    assert cache.prune(now=_now(cache) + 100)["expired"] == 5


def test_legacy_pending_rows_without_an_expiry_still_drain(tmp_path):
    """Rows written before pending registrations had an expiry sit at
    `expires_at = 0`, where the TTL query cannot see them at all."""
    cache = _cache(tmp_path, pending_ttl_seconds=10)
    cache.cache_url_for("https://img.example.test/legacy.jpg")
    with cache._connect() as conn:            # simulate the pre-fix row shape
        conn.execute("UPDATE image_cache SET expires_at = 0")
        conn.commit()

    assert cache.prune(now=_now(cache) + 100)["expired"] == 1
    assert cache.stats()["entries"] == 0


def test_registering_a_url_again_never_shortens_a_real_cached_image(tmp_path):
    """`cache_url_for` runs on every render, long after the image was fetched.
    Renewing a pending lease must not overwrite an `ok` row's full TTL with the
    much shorter pending one."""
    cache = _cache(tmp_path, pending_ttl_seconds=1)
    url = "https://img.example.test/real.jpg"
    cache.get_url(url)                          # -> status ok, full TTL
    cache.cache_url_for(url)                    # re-offered to the browser

    assert cache.prune(now=_now(cache) + 100)["expired"] == 0
    assert cache.stats()["ok"] == 1


def _now(cache):
    import time
    return time.time()
