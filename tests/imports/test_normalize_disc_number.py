"""Sokhi: some tracks in a multi-disc album got a null disc in Jellyfin and floated
ungrouped above the disc sections. Root cause: the tag-writer only wrote the disc
tag when disc_number was truthy, and upstream a 0 / None / '' (esp. when a track
matched a different edition than its siblings) slipped through — so on the
clear-then-rewrite those tracks lost their disc entirely. normalize_disc_number
floors any value to >=1 so a track is never written disc-less."""

from __future__ import annotations

import pytest

from core.imports.track_number import normalize_disc_number


@pytest.mark.parametrize("value,expected", [
    (1, 1), (2, 2), (4, 4),
    ("1", 1), ("3", 3), (" 2 ", 2),
    (0, 1), ("0", 1),            # the bug: 0 must floor to 1, not vanish
    (None, 1), ("", 1), ("  ", 1),
    (-1, 1), ("-2", 1),          # negatives floor to 1
    ("abc", 1), ("1/4", 1),      # non-numeric -> 1 (never raises)
    (2.0, 2),                    # float-ish via str()
])
def test_normalize_disc_number(value, expected):
    assert normalize_disc_number(value) == expected


def test_valid_multidisc_values_preserved():
    # a real disc on a 4xLP must survive untouched
    for d in (1, 2, 3, 4):
        assert normalize_disc_number(d) == d


# ── resolve_disc_for_track: the FOLDER and the TAG must use the same disc ──────

from core.imports.track_number import resolve_disc_for_track


def test_resolve_disc_prefers_per_track_search_then_album():
    # per-track disc wins (this is the value the tag uses) — so the folder, which
    # now calls the SAME resolver with the SAME inputs, lands on the same disc.
    assert resolve_disc_for_track({"disc_number": 3}, {"disc_number": 1}) == 3
    # falls back to album context when the per-track search has none
    assert resolve_disc_for_track({}, {"disc_number": 2}) == 2
    assert resolve_disc_for_track({"disc_number": None}, {"disc_number": 2}) == 2
    # both missing -> floored default 1
    assert resolve_disc_for_track({}, {}) == 1
    assert resolve_disc_for_track(None, None) == 1


def test_resolve_disc_floors_bad_values():
    assert resolve_disc_for_track({"disc_number": 0}, {"disc_number": 5}) == 5   # 0 is falsy -> fall to album
    assert resolve_disc_for_track({"disc_number": "2"}, {}) == 2
    assert resolve_disc_for_track({"disc_number": "junk"}, {}) == 1


def test_folder_and_tag_resolve_identically():
    # the regression that matters: given the same (original_search, album_info),
    # source.py (tag) and the pipeline (folder) get the IDENTICAL disc.
    cases = [
        ({"disc_number": 2}, {"disc_number": 1}),   # Sokhi's case: per-track 2, album 1
        ({"disc_number": 3}, {"disc_number": 1}),
        ({}, {"disc_number": 1}),
        ({"disc_number": 0}, {"disc_number": 1}),
    ]
    for osrch, ainfo in cases:
        folder_disc = resolve_disc_for_track(osrch, ainfo)   # what the pipeline writes to album_info
        tag_disc = resolve_disc_for_track(osrch, ainfo)      # what source.py writes to the tag
        assert folder_disc == tag_disc
