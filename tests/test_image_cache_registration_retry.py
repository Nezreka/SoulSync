"""A failed registration flush must not lose the batch (#1763).

A page registers its artwork in one batched flush. Dropping that batch on a
transient error ("database is locked" under load) left every image on the
page without a cache row, so each first-load serve 404'd — a burst of 4xx
from a healthy install — and the 60s registration coalesce in cache_url_for
suppressed re-registration on top of it. The flush now keeps the entries
pending and clears their coalesce marks so the next render or serve (a
serve flushes pending keys) retries the write.
"""

from __future__ import annotations

import pytest

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


URL = "https://images.example.test/first-load-cover.jpg"


def test_failed_flush_keeps_registration_and_serve_retries(tmp_path, monkeypatch):
    cache = ImageCache(tmp_path, fetcher=lambda url, **kw: FakeResponse(b"jpeg-bytes"))

    browser_path = cache.cache_url_for(URL)
    key = ImageCache.key_for_url(URL)
    assert browser_path == f"/api/image-cache/{key}"
    assert key in cache._pending_registrations

    # The batched write hits a transient failure.
    def locked(*args, **kwargs):
        raise Exception("database is locked")

    monkeypatch.setattr(cache, "_connect", locked)
    cache._flush_registrations()

    # The registration survived the failure instead of being dropped...
    assert key in cache._pending_registrations
    # ...and the coalesce mark is gone, so re-registering re-queues it.
    assert key not in cache._registrations

    monkeypatch.undo()

    # The serve itself flushes the pending registration, fetches the image
    # through, and answers 200 — no 404 burst for the browser.
    served = cache.get(key)
    assert served.path.read_bytes() == b"jpeg-bytes"


def test_failed_flush_does_not_clobber_a_newer_registration(tmp_path, monkeypatch):
    cache = ImageCache(tmp_path, fetcher=lambda url, **kw: FakeResponse(b"x"))
    cache.cache_url_for(URL)
    key = ImageCache.key_for_url(URL)

    def locked(*args, **kwargs):
        raise Exception("database is locked")

    monkeypatch.setattr(cache, "_connect", locked)
    cache._flush_registrations()
    monkeypatch.undo()

    # Registering again after the failure refreshes the pending entry.
    cache.cache_url_for(URL)
    assert key in cache._pending_registrations
    assert cache._pending_registrations[key][0] == URL
