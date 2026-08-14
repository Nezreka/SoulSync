"""Resized thumbnails — the half of the reporter's proxy SoulSync was missing.

Their measured win was not the caching (SoulSync already cached) but serving
"resized thumbnails for library-style grids instead of repeatedly downloading
full-size CDN originals". A 5,567-album library was filling 200px tiles with
1400px masters.

A variant is a normal cache row with its own key, so it expires, evicts and
counts towards the size cap by exactly the same rules as an original — no
parallel bookkeeping to drift out of sync.
"""

from __future__ import annotations

import io

import pytest

from core.image_cache import VARIANT_MAX_WIDTH, ImageCache

PIL = pytest.importorskip("PIL")


def _jpeg(width: int, height: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


BIG = _jpeg(1400, 1400)
WIDE = _jpeg(1600, 900)


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.status_code = 200
        self.headers = {"Content-Type": "image/jpeg", "Content-Length": str(len(body))}

    def iter_content(self, chunk_size=65536):
        yield self.body

    def close(self):
        pass


def _cache(tmp_path, body=BIG):
    calls = []

    def fetcher(url, **kwargs):
        calls.append(url)
        return FakeResponse(body)

    cache = ImageCache(tmp_path, fetcher=fetcher)
    cache.calls = calls
    return cache


URL = "https://cdn.example.test/cover.jpg"


def _dimensions(path):
    from PIL import Image
    with Image.open(path) as img:
        return img.size


# ── keys ─────────────────────────────────────────────────────────────────────

def test_each_variant_gets_its_own_key(tmp_path):
    cache = _cache(tmp_path)
    plain = cache.cache_url_for(URL)
    grid = cache.cache_url_for(URL, variant="grid")
    card = cache.cache_url_for(URL, variant="card")

    assert len({plain, grid, card}) == 3, "variants collided on one cache key"


def test_an_unknown_variant_falls_back_to_the_original(tmp_path):
    """A typo in a template must not 404 the image."""
    cache = _cache(tmp_path)
    assert cache.cache_url_for(URL, variant="enormous") == cache.cache_url_for(URL)
    assert cache.variant_url_for(URL, "enormous") == cache.cache_url_for(URL)


# ── resizing ─────────────────────────────────────────────────────────────────

def test_a_grid_variant_is_actually_smaller(tmp_path):
    cache = _cache(tmp_path)
    key = cache.cache_url_for(URL, variant="grid").rsplit("/", 1)[-1]

    served = cache.get(key)

    assert _dimensions(served.path)[0] == VARIANT_MAX_WIDTH["grid"]
    assert served.size < len(BIG), "the variant was not smaller than the original"


def test_aspect_ratio_is_preserved(tmp_path):
    """Artist photos and backdrops are not square; a forced box would crop."""
    cache = _cache(tmp_path, body=WIDE)
    key = cache.cache_url_for(URL, variant="card").rsplit("/", 1)[-1]

    w, h = _dimensions(cache.get(key).path)

    assert w == VARIANT_MAX_WIDTH["card"]
    assert abs((w / h) - (1600 / 900)) < 0.02


def test_an_image_smaller_than_the_variant_is_not_upscaled(tmp_path):
    """Upscaling costs bytes and buys nothing."""
    cache = _cache(tmp_path, body=_jpeg(120, 120))
    key = cache.cache_url_for(URL, variant="grid").rsplit("/", 1)[-1]

    assert _dimensions(cache.get(key).path) == (120, 120)


# ── caching behaviour ────────────────────────────────────────────────────────

def test_the_original_is_fetched_once_for_many_variants(tmp_path):
    """The whole point: one upstream fetch, several local sizes."""
    cache = _cache(tmp_path)
    for variant in ("grid", "card", "hero"):
        cache.get(cache.cache_url_for(URL, variant=variant).rsplit("/", 1)[-1])

    assert cache.calls == [URL], f"upstream was hit {len(cache.calls)} times, expected once"


def test_a_second_request_serves_the_variant_from_disk(tmp_path):
    cache = _cache(tmp_path)
    key = cache.cache_url_for(URL, variant="grid").rsplit("/", 1)[-1]
    cache.get(key)

    again = cache.get(key)

    assert again.status == "hit"
    assert cache.calls == [URL]


def test_variants_count_towards_the_size_cap(tmp_path):
    """They are real files on the user's disk, so they must be evictable."""
    cache = _cache(tmp_path)
    before = cache.stats()["bytes"]
    cache.get(cache.cache_url_for(URL, variant="grid").rsplit("/", 1)[-1])

    assert cache.stats()["bytes"] > before
    assert cache.stats()["entries"] == 2, "the variant should be its own cache entry"


# ── failure ──────────────────────────────────────────────────────────────────

def test_an_undecodable_image_falls_back_to_the_original(tmp_path):
    """A cover Pillow cannot read is not worth failing the page over — serve
    the bytes we have and let the browser scale them, exactly as before."""
    cache = _cache(tmp_path, body=b"this is not an image, but the CDN said image/jpeg")
    key = cache.cache_url_for(URL, variant="grid").rsplit("/", 1)[-1]

    served = cache.get(key)

    assert served.path.exists()
    assert served.path.read_bytes().startswith(b"this is not an image")


def test_serving_a_variant_cannot_deadlock(tmp_path):
    """Building a variant holds that key's lock and then fetches the original,
    which takes the lock for ITS key. Those are different keys in practice, but
    on a plain Lock any path that made them coincide would hang the request
    forever — no error, no timeout, just a stuck worker. A mutation run proved
    it by hanging a test process for six hours. The lock is reentrant so this
    can only ever be an error, never a hang."""
    cache = _cache(tmp_path)
    key = cache.cache_url_for(URL, variant="grid").rsplit("/", 1)[-1]

    lock = cache._lock_for_key(key)
    with lock:                       # would block forever on a plain Lock
        with lock:
            pass

    assert cache.get(key).path.exists()


# ── configurability (#1141: "configurable thumbnail variants") ───────────────

def test_variant_widths_come_from_config(monkeypatch):
    """His ask was configurable variants, not three sizes I picked. A caller
    whose grid is denser or wider than mine can size them to their layout."""
    from core import image_cache as mod

    class _Cfg:
        def get(self, key, default=None):
            return {"image_cache.variant_grid_px": 160}.get(key, default)

    monkeypatch.setattr(mod, "config_manager", _Cfg())
    widths = mod._resolved_variant_widths()

    assert widths["grid"] == 160
    assert widths["card"] == mod.DEFAULT_VARIANT_MAX_WIDTH["card"], "untouched sizes keep their default"


@pytest.mark.parametrize("bad", [0, -5, 99999, "wide", None])
def test_a_nonsense_width_falls_back_instead_of_losing_the_variant(monkeypatch, bad):
    """A typo in config must not make artwork vanish or blow memory on a
    resize — the variant survives at its default size."""
    from core import image_cache as mod

    class _Cfg:
        def get(self, key, default=None):
            return bad if key == "image_cache.variant_grid_px" else default

    monkeypatch.setattr(mod, "config_manager", _Cfg())

    assert mod._resolved_variant_widths()["grid"] == mod.DEFAULT_VARIANT_MAX_WIDTH["grid"]


def test_the_fetch_timeout_is_configurable_and_short_by_default(tmp_path):
    """"Short fetch timeouts ... so a slow image CDN never blocks page
    rendering" — it was hardcoded at 10s with no way to lower it."""
    from core.image_cache import DEFAULT_FETCH_TIMEOUT

    assert DEFAULT_FETCH_TIMEOUT <= 10, "the default is meant to be short"

    seen = {}

    def fetcher(url, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return FakeResponse(BIG)

    cache = ImageCache(tmp_path, fetcher=fetcher, fetch_timeout=2.5)
    cache.get_url(URL)

    assert seen["timeout"] == 2.5, "the configured timeout never reached the request"
