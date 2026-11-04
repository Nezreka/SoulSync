"""A cached image that cannot be served sends the browser to its original
(#1763: no 404 bursts on page load), but never to a URL carrying a server
token: the fork keeps its redacted 404 reason for those."""

from __future__ import annotations

import pytest
from flask import Flask

discover_routes = pytest.importorskip("api.discover_routes")

KEY = "a" * 64


class _Cache:
    def __init__(self, original):
        self.original = original

    def get(self, key):
        raise RuntimeError("upstream refused https://plex.lan/x?X-Plex-Token=secret")

    def get_original_url(self, key):
        return self.original


@pytest.fixture
def serve(monkeypatch):
    import core.image_cache as image_cache

    def run(original):
        monkeypatch.setattr(image_cache, "get_image_cache", lambda: _Cache(original))
        with Flask(__name__).test_request_context(f"/api/image-cache/{KEY}"):
            return discover_routes.serve_cached_image(KEY)
    return run


def test_a_plain_original_is_a_redirect(serve):
    response = serve("https://cdn.example/cover.jpg")
    assert response.status_code == 302
    assert response.headers["Location"] == "https://cdn.example/cover.jpg"


def test_an_original_with_a_server_token_is_never_handed_to_the_browser(serve):
    response = serve("https://plex.lan/library/metadata/1/thumb?X-Plex-Token=secret")
    assert response.status_code == 404
    assert "secret" not in response.headers["X-SoulSync-Image-Error"]
    assert "Location" not in response.headers


def test_no_original_keeps_the_404_with_its_reason(serve):
    response = serve(None)
    assert response.status_code == 404
    assert response.headers["X-SoulSync-Image-Error"].startswith("upstream refused")
