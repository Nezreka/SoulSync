"""`match_recording` must not pick a same-named band's recording (#1509).

The reported shape: `match_recording("Mammoth", "Mammoth")` scores
candidates on title similarity + the PRINTED credit text only. Another band
literally named "Mammoth" gets the full artist bonus (its credit IS the
query string) while Mammoth WVH's recordings are credited "Mammoth WVH",
so the wrong band's recording wins with confidence >= 70 — even though
`match_artist` had already resolved the right artist MBID for the same
file, and even though the wrong recording is 61s off the file's length.

The fix, pinned here:
1. When the caller passes `artist_mbid`, candidates must be credited to
   that identity (hard gate on the artist-credit MBIDs).
2. When the caller passes `duration_ms`, candidates whose MB `length` is
   further off than RECORDING_DURATION_TOLERANCE_MS are rejected.
3. A known artist MBID becomes part of the cache key, so a name-only
   cached wrong match can never be served to an identity-pinned lookup.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.musicbrainz_service import MusicBrainzService


WRONG_BAND_MBID = "325fa98e-1111-4444-8888-aaaaaaaaaaaa"
RIGHT_ARTIST_MBID = "49a6efb9-9b52-44ce-8167-7cb1c21a8c45"
WRONG_RECORDING = "66019226-a575-4e33-80ec-be566ce148d8"
RIGHT_RECORDING = "6a09647d-4250-4264-b67a-bef0260ca1e2"


@pytest.fixture
def service():
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    return svc


def _candidates():
    # What MB actually returns for recording:"Mammoth" AND artist:"Mammoth":
    # the wrong band's recording credited exactly "Mammoth" (209s), and
    # Mammoth WVH's credited "Mammoth WVH" (270s).
    return [
        {"id": WRONG_RECORDING, "title": "Mammoth", "score": 100, "length": 209000,
         "artist-credit": [{"artist": {"id": WRONG_BAND_MBID, "name": "Mammoth"}}]},
        {"id": RIGHT_RECORDING, "title": "Mammoth", "score": 95, "length": 270000,
         "artist-credit": [{"artist": {"id": RIGHT_ARTIST_MBID, "name": "Mammoth WVH"}}]},
    ]


def test_a_known_artist_mbid_rejects_the_same_named_band(service):
    service.mb_client.search_recording.return_value = _candidates()

    out = service.match_recording("Mammoth", "Mammoth", artist_mbid=RIGHT_ARTIST_MBID)

    assert out is not None
    assert out["mbid"] == RIGHT_RECORDING
    # No pinned retry needed — the plain search had the right answer once
    # the wrong band's credit was gated out.
    service.mb_client.search_recording_by_artist_mbid.assert_not_called()


def test_b_wrong_mbid_means_no_match_not_a_wrong_match(service):
    # The known identity genuinely has no such recording on MB: the only
    # candidates belong to other bands, and the arid: retry finds nothing.
    service.mb_client.search_recording.return_value = _candidates()
    service.mb_client.search_recording_by_artist_mbid.return_value = []

    out = service.match_recording("Mammoth", "Mammoth", artist_mbid="deadbeef-0000-0000-0000-000000000000")

    assert out is None
    # ...and the miss is cached under the identity-pinned key, not the
    # bare (track, name) key a later name-only lookup would read.
    service._save_to_cache.assert_any_call(
        "recording", "Mammoth", "Mammoth [mbid:deadbeef-0000-0000-0000-000000000000]",
        None, None, 0)


def test_c_duration_gate_rejects_a_far_off_candidate(service):
    # 270s file: the 209s same-named recording must not win on credit text
    # alone — the 270s recording does.
    service.mb_client.search_recording.return_value = _candidates()

    out = service.match_recording("Mammoth", "Mammoth", duration_ms=270000)

    assert out is not None
    assert out["mbid"] == RIGHT_RECORDING


def test_c2_duration_gate_alone_yields_no_match_not_a_wrong_match(service):
    # Only the far-off candidate exists: the gate rejects it, the name
    # fallback resolves nothing (ambiguous), so no recording is picked
    # instead of a wrong one.
    service.mb_client.search_recording.return_value = [_candidates()[0]]
    service.mb_client.search_artist.return_value = []

    out = service.match_recording("Mammoth", "Mammoth", duration_ms=270000)

    assert out is None


def test_d_duration_gate_keeps_a_close_candidate(service):
    # 270s file vs the 270s recording: the gate passes it and the match
    # lands (name-only behaviour is unchanged when nothing is rejected).
    service.mb_client.search_recording.return_value = [
        {"id": RIGHT_RECORDING, "title": "Mammoth", "score": 95, "length": 270000,
         "artist-credit": [{"artist": {"id": RIGHT_ARTIST_MBID, "name": "Mammoth WVH"}}]},
    ]

    out = service.match_recording("Mammoth", "Mammoth", duration_ms=270000)

    assert out is not None
    assert out["mbid"] == RIGHT_RECORDING


def test_e_missing_length_is_not_punished(service):
    # A candidate with no MB length must not be rejected by the duration
    # gate — missing data is not evidence of a wrong recording.
    service.mb_client.search_recording.return_value = [
        {"id": RIGHT_RECORDING, "title": "Mammoth", "score": 95,
         "artist-credit": [{"artist": {"id": RIGHT_ARTIST_MBID, "name": "Mammoth WVH"}}]},
    ]

    out = service.match_recording("Mammoth", "Mammoth", duration_ms=270000)

    assert out is not None
    assert out["mbid"] == RIGHT_RECORDING


def test_f_pinned_retry_uses_the_caller_supplied_mbid_directly(service):
    # Plain search finds nothing under the printed name; the caller already
    # knows the MBID, so no artist re-resolution is spent — straight to the
    # arid: query.
    service.mb_client.search_recording.return_value = []
    service.mb_client.search_recording_by_artist_mbid.return_value = [
        {"id": RIGHT_RECORDING, "title": "Mammoth", "score": 100, "length": 270000,
         "artist-credit": [{"artist": {"id": RIGHT_ARTIST_MBID, "name": "Mammoth WVH"}}]},
    ]

    out = service.match_recording("Mammoth", "Mammoth", artist_mbid=RIGHT_ARTIST_MBID)

    assert out is not None
    assert out["mbid"] == RIGHT_RECORDING
    service.mb_client.search_artist.assert_not_called()
    service.mb_client.search_recording_by_artist_mbid.assert_called_once_with(
        "Mammoth", RIGHT_ARTIST_MBID, limit=5, raise_on_error=True)


def test_g_identity_pinned_cache_key_includes_the_mbid(service):
    service.mb_client.search_recording.return_value = _candidates()

    service.match_recording("Mammoth", "Mammoth", artist_mbid=RIGHT_ARTIST_MBID)

    # The positive row is written under the identity-pinned key...
    service._save_to_cache.assert_any_call(
        "recording", "Mammoth", f"Mammoth [mbid:{RIGHT_ARTIST_MBID}]",
        RIGHT_RECORDING, service.mb_client.search_recording.return_value[1], 98)
    # ...and the lookup read that same key, never the bare (track, name) one.
    service._check_cache.assert_called_with(
        "recording", "Mammoth", f"Mammoth [mbid:{RIGHT_ARTIST_MBID}]")


def test_h_no_mbid_keeps_the_legacy_cache_key(service):
    service.mb_client.search_recording.return_value = [
        {"id": RIGHT_RECORDING, "title": "Mammoth", "score": 100, "length": 270000,
         "artist-credit": [{"artist": {"id": RIGHT_ARTIST_MBID, "name": "Mammoth WVH"}}]},
    ]

    service.match_recording("Mammoth", "Mammoth")

    service._check_cache.assert_called_with("recording", "Mammoth", "Mammoth")
    service._save_to_cache.assert_any_call(
        "recording", "Mammoth", "Mammoth", RIGHT_RECORDING,
        service.mb_client.search_recording.return_value[0], 100)


def test_i_tolerance_boundary(service):
    # The gate is ±30s *inclusive*: exactly 30s off passes, 31s off is
    # rejected. Both candidates are otherwise identical winners.
    base = {"title": "Mammoth", "score": 100,
            "artist-credit": [{"artist": {"id": RIGHT_ARTIST_MBID, "name": "Mammoth WVH"}}]}

    service.mb_client.search_recording.return_value = [
        {**base, "id": "rec-edge", "length": 240000},  # exactly 30s off 270s
    ]
    out = service.match_recording("Mammoth", "Mammoth", duration_ms=270000)
    assert out is not None and out["mbid"] == "rec-edge"

    service.mb_client.search_recording.return_value = [
        {**base, "id": "rec-over", "length": 239999},  # 30.001s off
    ]
    service.mb_client.search_artist.return_value = []
    out = service.match_recording("Mammoth", "Mammoth", duration_ms=270000)
    assert out is None


def test_j_pinned_pass_trusts_the_arid_query(service):
    # The artist-pinned retry is NOT identity-gated: `arid:<mbid>` already
    # constrained the artist server-side, so a credit whose MBIDs disagree
    # (MB index lag, merged artists) must not newly reject a match that
    # used to land.
    service.mb_client.search_recording.return_value = []
    service.mb_client.search_recording_by_artist_mbid.return_value = [
        {"id": RIGHT_RECORDING, "title": "Mammoth", "score": 100, "length": 270000,
         "artist-credit": [{"artist": {"id": "stale-other-mbid", "name": "Mammoth WVH"}}]},
    ]

    out = service.match_recording("Mammoth", "Mammoth", artist_mbid=RIGHT_ARTIST_MBID)

    assert out is not None
    assert out["mbid"] == RIGHT_RECORDING


def test_k_combined_gates_then_retry_applies_duration_only(service):
    # Plain search: the wrong band's recording is identity-gated out and the
    # right band's far-too-short recording is duration-gated out. The pinned
    # retry finds the right recording at the right length and picks it —
    # using the caller's MBID directly, no artist re-resolution.
    service.mb_client.search_recording.return_value = [
        {"id": WRONG_RECORDING, "title": "Mammoth", "score": 100, "length": 270000,
         "artist-credit": [{"artist": {"id": WRONG_BAND_MBID, "name": "Mammoth"}}]},
        {"id": "rec-far", "title": "Mammoth", "score": 90, "length": 100000,
         "artist-credit": [{"artist": {"id": RIGHT_ARTIST_MBID, "name": "Mammoth WVH"}}]},
    ]
    service.mb_client.search_recording_by_artist_mbid.return_value = [
        {"id": RIGHT_RECORDING, "title": "Mammoth", "score": 100, "length": 270000,
         "artist-credit": [{"artist": {"id": RIGHT_ARTIST_MBID, "name": "Mammoth WVH"}}]},
    ]

    out = service.match_recording(
        "Mammoth", "Mammoth", artist_mbid=RIGHT_ARTIST_MBID, duration_ms=270000)

    assert out is not None
    assert out["mbid"] == RIGHT_RECORDING
    service.mb_client.search_artist.assert_not_called()


def test_l_multi_artist_credit_passes_when_one_is_the_known_identity(service):
    # A collaboration credited to the known artist plus someone else is
    # still the known artist's recording.
    service.mb_client.search_recording.return_value = [
        {"id": RIGHT_RECORDING, "title": "Mammoth", "score": 95, "length": 270000,
         "artist-credit": [
             {"artist": {"id": RIGHT_ARTIST_MBID, "name": "Mammoth WVH"}},
             {"artist": {"id": "guest-mbid", "name": "Some Guest"}},
         ]},
    ]

    out = service.match_recording("Mammoth", "Mammoth", artist_mbid=RIGHT_ARTIST_MBID)

    assert out is not None
    assert out["mbid"] == RIGHT_RECORDING


def test_m_candidate_without_credit_mbids_is_not_rejected(service):
    # Missing identity data is not a mismatch: a candidate whose credit
    # carries no parseable MBIDs passes the identity gate.
    service.mb_client.search_recording.return_value = [
        {"id": RIGHT_RECORDING, "title": "Mammoth", "score": 95, "length": 270000,
         "artist-credit": [{"artist": {"name": "Mammoth WVH"}}]},
    ]

    out = service.match_recording("Mammoth", "Mammoth", artist_mbid=RIGHT_ARTIST_MBID)

    assert out is not None
    assert out["mbid"] == RIGHT_RECORDING


def test_n_cache_key_lowercases_the_mbid(service):
    # The same identity in different case must map to one cache row.
    service.mb_client.search_recording.return_value = _candidates()

    service.match_recording("Mammoth", "Mammoth", artist_mbid=RIGHT_ARTIST_MBID.upper())

    service._check_cache.assert_called_with(
        "recording", "Mammoth", f"Mammoth [mbid:{RIGHT_ARTIST_MBID}]")
