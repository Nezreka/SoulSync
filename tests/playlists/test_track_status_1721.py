"""#1721 (kevin2xk): 157 tracks synced as 103 and nothing said where the other
54 went. each mirrored track now gets one status from what its row already
knows: in library, wishlisted, needs a match, missing, or not checked yet.
"""

from __future__ import annotations

import json

import pytest

from core.playlists import track_status as ts


def _row(extra=None, source_track_id="sp1"):
    row = {"id": 1, "track_name": "Quiet Pages", "artist_name": "Evren Calder",
           "source_track_id": source_track_id}
    if extra is not None:
        row["extra_data"] = json.dumps(extra)
    return row


REAL = {"discovered": True, "provider": "deezer", "matched_data": {"id": "dz9"}}
STUB = {"discovered": True, "provider": "wing_it_fallback", "wing_it_fallback": True,
        "matched_data": {"id": "wing_it_abc123"}}


def test_in_library_wins_over_everything():
    extra = {**STUB, "in_library": True, "library_checked_at": 1}
    assert ts.track_status(_row(extra), {"sp1"}) == ts.IN_LIBRARY


def test_wishlisted_under_the_matched_id():
    """sync files a deezer-identified track's wishlist row under the deezer
    id, not the playlist's spotify id. the old card count only asked about the
    spotify id and read 0 for exactly this playlist."""
    extra = {**REAL, "in_library": False, "library_checked_at": 1}
    assert ts.track_status(_row(extra), {"dz9"}) == ts.WISHLISTED


def test_wishlisted_under_the_source_id():
    assert ts.track_status(_row(REAL), {"sp1"}) == ts.WISHLISTED


def test_a_stub_id_is_never_a_wishlist_id():
    assert ts.wishlist_ids(_row(STUB)) == ["sp1"]
    assert ts.track_status(_row(STUB, source_track_id=""), {"wing_it_abc123"}) == ts.NEEDS_MATCH


def test_wing_it_guess_needs_a_match_even_after_a_sync():
    extra = {**STUB, "in_library": False, "library_checked_at": 1}
    assert ts.track_status(_row(extra), set()) == ts.NEEDS_MATCH


def test_a_real_match_with_a_leftover_wing_it_flag_is_not_a_guess():
    extra = {**REAL, "wing_it_fallback": True}
    assert ts.needs_match(extra) is False


def test_a_manual_fix_is_never_a_guess():
    extra = {**STUB, "manual_match": True}
    assert ts.needs_match(extra) is False


@pytest.mark.parametrize("extra", [
    {"discovered": False, "discovery_attempted": True},
    {"unmatched_by_user": True},
])
def test_failed_or_removed_identification_needs_a_match(extra):
    assert ts.track_status(_row(extra), set()) == ts.NEEDS_MATCH


def test_identified_synced_and_not_owned_is_missing():
    extra = {**REAL, "in_library": False, "library_checked_at": 1}
    assert ts.track_status(_row(extra), set()) == ts.MISSING


@pytest.mark.parametrize("extra", [None, {}, REAL])
def test_never_synced_is_unchecked(extra):
    assert ts.track_status(_row(extra), set()) == ts.UNCHECKED


def test_garbage_extra_data_is_unchecked_not_a_crash():
    row = _row()
    row["extra_data"] = "{not json"
    assert ts.track_status(row, set()) == ts.UNCHECKED


def test_counts_cover_every_status():
    counts = ts.status_counts([ts.IN_LIBRARY, ts.IN_LIBRARY, ts.NEEDS_MATCH, "bogus"])
    assert counts == {ts.IN_LIBRARY: 2, ts.WISHLISTED: 0, ts.NEEDS_MATCH: 1,
                      ts.MISSING: 0, ts.UNCHECKED: 0}
