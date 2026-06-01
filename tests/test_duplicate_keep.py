"""Tests for duplicate-keeper selection (core/library/duplicate_keep.py).

The headline contract: lossless format wins over lossy regardless of the
recorded bitrate — the bug a user hit was a FLAC (no bitrate in the DB) being
deleted in favor of a 282 kbps MP3 because the old ranking compared bitrate
first.
"""

from __future__ import annotations

from core.library.duplicate_keep import (
    duplicate_keep_sort_key,
    format_rank_for_path,
    pick_duplicate_to_keep,
)


def _t(path, bitrate=None, duration=None, track_number=None, tid=1):
    return {"id": tid, "file_path": path, "bitrate": bitrate,
            "duration": duration, "track_number": track_number}


# --- the reported regression --------------------------------------------------


def test_flac_with_missing_bitrate_beats_282kbps_mp3():
    # Havok "Prepare For Attack": FLAC has no bitrate recorded, MP3 is 282 kbps.
    mp3 = _t("/music/Havok/01 - Prepare For Attack.mp3", bitrate=282, duration=236, tid=1)
    flac = _t("/music/Havok/01 - Prepare for Attack.flac", bitrate=None, duration=236, tid=2)
    keep = pick_duplicate_to_keep([mp3, flac])
    assert keep["id"] == 2  # the FLAC


def test_flac_beats_mp3_regardless_of_order():
    mp3 = _t("/x/a.mp3", bitrate=320, tid=1)
    flac = _t("/x/a.flac", bitrate=0, tid=2)
    assert pick_duplicate_to_keep([mp3, flac])["id"] == 2
    assert pick_duplicate_to_keep([flac, mp3])["id"] == 2


# --- format ranking -----------------------------------------------------------


def test_format_rank_lossless_outranks_lossy():
    assert format_rank_for_path("a.flac") > format_rank_for_path("a.mp3")
    assert format_rank_for_path("a.wav") > format_rank_for_path("a.aac")
    assert format_rank_for_path("a.m4a") > format_rank_for_path("a.mp3")


def test_format_rank_unknown_and_missing():
    assert format_rank_for_path("a.xyz") == 1
    assert format_rank_for_path("noext") == 1
    assert format_rank_for_path(None) == 1
    assert format_rank_for_path("") == 1


def test_format_rank_case_insensitive():
    assert format_rank_for_path("A.FLAC") == format_rank_for_path("a.flac")


# --- tie-breakers within the same format -------------------------------------


def test_same_format_higher_bitrate_wins():
    lo = _t("/x/a.mp3", bitrate=192, tid=1)
    hi = _t("/x/b.mp3", bitrate=320, tid=2)
    assert pick_duplicate_to_keep([lo, hi])["id"] == 2


def test_same_format_same_bitrate_longer_duration_wins():
    short = _t("/x/a.flac", bitrate=900, duration=200, tid=1)
    long = _t("/x/b.flac", bitrate=900, duration=240, tid=2)
    assert pick_duplicate_to_keep([short, long])["id"] == 2


def test_track_number_is_final_tiebreak():
    a = _t("/x/a.flac", bitrate=900, duration=240, track_number=1, tid=1)
    b = _t("/x/b.flac", bitrate=900, duration=240, track_number=7, tid=2)
    assert pick_duplicate_to_keep([a, b])["id"] == 2


# --- shape / edge cases -------------------------------------------------------


def test_sort_key_tuple_order_is_format_first():
    key = duplicate_keep_sort_key(_t("/x/a.flac", bitrate=100, duration=5, track_number=3))
    assert key == (10, 100, 5, 3)


def test_missing_numeric_fields_default_to_zero():
    assert duplicate_keep_sort_key(_t("/x/a.mp3")) == (5, 0, 0, 0)


def test_empty_group_returns_none():
    assert pick_duplicate_to_keep([]) is None
