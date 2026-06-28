"""Wishlist art enrichment (read-path): library-sourced items (re-downloads, preview-clip
re-fetches) store media-server RELATIVE thumb paths that don't render in a browser, and the
nebula only has artist photos for watchlisted artists. _enrich_wishlist_images fixes both on
read — normalizing relative/internal image URLs (leaving CDN URLs untouched) and building an
artist-name -> library-photo map — so even items already sitting in the wishlist get fixed."""

from __future__ import annotations

import sqlite3

import pytest

from core.wishlist import routes


class _DB:
    def __init__(self, artists):
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute("CREATE TABLE artists (name TEXT, thumb_url TEXT)")
        self._conn.executemany("INSERT INTO artists VALUES (?, ?)", artists)
        self._conn.commit()

    def _get_connection(self):
        return self._conn


@pytest.fixture(autouse=True)
def _stub_normalize(monkeypatch):
    # Deterministic stand-in for the real Plex/Jellyfin URL rebuild.
    monkeypatch.setattr(routes, "normalize_image_url", lambda u: f"PROXY({u})")


def test_needs_image_fix_predicate():
    assert routes._needs_image_fix("/library/metadata/1/thumb/2") is True
    assert routes._needs_image_fix("/Items/x/Images/Primary") is True
    assert routes._needs_image_fix("http://localhost:32400/library/x") is True
    assert routes._needs_image_fix("https://i.scdn.co/image/ab") is False
    assert routes._needs_image_fix("https://is1.mzstatic.com/600x600bb.jpg") is False
    assert routes._needs_image_fix("") is False
    assert routes._needs_image_fix(None) is False


def test_relative_album_image_is_normalized():
    tracks = [{"artist_name": "A",
               "spotify_data": {"album": {"images": [{"url": "/library/metadata/9/thumb/1"}]}}}]
    routes._enrich_wishlist_images(tracks, _DB([]))
    assert tracks[0]["spotify_data"]["album"]["images"][0]["url"] == "PROXY(/library/metadata/9/thumb/1)"


def test_cdn_album_image_is_left_untouched():
    """Items that already render must not change — guards against regressing normal wishlist art."""
    url = "https://i.scdn.co/image/ab67616d"
    tracks = [{"artist_name": "A", "spotify_data": {"album": {"images": [{"url": url}]}}}]
    routes._enrich_wishlist_images(tracks, _DB([]))
    assert tracks[0]["spotify_data"]["album"]["images"][0]["url"] == url


def test_builds_artist_image_map_from_library():
    tracks = [
        {"artist_name": "Modest Mouse", "spotify_data": {"album": {"images": []}}},
        {"artist_name": "Unknown Artist", "spotify_data": {}},   # skipped
    ]
    db = _DB([("Modest Mouse", "/library/metadata/111/thumb/9"),
              ("Other Band", "/library/metadata/222/thumb/9")])  # not in wishlist → not returned
    amap = routes._enrich_wishlist_images(tracks, db)
    assert amap == {"modest mouse": "PROXY(/library/metadata/111/thumb/9)"}


def test_artist_with_empty_thumb_is_omitted():
    tracks = [{"artist_name": "NoArt", "spotify_data": {"album": {"images": []}}}]
    amap = routes._enrich_wishlist_images(tracks, _DB([("NoArt", "")]))
    assert amap == {}


def test_already_proxied_artist_thumb_not_double_wrapped():
    """A library thumb that's already a CDN/proxy URL passes through unchanged (idempotent)."""
    tracks = [{"artist_name": "B", "spotify_data": {"album": {"images": []}}}]
    amap = routes._enrich_wishlist_images(tracks, _DB([("B", "https://i.scdn.co/image/cdn")]))
    assert amap == {"b": "https://i.scdn.co/image/cdn"}


def test_handles_string_or_missing_spotify_data_gracefully():
    tracks = [
        {"artist_name": "A", "spotify_data": "not-a-dict"},
        {"artist_name": "B"},
        {"spotify_data": {"album": {"images": [{"url": "/library/x"}]}}},  # no artist_name
    ]
    amap = routes._enrich_wishlist_images(tracks, _DB([("A", "/library/a")]))
    # third track's relative album image still gets fixed
    assert tracks[2]["spotify_data"]["album"]["images"][0]["url"] == "PROXY(/library/x)"
    assert amap == {"a": "PROXY(/library/a)"}
