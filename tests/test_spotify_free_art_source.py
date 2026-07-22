"""Spotify cover art must be offered when the FREE metadata source is active.

Sokhi's request: Spotify has the best JP covers and matches MusicBrainz better
than Apple/Deezer there, but the art-source list only offered Spotify to
CONNECTED accounts — free-mode users never saw the row, because availability
went through the registry accessor that requires authentication.
"""

from __future__ import annotations

from types import SimpleNamespace

import core.metadata.art_lookup as al


def _wire(monkeypatch, *, registry_client=None, raw_client=None):
    import core.metadata.registry as reg
    monkeypatch.setattr(reg, "get_client_for_source",
                        lambda source, **kw: registry_client)
    monkeypatch.setattr(reg, "get_spotify_client",
                        lambda *a, **kw: raw_client, raising=False)


class _FreeSpotify:
    def __init__(self, albums=()):
        self._albums = list(albums)
        self.calls = []

    def _free_active(self):
        return True

    def search_albums(self, query, limit=10, allow_fallback=True,
                      artist=None, album=None, prefer_free=False):
        self.calls.append({"query": query, "allow_fallback": allow_fallback,
                           "artist": artist, "album": album})
        return list(self._albums)


def test_free_active_makes_spotify_art_available(monkeypatch):
    _wire(monkeypatch, registry_client=None, raw_client=_FreeSpotify())
    assert al.is_art_source_available("spotify") is True


def test_no_account_and_no_free_stays_unavailable(monkeypatch):
    dormant = _FreeSpotify()
    dormant._free_active = lambda: False
    _wire(monkeypatch, registry_client=None, raw_client=dormant)
    assert al.is_art_source_available("spotify") is False


def test_free_client_serves_the_art_lookup(monkeypatch):
    hit = SimpleNamespace(image_url="https://i.scdn.co/image/abc",
                          name="YOASOBI album", artists=["YOASOBI"])
    client = _FreeSpotify([hit])
    _wire(monkeypatch, registry_client=None, raw_client=client)
    monkeypatch.setattr(al, "_album_matches", lambda *a: True)
    monkeypatch.setattr(al, "_result_album_artist", lambda alb: ("YOASOBI album", "YOASOBI"))

    url = al._spotify_art("YOASOBI", "THE BOOK", {})
    assert url == "https://i.scdn.co/image/abc"
    # source-specific lookup: no cross-source fallback, free album path enabled
    assert client.calls[0]["allow_fallback"] is False
    assert client.calls[0]["artist"] == "YOASOBI" and client.calls[0]["album"] == "THE BOOK"


def test_connected_account_still_preferred(monkeypatch):
    connected = _FreeSpotify()
    _wire(monkeypatch, registry_client=connected, raw_client=None)
    assert al._spotify_art_client() is connected
