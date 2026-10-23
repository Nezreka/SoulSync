"""A media-server image URL must be the same URL every time.

`normalize_image_url` built Navidrome's Subsonic auth with a fresh
`secrets.token_hex()` salt on every call, so the same cover produced a different
URL each time it was normalized. The image cache keys on the full URL
(`ImageCache.key_for_url`), so one artist photo minted a brand-new cache entry
per page render: a production cache held 857 rows, 602 of them `pending`
registrations that had already been superseded by the next render's URL.

Also pinned here: SoulSync's own image endpoints are never run through the
media-server rebuild. `/api/library/v2/artwork/...` starts with `/api/`, which
this function reads as "old Navidrome API path" — it would have rewritten a
working local artwork URL into an unreachable Subsonic one.
"""

from __future__ import annotations

import pytest

from core.metadata import artwork


class _Config:
    def __init__(self, server="navidrome"):
        self._server = server

    def get_active_media_server(self):
        return self._server

    def get_navidrome_config(self):
        return {"base_url": "http://nav.local", "username": "u", "password": "secret"}

    def get(self, *args, **kwargs):
        return kwargs.get("default") if kwargs else (args[1] if len(args) > 1 else None)


@pytest.fixture()
def navidrome(monkeypatch):
    monkeypatch.setattr(artwork, "get_config_manager", lambda: _Config())
    # Keep the assertion on the URL this function BUILDS; the cache wrapper is
    # exercised by its own tests.
    monkeypatch.setattr(artwork, "_browser_safe_image_url", lambda url: url)


def test_the_same_cover_normalizes_to_the_same_url(navidrome):
    first = artwork.normalize_image_url("/rest/getCoverArt.view?id=al-1")
    second = artwork.normalize_image_url("/rest/getCoverArt.view?id=al-1")
    assert first == second


def test_different_covers_still_get_different_urls(navidrome):
    one = artwork.normalize_image_url("/rest/getCoverArt.view?id=al-1")
    two = artwork.normalize_image_url("/rest/getCoverArt.view?id=al-2")
    assert one != two


def test_the_url_still_carries_subsonic_auth(navidrome):
    url = artwork.normalize_image_url("/rest/getCoverArt.view?id=al-1")
    assert "u=u" in url and "&t=" in url and "&s=" in url
    # The salt is a digest of the password, never the password itself.
    assert "secret" not in url


def test_soulsync_artwork_urls_pass_through_untouched(navidrome):
    for url in ("/api/library/v2/artwork/album/12",
                "/api/library/v2/artwork/artist/7?v=1700000000",
                "/api/image-cache/" + "a" * 64,
                "/api/image-proxy?url=https%3A%2F%2Fexample.test%2Fa.jpg"):
        assert artwork.normalize_image_url(url) == url


def test_a_real_navidrome_api_path_is_still_rebuilt(navidrome):
    """The `/api/` rule exists for legacy Navidrome paths; narrowing it must not
    turn that off."""
    rebuilt = artwork.normalize_image_url("/api/img/cover/al-1")
    assert rebuilt.startswith("http://nav.local/api/img/cover/al-1?")
