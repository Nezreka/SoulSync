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
        "Sparkle", "mbid-yamashita", limit=5, raise_on_error=True)

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


def test_e_a_raising_fallback_returns_none_and_caches_nothing(service):
    service.mb_client.search_recording.return_value = []
    # An exception in the fallback must not escape match_recording, must not
    # be mistaken for a positive result — and must not be written down as a
    # miss either: it is an outage, not an answer.
    service._resolve_unambiguous_artist_mbid = MagicMock(
        side_effect=RuntimeError("musicbrainz is down"))

    out = service.match_recording("Sparkle", "Tatsuro Yamashita")

    assert out is None
    service.mb_client.search_recording_by_artist_mbid.assert_not_called()
    service._save_to_cache.assert_not_called()


def test_e2_a_transient_pinned_search_failure_is_not_cached_as_a_miss(service):
    service.mb_client.search_recording.return_value = []
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-yamashita", "name": "Tatsuro Yamashita", "score": 100},
    ]
    service.mb_client.search_recording_by_artist_mbid.side_effect = RuntimeError("503")

    assert service.match_recording("Sparkle", "Tatsuro Yamashita") is None
    service.mb_client.search_recording_by_artist_mbid.assert_called_once_with(
        "Sparkle", "mbid-yamashita", limit=5, raise_on_error=True)
    # The artist pin itself was a real answer and may be cached; the
    # recording must not be.
    assert not [c for c in service._save_to_cache.call_args_list
                if c.args[0] == "recording"]


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
    service._save_to_cache.assert_not_called()


def test_g_a_shared_exact_name_is_refused_whatever_the_score_gap(service):
    # Live shape of a bare "Nirvana" query: MusicBrainz scales the best hit
    # to 100 and the other same-named artists trail by 19+ points. A score
    # gap is not evidence of which Nirvana the caller meant, so the name
    # being shared refuses the pin outright.
    service.mb_client.search_recording.return_value = []
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-1", "name": "Nirvana", "score": 100},
        {"id": "mbid-2", "name": "Nirvana", "score": 81},
        {"id": "mbid-3", "name": "Approaching Nirvana", "score": 75},
        {"id": "mbid-4", "name": "Nirvana", "score": 70},
    ]

    out = service.match_recording("Sparkle", "Nirvana")

    assert out is None
    service.mb_client.search_recording_by_artist_mbid.assert_not_called()


def test_g2_an_unknown_name_is_not_pinned_to_the_top_scoring_stranger(service):
    # Live shape of a bare query for a name MusicBrainz has never heard of:
    # the special-purpose "[unknown]" artist at 100 with a 78-point
    # runner-up. Pinning would then run `arid:[unknown] AND recording:"..."`,
    # and [unknown] has dozens of recordings under common titles.
    service.mb_client.search_recording.return_value = []
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-unknown", "name": "[unknown]", "score": 100},
        {"id": "mbid-2", "name": "Some Shitty Cover Band", "score": 78},
        {"id": "mbid-3", "name": "Cover Band", "score": 71},
    ]

    out = service.match_recording("Yesterday", "Some Unknown Cover Band")

    assert out is None
    service.mb_client.search_recording_by_artist_mbid.assert_not_called()
    # ...and the refusal is remembered per artist, so the next track by the
    # same name does not pay the artist search again.
    service._save_to_cache.assert_any_call(
        "artist_recording_pin", "Some Unknown Cover Band", None, None,
        {"id": "mbid-unknown", "name": "[unknown]", "score": 100}, 100)


def test_g3_an_exact_alias_hit_pins_the_native_script_entity(service):
    # The motivating case as MusicBrainz actually returns it: the entity is
    # named in kanji, the romanised query appears only in its alias list.
    service.mb_client.search_recording.return_value = []
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-yamashita", "name": "山下達郎", "score": 100,
         "aliases": [{"name": "Tatsu Yamashita"}, {"name": "Tatsuro Yamashita"}]},
        {"id": "mbid-2", "name": "tatsuro", "score": 56},
        {"id": "mbid-3", "name": "鈴木達郎", "score": 52,
         "aliases": [{"name": "Tatsuro Suzuki"}]},
    ]
    service.mb_client.search_recording_by_artist_mbid.return_value = [
        {"id": "rec-sparkle", "title": "Sparkle", "score": 100},
    ]

    out = service.match_recording("Sparkle", "Tatsuro Yamashita")

    assert out is not None and out["mbid"] == "rec-sparkle"
    service.mb_client.search_recording_by_artist_mbid.assert_called_once_with(
        "Sparkle", "mbid-yamashita", limit=5, raise_on_error=True)


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
