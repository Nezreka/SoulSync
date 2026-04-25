import pytest

from core.imports.filename import parse_filename_metadata


@pytest.mark.parametrize(
    "filename,expected",
    [
        (
            "01 - Artist One - Title One.mp3",
            {"artist": "Artist One", "title": "Title One", "album": "", "track_number": 1},
        ),
        (
            "Artist Two - Title Two.flac",
            {"artist": "Artist Two", "title": "Title Two", "album": "", "track_number": None},
        ),
        (
            r"Artist Three\Album Three\03 - Title Three.ogg",
            {"artist": "", "title": "Title Three", "album": "Album Three", "track_number": 3},
        ),
        (
            "Loose Song.wav",
            {"artist": "", "title": "Loose Song", "album": "", "track_number": None},
        ),
    ],
)
def test_parse_filename_metadata_handles_common_patterns(filename, expected):
    parsed = parse_filename_metadata(filename)

    assert parsed["artist"] == expected["artist"]
    assert parsed["title"] == expected["title"]
    assert parsed["album"] == expected["album"]
    assert parsed["track_number"] == expected["track_number"]
