"""Regression tests for image URL normalization."""

from __future__ import annotations

import pytest
from urllib.parse import quote


@pytest.mark.parametrize(
    "thumb_url",
    [
        "/api/image-proxy?url=https%3A%2F%2Fexample.com%2Fcover.jpg",
        "http://host.docker.internal:4533/api/image-proxy?u=ketiska&t=abc&s=def&v=1.16.1&c=SoulSync&f=json",
    ],
)
def test_normalize_image_url_leaves_existing_image_proxy_urls_alone(thumb_url):
    """Existing proxy URLs should not be wrapped in a second proxy layer."""
    from core.metadata import normalize_image_url

    assert normalize_image_url(thumb_url) == thumb_url


def test_normalize_image_url_proxies_internal_http_urls(monkeypatch):
    """Raw internal image URLs should still be routed through SoulSync's proxy."""
    from core.metadata import normalize_image_url
    from core.metadata import artwork

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

    url = "http://localhost:4533/cover.jpg"
    assert normalize_image_url(url) == f"/api/image-proxy?url={quote(url, safe='')}"
