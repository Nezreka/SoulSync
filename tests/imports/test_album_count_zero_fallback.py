"""Regression test for the EP-filed-as-Single bug.

When the album list (e.g. Deezer's /artist/albums, which omits nb_tracks)
provides total_tracks=0 ("unknown"), build_import_album_info must NOT fall
through to the per-track total_tracks (often 1) via `or` — 0 is falsy, so
the old code used the track's count, filing 6-track EPs under Single/.

Bug report: CAL/Broque, Sep 28 2026 — Collision Course EP filed to Single/.
"""
from core.imports.context import build_import_album_info


def test_album_count_zero_does_not_fall_through_to_track_count():
    """total_tracks=0 in album context means unknown, not 'use track count'."""
    context = {
        "album": {
            "name": "Collision Course",
            "album_type": "ep",
            "total_tracks": 0,  # Deezer list omits nb_tracks
        },
        "track_info": {
            "name": "Numb / Encore",
            "total_tracks": 1,  # per-track tag data
        },
        "original_search_result": {},
    }
    info = build_import_album_info(context, force_album=True)
    # Must be 0 (unknown), NOT 1 (the track's count)
    assert info["total_tracks"] == 0, f"got {info['total_tracks']}"
    assert info["album_type"] == "ep"


def test_album_count_none_falls_through_to_album_info():
    """When album context has NO count (None), other album-level sources ok."""
    context = {
        "album": {
            "name": "Collision Course",
            "album_type": "ep",
            # no total_tracks key at all
        },
        "track_info": {
            "name": "Numb / Encore",
            "total_tracks": 1,
        },
        "original_search_result": {},
    }
    # Simulate album_info with a valid count
    info = build_import_album_info(
        context, album_info={"total_tracks": 6}, force_album=True
    )
    assert info["total_tracks"] == 6


def test_valid_album_count_preserved():
    """A valid album-level count is used directly."""
    context = {
        "album": {
            "name": "Collision Course",
            "album_type": "ep",
            "total_tracks": 6,
        },
        "track_info": {
            "name": "Numb / Encore",
            "total_tracks": 1,
        },
        "original_search_result": {},
    }
    info = build_import_album_info(context, force_album=True)
    assert info["total_tracks"] == 6
    assert info["album_type"] == "ep"
