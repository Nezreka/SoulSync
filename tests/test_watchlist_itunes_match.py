"""Watchlist iTunes ID backfill (Boulder: recent watchlist artists never got
their iTunes match — 'MetadataService not available' ×8, Backfilled 0/8).

_match_to_itunes was the only matcher with no fallback: it read the PRIVATE
_metadata_service attr, which is None in the normal web_server wiring
(scanner built from a spotify_client), and gave up. It must use the canonical
registry iTunes client like the deezer/discogs/musicbrainz matchers do.
"""

from __future__ import annotations

from types import SimpleNamespace

import core.watchlist_scanner as ws
from core.watchlist_scanner import WatchlistScanner


def _scanner():
    # The normal web_server wiring: spotify_client only, no MetadataService.
    return WatchlistScanner(spotify_client=SimpleNamespace())


def _registry(monkeypatch, results):
    client = SimpleNamespace(search_artists=lambda name, limit=5: results)
    import core.metadata.registry as registry
    monkeypatch.setattr(registry, 'get_itunes_client', lambda *a, **k: client)
    return client


def test_itunes_match_works_without_metadata_service(monkeypatch):
    s = _scanner()
    assert s._metadata_service is None  # the exact production condition
    _registry(monkeypatch, [SimpleNamespace(name='Green Day', id='it123', popularity=80)])

    assert s._match_to_itunes('Green Day') == 'it123'


def test_itunes_match_unconfident_returns_none(monkeypatch):
    s = _scanner()
    _registry(monkeypatch, [SimpleNamespace(name='Completely Different Band', id='x1', popularity=10)])

    assert s._match_to_itunes('Green Day') is None


def test_itunes_match_no_client_returns_none(monkeypatch):
    s = _scanner()
    import core.metadata.registry as registry
    monkeypatch.setattr(registry, 'get_itunes_client', lambda *a, **k: None)

    assert s._match_to_itunes('Green Day') is None
