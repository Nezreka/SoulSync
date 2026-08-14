"""The video side re-fetched every poster from TMDB on every request.

`/api/video/poster/...` and `/api/video/img?u=...` streamed upstream each time
and relied purely on `Cache-Control` for the browser to do the remembering. A
hard refresh, a second device, or a cleared browser cache paid for the whole
grid again — and none of it ever landed on disk.

The music side has had a disk cache for a while. These pin that both sides now
use the SAME one, so there is a single size limit, a single TTL and a single
Clear button rather than two half-solutions.
"""

from __future__ import annotations

import pytest

from api.video import poster as poster_mod


class _Cached:
    def __init__(self, path):
        self.path, self.mime_type, self.size, self.status = path, "image/jpeg", 3, "hit"


def test_the_cache_is_consulted_before_going_upstream(tmp_path, monkeypatch):
    f = tmp_path / "poster.jpg"
    f.write_bytes(b"jpg")
    seen = []

    class _Cache:
        def get_url(self, url):
            seen.append(url)
            return _Cached(f)

    monkeypatch.setattr("core.image_cache.get_image_cache", lambda: _Cache())

    got = poster_mod._cached_file("https://image.tmdb.org/t/p/w500/x.jpg")

    assert got is not None and got.path == f
    assert seen == ["https://image.tmdb.org/t/p/w500/x.jpg"]


def test_the_shared_toggle_turns_the_video_side_off_too(monkeypatch):
    """One switch governs both sides — that was the ask."""
    class _Cfg:
        def get(self, key, default=None):
            return False if key == "image_cache.enabled" else default

    monkeypatch.setattr("core.settings.config_manager", _Cfg())

    assert poster_mod._cached_file("https://image.tmdb.org/t/p/w500/x.jpg") is None


def test_a_cache_failure_falls_back_to_streaming(monkeypatch):
    """A page full of posters must not break because the cache had a bad day —
    a cache problem costs a cache miss, never a broken image."""
    def _boom():
        raise RuntimeError("cache is unwell")

    monkeypatch.setattr("core.image_cache.get_image_cache", _boom)

    assert poster_mod._cached_file("https://image.tmdb.org/t/p/w500/x.jpg") is None


def test_a_miss_returns_none_rather_than_raising(monkeypatch):
    class _Cache:
        def get_url(self, url):
            from core.image_cache import ImageCacheError
            raise ImageCacheError("upstream 404")

    monkeypatch.setattr("core.image_cache.get_image_cache", lambda: _Cache())

    assert poster_mod._cached_file("https://image.tmdb.org/t/p/w500/x.jpg") is None


@pytest.mark.parametrize("route_src", ["poster", "img"])
def test_both_video_art_routes_go_through_the_cache(route_src):
    """Guards against one route being wired and the other forgotten — the
    'fixed in one of two places' mistake this codebase has made before."""
    import inspect
    src = inspect.getsource(poster_mod.register_routes)
    # _stream_art serves posters/backdrops; video_img_proxy serves /img.
    assert src.count("_cached_file(") >= 2, \
        "one of the two video art routes still bypasses the shared cache"
