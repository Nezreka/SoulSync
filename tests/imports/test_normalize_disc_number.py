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
