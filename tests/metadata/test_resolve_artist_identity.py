"""Resolve an artist NAME to a provider identity (source + id + image + genres).

Backs Library v2's native-artist enrichment: a native artist has only a name,
so it must be resolved by walking the source-priority chain and taking the
strict exact-name match (the #988 matcher) — never an unrelated popular artist.
"""

from __future__ import annotations

import types

from core.metadata import album_tracks
from core.metadata import registry as metadata_registry


def _patch_sources(monkeypatch, clients):
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda **k: "deezer")
    monkeypatch.setattr(
        metadata_registry, "get_source_priority",
        lambda primary: ["deezer", "spotify", "itunes"],
    )
    monkeypatch.setattr(
        metadata_registry, "get_client_for_source",
        lambda source, **k: clients.get(source),
    )


class _Client:
    def __init__(self, results):
        self._results = list(results)

    def search_artists(self, query, **kwargs):
        return list(self._results)


def _artist(artist_id, name, image_url=None, genres=None):
    return types.SimpleNamespace(
        id=artist_id, name=name, image_url=image_url, genres=genres or []
    )


def test_returns_exact_match_from_first_source_with_id(monkeypatch):
    _patch_sources(monkeypatch, {
        "deezer": _Client([_artist("DZ1", "Afrojack", image_url="http://p/afro", genres=["edm"])]),
    })

    ident = album_tracks.resolve_artist_identity("Afrojack")

    assert ident == {
        "source": "deezer",
        "artist_id": "DZ1",
        "name": "Afrojack",
        "image_url": "http://p/afro",
        "genres": ["edm"],
    }


def test_falls_through_to_next_source_when_first_has_no_match(monkeypatch):
    _patch_sources(monkeypatch, {
        "deezer": _Client([_artist("DZX", "Totally Different Band")]),
        "spotify": _Client([_artist("SP9", "Afrojack", image_url="http://p/sp")]),
    })

    ident = album_tracks.resolve_artist_identity("Afrojack")

    assert ident["source"] == "spotify"
    assert ident["artist_id"] == "SP9"


def test_returns_none_when_no_source_has_an_exact_or_close_match(monkeypatch):
    _patch_sources(monkeypatch, {
        "deezer": _Client([_artist("DZ1", "Big Sean")]),
        "spotify": _Client([_artist("SP1", "BabyTron")]),
    })

    assert album_tracks.resolve_artist_identity("Big Sean and BabyTron") is None


def test_returns_none_for_blank_name(monkeypatch):
    _patch_sources(monkeypatch, {"deezer": _Client([_artist("DZ1", "Anything")])})
    assert album_tracks.resolve_artist_identity("   ") is None
