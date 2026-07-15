"""#1026 (QT3496): opening an artist FROM a specific source's card must show
that source's catalog — even when the artist is already owned.

The artist-detail route upgrades a source-card click to the rich library view
when the artist is in the library; that view's discography walks the source
chain. Before the fix the route never passed the clicked source, so the chain
started at the PRIMARY: with Deezer primary, an Apple Music card rendered
Deezer's 2-album Afroman instead of iTunes' 37. (Unowned artists take the
source-only path, which always honoured the source — which is why the report
only reproduced for owned artists.)

These pin the chain mechanics the route's ``source_override=source_param``
now relies on.
"""

from __future__ import annotations

import core.metadata.discography as disco
from core.metadata.lookup import MetadataLookupOptions


def _fake_registry(monkeypatch, primary, chain):
    monkeypatch.setattr(disco.metadata_registry, 'get_primary_source', lambda: primary)
    monkeypatch.setattr(disco.metadata_registry, 'get_source_priority', lambda p: list(chain))


def test_source_override_leads_the_chain(monkeypatch):
    _fake_registry(monkeypatch, 'deezer', ['deezer', 'spotify', 'itunes'])
    chain = disco._get_source_chain_for_lookup(MetadataLookupOptions(source_override='itunes'))
    assert chain == ['itunes', 'deezer', 'spotify']   # clicked source first, priority backstops


def test_no_override_keeps_the_primary_chain(monkeypatch):
    _fake_registry(monkeypatch, 'deezer', ['deezer', 'spotify', 'itunes'])
    chain = disco._get_source_chain_for_lookup(MetadataLookupOptions())
    assert chain == ['deezer', 'spotify', 'itunes']   # library-page opens: unchanged


def test_override_matching_primary_is_a_noop(monkeypatch):
    _fake_registry(monkeypatch, 'deezer', ['deezer', 'spotify', 'itunes'])
    chain = disco._get_source_chain_for_lookup(MetadataLookupOptions(source_override='deezer'))
    assert chain == ['deezer', 'spotify', 'itunes']


def test_override_respects_no_fallback(monkeypatch):
    _fake_registry(monkeypatch, 'deezer', ['deezer', 'spotify', 'itunes'])
    chain = disco._get_source_chain_for_lookup(
        MetadataLookupOptions(source_override='itunes', allow_fallback=False))
    assert chain == ['itunes']
