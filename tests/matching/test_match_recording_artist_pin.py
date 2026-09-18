"""`match_recording` has to reach a recording credited in the artist's
native script, even when the caller only has a romanised/cross-script name.

Root cause: MusicBrainz's `/recording` search field-scopes `artist` to the
CREDIT printed on that specific recording — never the artist entity's
aliases, and there is no alias field on `/recording` at all. So
`search_recording(strict=True)`'s `artist:"..."` clause structurally cannot
match `"Tatsuro Yamashita"` against a recording credited `山下達郎`, however
exact the title is (verified live: `artist:"Tatsuro Yamashita" AND
recording:"Sparkle"` → 0 hits; artist search `"Tatsuro Yamashita"` → 山下達郎
score 100; `arid:<id> AND recording:"Sparkle"` → hit).

The fix: when the plain name+artist search finds nothing usable, resolve the
artist via the alias-aware `search_artist(strict=False)` and retry the
recording search pinned to that MBID (`arid:<mbid>`) instead of the printed
credit text — but only when that artist resolution is unambiguous, since a
wrong pin here is a wrong recording match written into the cache.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.musicbrainz_service import MusicBrainzService


@pytest.fixture
def service():
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    return svc


def test_a_cross_script_artist_is_pinned_and_matched(service):
    # Strict name+artist search finds nothing — the credit is in kanji.
    service.mb_client.search_recording.return_value = []
    # Alias-aware artist search resolves unambiguously: a single, high-score
    # candidate.
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-yamashita", "name": "Tatsuro Yamashita", "score": 100},
    ]
    # The pinned retry finds the recording via arid:.
    service.mb_client.search_recording_by_artist_mbid.return_value = [
        {"id": "rec-sparkle", "title": "Sparkle", "score": 100},
    ]

    out = service.match_recording("Sparkle", "Tatsuro Yamashita")

    assert out is not None
    assert out["mbid"] == "rec-sparkle"
    assert out["confidence"] >= 70

    service.mb_client.search_artist.assert_called_once_with(
        "Tatsuro Yamashita", limit=5, strict=False, raise_on_error=True)
    service.mb_client.search_recording_by_artist_mbid.assert_called_once_with(
        "Sparkle", "mbid-yamashita", limit=5)

    # The positive result lands in the SAME (track, artist) cache key the
    # plain path uses, so `_check_cache` still short-circuits it next time.
    service._save_to_cache.assert_any_call(
        "recording", "Sparkle", "Tatsuro Yamashita", "rec-sparkle",
        {"id": "rec-sparkle", "title": "Sparkle", "score": 100}, 100)


def test_b_an_ambiguous_artist_resolution_is_refused_no_pinned_call(service):
    service.mb_client.search_recording.return_value = []
    # Two same-scoring-ish, differently-named candidates — MusicBrainz did
    # not land on one entity.
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-1", "name": "Foo Bar", "score": 100},
        {"id": "mbid-2", "name": "Baz Qux", "score": 95},
    ]

    out = service.match_recording("Sparkle", "Foo")

    assert out is None
    service.mb_client.search_recording_by_artist_mbid.assert_not_called()


def test_c_title_gate_failure_on_strict_still_triggers_the_fallback(service):
    # Strict search returns something, but nothing on it is actually the
    # right title — every candidate fails the 0.6 similarity gate.
    service.mb_client.search_recording.return_value = [
        {"id": "rec-wrong", "title": "A Completely Unrelated Song Title",
         "score": 90, "artist-credit": [{"artist": {"name": "Tatsuro Yamashita"}}]},
    ]
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-yamashita", "name": "Tatsuro Yamashita", "score": 100},
    ]
    service.mb_client.search_recording_by_artist_mbid.return_value = [
        {"id": "rec-sparkle", "title": "Sparkle", "score": 100},
    ]

    out = service.match_recording("Sparkle", "Tatsuro Yamashita")

    assert out is not None
    assert out["mbid"] == "rec-sparkle"
    service.mb_client.search_recording_by_artist_mbid.assert_called_once()


def test_d_a_normal_strict_hit_never_touches_artist_resolution(service):
    # The happy path: strict search finds the right recording under the
    # printed credit directly. No fallback traffic at all.
    service.mb_client.search_recording.return_value = [
        {"id": "rec-money", "title": "Money", "score": 100,
         "artist-credit": [{"artist": {"name": "Pink Floyd"}}]},
    ]

    out = service.match_recording("Money", "Pink Floyd")

    assert out is not None
    assert out["mbid"] == "rec-money"
    service.mb_client.search_artist.assert_not_called()
    service.mb_client.search_recording_by_artist_mbid.assert_not_called()


def test_e_a_raising_fallback_fails_soft_and_caches_nothing_positive(service):
    service.mb_client.search_recording.return_value = []
    # Any exception anywhere in the fallback must not propagate and must not
    # be mistaken for a positive result.
    service._resolve_unambiguous_artist_mbid = MagicMock(
        side_effect=RuntimeError("musicbrainz is down"))

    out = service.match_recording("Sparkle", "Tatsuro Yamashita")

    assert out is None
    service.mb_client.search_recording_by_artist_mbid.assert_not_called()
    # The cache row written for this miss must not carry a musicbrainz id.
    recording_calls = [
        c for c in service._save_to_cache.call_args_list
        if c.args[0] == "recording"
    ]
    assert recording_calls
    assert recording_calls[-1].args[3] is None


def test_f_a_transient_search_artist_failure_is_not_cached_as_ambiguous(service):
    # search_artist is fail-soft by default and would otherwise collapse a
    # timeout/5xx into the same [] it uses for "no such artist" — caching
    # THAT as a negative pin result would silence this whole fallback for
    # the cache row's TTL off the back of one outage. raise_on_error=True
    # must be requested, and the raise itself must not be cached either way.
    service.mb_client.search_recording.return_value = []
    service.mb_client.search_artist.side_effect = RuntimeError("timeout")

    out = service.match_recording("Sparkle", "Tatsuro Yamashita")

    assert out is None
    service.mb_client.search_artist.assert_called_once_with(
        "Tatsuro Yamashita", limit=5, strict=False, raise_on_error=True)
    pin_cache_calls = [
        c for c in service._save_to_cache.call_args_list
        if c.args[0] == "artist_recording_pin"
    ]
    assert pin_cache_calls == []


def test_g_a_tied_exact_name_match_is_refused(service):
    # Three MB artists literally named "Nirvana", scores within 10 of each
    # other. The top result's name equalling the query is only a rescue when
    # it is the ONLY candidate that ties on the exact name — here it is not,
    # so the tie must not be pinned.
    service.mb_client.search_recording.return_value = []
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-1", "name": "Nirvana", "score": 95},
        {"id": "mbid-2", "name": "Nirvana", "score": 92},
        {"id": "mbid-3", "name": "Nirvana", "score": 90},
    ]

    out = service.match_recording("Sparkle", "Nirvana")

    assert out is None
    service.mb_client.search_recording_by_artist_mbid.assert_not_called()


def test_h_pinned_candidates_failing_the_title_gate_still_return_none(service):
    service.mb_client.search_recording.return_value = []
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-yamashita", "name": "Tatsuro Yamashita", "score": 100},
    ]
    # The pinned search resolves the artist correctly, but nothing it finds
    # is actually the requested track.
    service.mb_client.search_recording_by_artist_mbid.return_value = [
        {"id": "rec-wrong", "title": "A Completely Different Song", "score": 90},
    ]

    out = service.match_recording("Sparkle", "Tatsuro Yamashita")

    assert out is None
    recording_calls = [
        c for c in service._save_to_cache.call_args_list
        if c.args[0] == "recording"
    ]
    assert recording_calls
    last_call = recording_calls[-1]
    assert last_call.args[3] is None   # musicbrainz_id
    assert last_call.args[5] == 0      # confidence
