from __future__ import annotations

from core.image_cache import ImageCache


class FakeResponse:
    def __init__(self, body: bytes, *, status_code: int = 200, content_type: str = "image/jpeg"):
        self.body = body
        self.status_code = status_code
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        }
        self.closed = False

    def iter_content(self, chunk_size=65536):
        yield self.body

    def close(self):
        self.closed = True


def test_cache_url_for_registers_hashed_browser_path(tmp_path):
    cache = ImageCache(tmp_path)
    url = "https://images.example.test/cover.jpg?token=secret"

    cached_url = cache.cache_url_for(url)

    assert cached_url == f"/api/image-cache/{ImageCache.key_for_url(url)}"
    assert "secret" not in cached_url


def test_get_url_fetches_once_then_serves_cached_file(tmp_path):
    calls = []

    def fetcher(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(b"fake-jpeg-bytes")

    cache = ImageCache(tmp_path, fetcher=fetcher)
    url = "https://images.example.test/cover.jpg"

    first = cache.get_url(url)
    second = cache.get_url(url)

    assert first.status == "miss"
    assert second.status == "hit"
    assert first.path == second.path
    assert first.path.read_bytes() == b"fake-jpeg-bytes"
    assert first.mime_type == "image/jpeg"
    assert len(calls) == 1


def test_get_url_rejects_non_image_responses(tmp_path):
    cache = ImageCache(
        tmp_path,
        fetcher=lambda url, **kwargs: FakeResponse(b"<html></html>", content_type="text/html"),
    )

    try:
        cache.get_url("https://images.example.test/not-image")
    except Exception as exc:
        assert "not an image" in str(exc)
    else:
        raise AssertionError("Expected non-image response to fail")

