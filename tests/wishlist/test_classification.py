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


@pytest.mark.parametrize("source_type", ["album", "discography"])
def test_classify_wishlist_track_keeps_explicit_album_sources_as_albums(source_type):
    assert classify_wishlist_track({
        "source_type": source_type,
        "spotify_data": {"album": {"album_type": "single", "total_tracks": 1}},
    }) == "albums"
