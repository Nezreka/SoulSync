from __future__ import annotations

from core.image_cache import ImageCache


class FakeResponse:
    def __init__(self, body: bytes, *, status_code: int = 200, content_type: str = "image/jpeg",
                 declared_length: int | None = None):
        self.body = body
        self.status_code = status_code
        # declared_length lets a test simulate a truncated download: the server
        # promises N bytes (Content-Length) but the body delivers fewer.
        length = len(body) if declared_length is None else declared_length
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(length),
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


def test_truncated_download_is_rejected_not_cached(tmp_path):
    """#750: a short/dropped download (body shorter than the declared
    Content-Length) must NOT be committed as a good cache entry — otherwise the
    half-decoded cover (top strip, rest grey) is served forever. It should raise
    and leave nothing cached, so the next request retries fresh."""
    calls = []

    def fetcher(url, **kwargs):
        calls.append(url)
        # Server promises 5000 bytes but only delivers 800 (connection dropped).
        return FakeResponse(b"x" * 800, declared_length=5000)

    cache = ImageCache(tmp_path, fetcher=fetcher)
    url = "https://images.example.test/big-cover.jpg"

    raised = False
    try:
        cache.get_url(url)
    except Exception as exc:
        raised = True
        assert "Truncated" in str(exc) or "truncated" in str(exc)
    assert raised, "Expected a truncated download to raise"

    # Nothing partial left on disk for this key.
    import glob
    key = ImageCache.key_for_url(url)
    leftover = glob.glob(str(tmp_path / "**" / f"{key}*"), recursive=True)
    leftover = [p for p in leftover if not p.endswith(".sqlite3")]
    assert leftover == [], f"truncated file should not be cached, found: {leftover}"

    # A subsequent SUCCESSFUL fetch works (not poisoned by the failed attempt).
    cache2 = ImageCache(
        tmp_path,
        fetcher=lambda u, **k: FakeResponse(b"complete-jpeg-bytes"),
    )
    result = cache2.get_url(url)
    assert result.path.read_bytes() == b"complete-jpeg-bytes"


def test_complete_download_with_content_length_succeeds(tmp_path):
    """Positive control: a full download whose body matches Content-Length is
    cached normally (the truncation guard doesn't false-positive)."""
    cache = ImageCache(
        tmp_path,
        fetcher=lambda u, **k: FakeResponse(b"a-real-cover"),  # declared==actual
    )
    result = cache.get_url("https://images.example.test/ok-cover.jpg")
    assert result.path.read_bytes() == b"a-real-cover"
    assert result.status == "miss"


def test_no_content_length_still_caches(tmp_path):
    """Some CDNs omit Content-Length (chunked transfer). With no declared size
    we can't detect truncation, so we must NOT reject — cache as before."""
    class NoLengthResponse(FakeResponse):
        def __init__(self, body):
            super().__init__(body)
            del self.headers["Content-Length"]

    cache = ImageCache(
        tmp_path,
        fetcher=lambda u, **k: NoLengthResponse(b"chunked-cover-bytes"),
    )
    result = cache.get_url("https://images.example.test/chunked.jpg")
    assert result.path.read_bytes() == b"chunked-cover-bytes"


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

