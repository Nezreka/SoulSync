"""JSPF builder for ListenBrainz playlist export (#903).

LB's create-playlist requires every track to carry a recording-MBID identifier; text-only
tracks are rejected. These pin the shape (top-level {"playlist": {...}}, string identifier
in the exact MB recording URL form), that tracks without a valid MBID are dropped, and that
the coverage summary counts included vs skipped.
"""

from __future__ import annotations

from core.exports.jspf_export import MB_RECORDING_PREFIX, build_jspf, is_valid_recording_mbid

MBID_A = "e8f9b188-f819-4e43-ab0f-4bd26ce9ff56"
MBID_B = "8f3471b5-7e6a-4c1f-9c1a-2b2b2b2b2b2b"


def test_valid_mbid_check():
    assert is_valid_recording_mbid(MBID_A) is True
    assert is_valid_recording_mbid("not-a-uuid") is False
    assert is_valid_recording_mbid("") is False
    assert is_valid_recording_mbid(None) is False


def test_top_level_shape_and_identifier_format():
    jspf, summary = build_jspf("My Playlist", [
        {"recording_mbid": MBID_A, "title": "Gold", "artist": "Spandau Ballet", "album": "True"},
    ])
    assert set(jspf.keys()) == {"playlist"}
    pl = jspf["playlist"]
    assert pl["title"] == "My Playlist"
    assert len(pl["track"]) == 1
    t = pl["track"][0]
    # identifier is a STRING in the exact MB recording form (per the LB/JSPF spec)
    assert t["identifier"] == f"{MB_RECORDING_PREFIX}{MBID_A}"
    assert isinstance(t["identifier"], str)
    assert t["title"] == "Gold"
    assert t["creator"] == "Spandau Ballet"  # artist -> creator
    assert t["album"] == "True"
    assert summary == {"total": 1, "included": 1, "skipped": 0}


def test_tracks_without_valid_mbid_are_dropped():
    jspf, summary = build_jspf("P", [
        {"recording_mbid": MBID_A, "title": "Keep"},
        {"recording_mbid": "", "title": "No MBID"},
        {"recording_mbid": "garbage", "title": "Bad MBID"},
        {"title": "Missing key entirely"},
    ])
    assert [t["title"] for t in jspf["playlist"]["track"]] == ["Keep"]
    assert summary == {"total": 4, "included": 1, "skipped": 3}


def test_order_is_preserved():
    jspf, _ = build_jspf("P", [
        {"recording_mbid": MBID_A, "title": "first"},
        {"recording_mbid": MBID_B, "title": "second"},
    ])
    assert [t["title"] for t in jspf["playlist"]["track"]] == ["first", "second"]


def test_optional_fields_omitted_when_absent():
    jspf, _ = build_jspf("P", [{"recording_mbid": MBID_A}])
    t = jspf["playlist"]["track"][0]
    assert "title" not in t and "creator" not in t and "album" not in t


def test_creator_and_title_defaults():
    jspf, summary = build_jspf("", [], creator="SoulSync")
    assert jspf["playlist"]["title"] == "SoulSync Export"  # blank -> default
    assert jspf["playlist"]["creator"] == "SoulSync"
    assert jspf["playlist"]["track"] == []
    assert summary == {"total": 0, "included": 0, "skipped": 0}
