"""Tests for core.playback.play_log.build_play_event."""

from __future__ import annotations

from core.playback.play_log import WEB_PLAYER_SOURCE, build_play_event

TS = "2026-05-30T12:00:00Z"


def test_library_track_full_event():
    ev = build_play_event(
        {"id": 4321, "title": "DtMF", "artist": "Bad Bunny", "album": "DeBÍ"},
        TS, duration_ms=237000,
    )
    assert ev == {
        "track_id": "4321",
        "title": "DtMF",
        "artist": "Bad Bunny",
        "album": "DeBÍ",
        "played_at": TS,
        "duration_ms": 237000,
        "server_source": WEB_PLAYER_SOURCE,
        "db_track_id": 4321,
    }


def test_int_string_id_is_db_track_id():
    ev = build_play_event({"id": "99", "title": "X"}, TS)
    assert ev["db_track_id"] == 99
    assert ev["track_id"] == "99"


def test_composite_id_not_used_as_db_track_id():
    # Streamed/search results can carry a composite id like "user||file" —
    # must NOT become db_track_id (would corrupt the int FK join).
    ev = build_play_event({"id": "peer||song.flac", "title": "Streamed"}, TS)
    assert ev["db_track_id"] is None
    assert ev["track_id"] == "peer||song.flac"


def test_missing_title_returns_none():
    assert build_play_event({"id": 1, "artist": "A"}, TS) is None
    assert build_play_event({"title": "   "}, TS) is None


def test_non_dict_returns_none():
    assert build_play_event(None, TS) is None
    assert build_play_event("nope", TS) is None


def test_missing_fields_default_to_empty():
    ev = build_play_event({"title": "Solo"}, TS)
    assert ev["artist"] == ""
    assert ev["album"] == ""
    assert ev["duration_ms"] == 0
    assert ev["db_track_id"] is None
    assert ev["track_id"] is None


def test_bad_duration_is_zero():
    ev = build_play_event({"id": 1, "title": "T"}, TS, duration_ms="not-a-number")
    assert ev["duration_ms"] == 0


def test_bool_id_not_treated_as_int():
    # True is an int in Python — must not slip through as a track id.
    ev = build_play_event({"id": True, "title": "T"}, TS)
    assert ev["db_track_id"] is None


def test_caller_supplies_timestamp_pure():
    # The module never reads the clock — same input → same output.
    a = build_play_event({"id": 1, "title": "T"}, TS)
    b = build_play_event({"id": 1, "title": "T"}, TS)
    assert a == b
