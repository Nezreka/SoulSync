import pytest

from core.wishlist.classification import classify_wishlist_track


@pytest.mark.parametrize(
    "spotify_data,expected",
    [
        ({"album": {"album_type": "single"}}, "singles"),
        ({"track_data": {"album": {"album_type": "single"}}}, "singles"),
        ({"album": {"album_type": "ep"}}, "singles"),
        ({"album": {"album_type": "album"}}, "albums"),
        ({"album": {"album_type": "compilation"}}, "albums"),
        ({"album": {"total_tracks": 4}}, "singles"),
        ({"album": {"total_tracks": 8}}, "albums"),
        ({"album": {"total_tracks": "4"}}, "singles"),
        ({"album": {"total_tracks": "8"}}, "albums"),
        ({"album": {"total_tracks": "not-a-number"}}, "albums"),
        ({}, "albums"),
    ],
)
def test_classify_wishlist_track(spotify_data, expected):
    assert classify_wishlist_track({"spotify_data": spotify_data}) == expected
