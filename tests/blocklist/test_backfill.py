"""Cross-source ID backfill (pure resolver layer)."""

from __future__ import annotations

from core.blocklist.backfill import resolve_missing_ids


def _resolvers(**by_source):
    # each value is the id that source returns (or None)
    return {src: (lambda et, n, p, _v=v: _v) for src, v in by_source.items()}


def test_fills_only_missing_sources():
    entry = {"entity_type": "artist", "name": "Drake", "spotify_id": "sp-known"}
    resolvers = _resolvers(spotify="SHOULD-NOT-BE-USED", itunes="it-new", deezer="dz-new")
    out = resolve_missing_ids(entry, resolvers)
    assert out == {"itunes_id": "it-new", "deezer_id": "dz-new"}  # spotify skipped (known)


def test_resolver_returning_none_leaves_source_unmatched():
    entry = {"entity_type": "album", "name": "Some Album"}
    resolvers = _resolvers(spotify="sp", itunes=None, deezer=None, musicbrainz="mb")
    out = resolve_missing_ids(entry, resolvers)
    assert out == {"spotify_id": "sp", "musicbrainz_id": "mb"}


def test_resolver_exception_is_swallowed():
    def boom(et, n, p):
        raise RuntimeError("source down")
    entry = {"entity_type": "artist", "name": "X"}
    out = resolve_missing_ids(entry, {"spotify": boom, "deezer": lambda et, n, p: "dz"})
    assert out == {"deezer_id": "dz"}


def test_no_name_or_type_returns_empty():
    assert resolve_missing_ids({"entity_type": "artist"}, _resolvers(spotify="x")) == {}
    assert resolve_missing_ids({"name": "X"}, _resolvers(spotify="x")) == {}


def test_resolver_receives_type_name_and_parent():
    seen = {}
    def capture(et, n, p):
        seen.update(entity_type=et, name=n, parent=p)
        return "id1"
    resolve_missing_ids(
        {"entity_type": "album", "name": "Scorpion", "parent_name": "Drake"},
        {"spotify": capture})
    assert seen == {"entity_type": "album", "name": "Scorpion", "parent": "Drake"}
