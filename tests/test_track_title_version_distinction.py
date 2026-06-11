"""Regression: normal vs Extended Mix must not collapse to the same cleaned title."""

import tempfile
from pathlib import Path

import pytest

from database.music_database import DatabaseTrack, MusicDatabase


@pytest.fixture
def db(tmp_path: Path):
    return MusicDatabase(database_path=str(tmp_path / "test.db"))


def test_clean_title_preserves_extended_mix_after_feat(db):
    normal = db._clean_track_title_for_comparison("Latinamerica (feat. Vika)")
    extended = db._clean_track_title_for_comparison(
        "Latinamerica (feat. Vika) - Extended Mix"
    )
    assert normal == "latinamerica"
    assert extended == "latinamerica extended mix"
    assert normal != extended


def test_normal_does_not_match_extended_in_library(db):
    extended_track = DatabaseTrack(
        id="t1",
        album_id="a1",
        artist_id="ar1",
        title="Latinamerica (feat. Vika) - Extended Mix",
        track_number=1,
        duration=300000,
        file_path="/music/ext.flac",
        bitrate=320,
    )
    extended_track.artist_name = "Raffa FL"
    extended_track.album_title = "Latinamerica (feat. Vika)"

    confidence = db._calculate_track_confidence(
        "Latinamerica (feat. Vika)",
        "Raffa FL",
        extended_track,
    )
    assert confidence < 0.7


def test_extended_matches_extended_in_library(db):
    extended_track = DatabaseTrack(
        id="t1",
        album_id="a1",
        artist_id="ar1",
        title="Latinamerica (feat. Vika) - Extended Mix",
        track_number=1,
        duration=300000,
        file_path="/music/ext.flac",
        bitrate=320,
    )
    extended_track.artist_name = "Raffa FL"
    extended_track.album_title = "Latinamerica (feat. Vika)"

    confidence = db._calculate_track_confidence(
        "Latinamerica (feat. Vika) - Extended Mix",
        "Raffa FL",
        extended_track,
    )
    assert confidence >= 0.7
