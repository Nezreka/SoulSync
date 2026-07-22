"""MBID resolution waterfall for playlist export (#903).

Pins: cheapest-source-first short-circuit (don't hit MusicBrainz when the DB has it),
invalid MBIDs are treated as misses, a raising source doesn't abort the export, and the
resolving source label is reported (for the live status breakdown).
"""

from __future__ import annotations

from core.exports.mbid_resolver import (
    SRC_CACHE,
    SRC_DB,
    SRC_MUSICBRAINZ,
    normalize_key,
    resolve_recording_mbid,
)

MBID = "e8f9b188-f819-4e43-ab0f-4bd26ce9ff56"
MBID2 = "8f3471b5-7e6a-4c1f-9c1a-2b2b2b2b2b2b"


def _src(label, value):
    return (label, lambda a, t: value)


def test_returns_first_hit_with_label():
    mbid, label = resolve_recording_mbid("A", "T", [_src(SRC_DB, MBID), _src(SRC_MUSICBRAINZ, MBID2)])
    assert (mbid, label) == (MBID, SRC_DB)


def test_short_circuits_expensive_sources():
    called = {"mb": False}
    def mb(a, t):
        called["mb"] = True
        return MBID2
    mbid, label = resolve_recording_mbid("A", "T", [_src(SRC_CACHE, MBID), (SRC_MUSICBRAINZ, mb)])
    assert (mbid, label) == (MBID, SRC_CACHE)
    assert called["mb"] is False  # cache hit -> MusicBrainz never queried


def test_falls_through_misses_to_later_source():
    mbid, label = resolve_recording_mbid("A", "T", [
        _src(SRC_CACHE, None),
        _src(SRC_DB, ""),
        _src(SRC_MUSICBRAINZ, MBID2),
    ])
    assert (mbid, label) == (MBID2, SRC_MUSICBRAINZ)


def test_invalid_mbid_is_a_miss():
    mbid, label = resolve_recording_mbid("A", "T", [
        _src(SRC_DB, "not-a-uuid"),
        _src(SRC_MUSICBRAINZ, MBID),
    ])
    assert (mbid, label) == (MBID, SRC_MUSICBRAINZ)


def test_raising_source_does_not_abort():
    def boom(a, t):
        raise RuntimeError("MusicBrainz timeout")
    mbid, label = resolve_recording_mbid("A", "T", [
        (SRC_DB, boom),
        _src(SRC_MUSICBRAINZ, MBID),
    ])
    assert (mbid, label) == (MBID, SRC_MUSICBRAINZ)


def test_all_miss_returns_none():
    assert resolve_recording_mbid("A", "T", [_src(SRC_DB, None), _src(SRC_MUSICBRAINZ, None)]) == (None, None)
    assert resolve_recording_mbid("A", "T", []) == (None, None)


def test_normalize_key_is_stable_across_variations():
    assert normalize_key("The Beatles", "Hey Jude!") == normalize_key("the beatles", "hey  jude")
    assert normalize_key("A", "X") != normalize_key("B", "X")
