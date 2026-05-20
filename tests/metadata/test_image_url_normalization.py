"""Regression tests for image URL normalization."""

from __future__ import annotations

import pytest
from urllib.parse import quote


@pytest.mark.parametrize(
    "thumb_url",
    [
        "/api/image-proxy?url=https%3A%2F%2Fexample.com%2Fcover.jpg",
        "/api/image-cache/" + ("a" * 64),
        "http://host.docker.internal:4533/api/image-proxy?u=ketiska&t=abc&s=def&v=1.16.1&c=SoulSync&f=json",
    ],
)
def test_normalize_image_url_leaves_existing_image_proxy_urls_alone(thumb_url):
    """Existing proxy URLs should not be wrapped in a second proxy layer."""
    from core.metadata import normalize_image_url

    assert normalize_image_url(thumb_url) == thumb_url


def test_normalize_image_url_registers_internal_http_urls_with_image_cache(monkeypatch):
    """Raw internal image URLs should be routed through SoulSync's hashed cache URL."""
    from core.metadata import normalize_image_url
    from core.metadata import artwork
    import core.image_cache as image_cache

    class _FakeConfig:
        def get_active_media_server(self):
            return "spotify"

        def get_plex_config(self):
            return {}

        def get_jellyfin_config(self):
            return {}

        def get_navidrome_config(self):
            return {}

    monkeypatch.setattr(artwork, "get_config_manager", lambda: _FakeConfig())
    monkeypatch.setattr(image_cache, "cached_image_url", lambda u: "/api/image-cache/" + ("b" * 64))

    url = "http://localhost:4533/cover.jpg"
    assert normalize_image_url(url) == "/api/image-cache/" + ("b" * 64)


def test_normalize_image_url_falls_back_to_proxy_when_cache_registration_fails(monkeypatch):
    """If cache registration breaks, internal URLs keep the old image-proxy behavior."""
    from core.metadata import normalize_image_url
    from core.metadata import artwork
    import core.image_cache as image_cache

    class _FakeConfig:
        def get_active_media_server(self):
            return "spotify"

        def get_plex_config(self):
            return {}

        def get_jellyfin_config(self):
            return {}

        def get_navidrome_config(self):
            return {}

    monkeypatch.setattr(artwork, "get_config_manager", lambda: _FakeConfig())
    monkeypatch.setattr(image_cache, "cached_image_url", lambda u: (_ for _ in ()).throw(RuntimeError("boom")))

    url = "http://localhost:4533/cover.jpg"
    assert normalize_image_url(url) == f"/api/image-proxy?url={quote(url, safe='')}"
