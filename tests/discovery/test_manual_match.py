"""Tests for core.discovery.manual_match helpers.

These pin the contract for two route-layer decisions lifted out of
web_server.py so the Fix-popup → mirrored-playlist back-sync flow is
testable in isolation (per kettui's standing rule that web_server.py
behavior is reproduced in core/ modules with real unit tests, not by
AST-parsing the route file).
"""

from core.discovery.manual_match import (
    derive_manual_match_provider,
    is_drifted_for_redo,
    should_rediscover,
)


# ---------------------------------------------------------------------------
# derive_manual_match_provider
# ---------------------------------------------------------------------------


def test_derive_uses_payload_source_when_present():
    """Search-endpoint payloads always stamp `source` — that's the
    authoritative provider for a manual match."""
    payload = {'id': 'rec-1', 'source': 'musicbrainz', 'name': 'Track'}
    assert derive_manual_match_provider(payload, 'spotify') == 'musicbrainz'


def test_derive_falls_back_to_active_when_payload_missing_source():
    """MBID-paste path returns a lean flat shape without `source`. Fall
    back to the user's active discovery source so the cached match
    matches whatever provider next compares against it."""
    payload = {'id': 'mb-mbid', 'name': 'Track'}  # no `source`
    assert derive_manual_match_provider(payload, 'musicbrainz') == 'musicbrainz'


def test_derive_falls_back_to_spotify_when_both_missing():
    """Last-ditch default matches the historic hardcode so behaviour is
    identical when both upstream signals are absent (e.g. broken
    config, missing active source)."""
    assert derive_manual_match_provider({}, None) == 'spotify'
    assert derive_manual_match_provider({}, '') == 'spotify'


def test_derive_handles_non_dict_payload_gracefully():
    """Defensive — caller passes whatever request.get_json() returned."""
    assert derive_manual_match_provider(None, 'spotify') == 'spotify'
    assert derive_manual_match_provider('not-a-dict', 'musicbrainz') == 'musicbrainz'


def test_derive_payload_source_wins_even_when_active_set():
    """`source` on payload is authoritative — even if the user's active
    source changed mid-flow, the match came from whatever the popup
    cascade actually queried."""
    payload = {'source': 'itunes'}
    assert derive_manual_match_provider(payload, 'spotify') == 'itunes'


# ---------------------------------------------------------------------------
# is_drifted_for_redo
# ---------------------------------------------------------------------------


def test_drift_redo_when_provider_changed_and_not_manual():
    """Standard provider-drift case: cached provider differs from
    active, no manual flag → re-discover so active source's IDs /
    artwork take effect."""
    extra = {'discovered': True, 'provider': 'spotify'}
    assert is_drifted_for_redo(extra, 'musicbrainz') is True


def test_drift_no_redo_when_provider_matches():
    """Same provider → cached entry is fresh, no redo needed."""
    extra = {'discovered': True, 'provider': 'spotify'}
    assert is_drifted_for_redo(extra, 'spotify') is False


def test_drift_no_redo_when_manual_match_even_if_provider_drifted():
    """The crux of the bug fix: manual matches are exempt from
    provider-drift redo. Re-running would overwrite the user's pick."""
    extra = {'discovered': True, 'provider': 'musicbrainz', 'manual_match': True}
    assert is_drifted_for_redo(extra, 'spotify') is False


def test_drift_no_redo_when_manual_match_with_matching_provider():
    """Manual + provider match: trivially fresh."""
    extra = {'discovered': True, 'provider': 'spotify', 'manual_match': True}
    assert is_drifted_for_redo(extra, 'spotify') is False


def test_drift_no_redo_when_extra_data_missing():
    """No cached entry → nothing to drift from."""
    assert is_drifted_for_redo(None, 'spotify') is False
    assert is_drifted_for_redo({}, 'spotify') is False


def test_drift_handles_non_dict_extra_data():
    """Defensive — extra_data deserialisation can land non-dict shapes."""
    assert is_drifted_for_redo('not-a-dict', 'spotify') is False


def test_drift_default_provider_is_spotify_when_absent():
    """Historic cached entries may pre-date the provider column being
    populated — treat absent provider as 'spotify' (the legacy default)."""
    extra = {'discovered': True}  # no provider field
    assert is_drifted_for_redo(extra, 'spotify') is False
    assert is_drifted_for_redo(extra, 'musicbrainz') is True


# ---------------------------------------------------------------------------
# should_rediscover — the Playlist Pipeline pre-scan gate
# ---------------------------------------------------------------------------


def test_rediscovers_never_discovered_track():
    assert should_rediscover({}) is True
    assert should_rediscover(None) is True


def test_skips_complete_discovery():
    extra = {
        'discovered': True,
        'matched_data': {'track_number': 3, 'album': {'release_date': '2020'}},
    }
    assert should_rediscover(extra) is False


def test_rediscovers_incomplete_discovery():
    # Missing track_number / release_date / album.id — re-discover to backfill.
    extra = {'discovered': True, 'matched_data': {'name': 'X'}}
    assert should_rediscover(extra) is True


def test_album_id_satisfies_completeness():
    extra = {
        'discovered': True,
        'matched_data': {'track_number': 1, 'album': {'id': 'al-1'}},
    }
    assert should_rediscover(extra) is False


def test_rediscovers_wing_it_stub():
    extra = {'discovered': True, 'wing_it_fallback': True}
    assert should_rediscover(extra) is True


def test_skips_manual_match():
    extra = {'discovered': True, 'manual_match': True}
    assert should_rediscover(extra) is False


def test_skips_unmatched_by_user():
    extra = {'unmatched_by_user': True}
    assert should_rediscover(extra) is False


def test_regression_manual_match_wins_over_stale_wing_it_flag():
    """The #799 revert bug: extra_data is MERGED on save, so a track fixed
    after being a Wing It stub still carries wing_it_fallback=True alongside
    the new manual_match=True. The manual match MUST win — otherwise the
    pipeline re-discovers and silently reverts the user's pick to Wing It.

    Before the fix the pre-scan checked wing_it_fallback first and returned
    True (re-discover). It must now skip."""
    extra = {
        'discovered': True,
        'wing_it_fallback': True,   # stale flag left by the merge
        'manual_match': True,       # the user's authoritative fix
        'matched_data': {'name': 'The Real Match'},
    }
    assert should_rediscover(extra) is False


def test_manual_match_wins_even_without_other_fields():
    # Lean Fix-popup save shape (no track_number/album) must still be honored.
    extra = {'discovered': True, 'manual_match': True, 'wing_it_fallback': True}
    assert should_rediscover(extra) is False
