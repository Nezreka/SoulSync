"""Export source wiring (#903): waterfall order + cache write-back.

build_resolve_fn assembles cache -> DB -> file -> MusicBrainz and writes a fresh
(non-cache) hit back to the cache. Pins: a cache hit short-circuits everything and is
NOT re-written; a DB/MB hit IS written back; misses fall through; the resolving label is
returned.
"""

from __future__ import annotations

from core.exports.export_sources import build_resolve_fn
from core.exports.mbid_resolver import SRC_CACHE, SRC_DB, SRC_MUSICBRAINZ

MBID = "e8f9b188-f819-4e43-ab0f-4bd26ce9ff56"


def _wire(db=None, file=None, mb=None, cache=None):
    recorded = {}
    store = dict(cache or {})
    fn = build_resolve_fn(
        db_fn=lambda a, t: (db or {}).get((a, t)),
        file_fn=lambda a, t: (file or {}).get((a, t)),
        mb_fn=lambda a, t: (mb or {}).get((a, t)),
        cache_lookup=lambda k: store.get(k),
        cache_record=lambda k, m: recorded.__setitem__(k, m) or True,
    )
    return fn, recorded


def test_cache_hit_short_circuits_and_is_not_rewritten():
    from core.exports.mbid_resolver import normalize_key
    fn, recorded = _wire(
        cache={normalize_key("A", "T"): MBID},
        db={("A", "T"): "should-not-reach"},
    )
    mbid, label = fn("A", "T")
    assert (mbid, label) == (MBID, SRC_CACHE)
    assert recorded == {}                       # cache hit -> no write-back


def test_db_hit_is_written_back_to_cache():
    from core.exports.mbid_resolver import normalize_key
    fn, recorded = _wire(db={("A", "T"): MBID})
    mbid, label = fn("A", "T")
    assert (mbid, label) == (MBID, SRC_DB)
    assert recorded == {normalize_key("A", "T"): MBID}   # fresh hit cached for next time


def test_falls_through_to_musicbrainz_and_caches():
    fn, recorded = _wire(db={}, file={}, mb={("A", "T"): MBID})
    mbid, label = fn("A", "T")
    assert (mbid, label) == (MBID, SRC_MUSICBRAINZ)
    assert list(recorded.values()) == [MBID]


def test_all_miss_returns_none_and_no_write():
    fn, recorded = _wire()
    assert fn("A", "T") == (None, None)
    assert recorded == {}
