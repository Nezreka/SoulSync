from core.wishlist import selection


def test_sanitize_and_dedupe_wishlist_tracks_removes_duplicate_ids():
    raw_tracks = [
        {"id": "1", "name": "Song One", "artists": [{"name": "Artist One"}]},
        {"id": "1", "name": "Song One", "artists": [{"name": "Artist One"}]},
        {"id": "2", "name": "Song Two", "artists": [{"name": "Artist Two"}]},
    ]

    tracks, duplicates_found = selection.sanitize_and_dedupe_wishlist_tracks(raw_tracks)

    assert duplicates_found == 1
    assert [track["id"] for track in tracks] == ["1", "2"]


def test_filter_wishlist_tracks_by_category_uses_classifier():
    tracks = [
        {"id": "1", "spotify_data": {"album": {"album_type": "single"}}},
        {"id": "2", "spotify_data": {"album": {"total_tracks": 8}}},
        {"id": "3", "spotify_data": {"album": {"album_type": "ep"}}},
    ]

    filtered, total = selection.filter_wishlist_tracks_by_category(tracks, "singles")

    assert [track["id"] for track in filtered] == ["1", "3"]
    assert total == 2


def test_prepare_wishlist_tracks_for_display_applies_limit_after_category_filter():
    raw_tracks = [
        {"id": "1", "spotify_data": {"album": {"album_type": "single"}}},
        {"id": "2", "spotify_data": {"album": {"album_type": "single"}}},
        {"id": "3", "spotify_data": {"album": {"total_tracks": 8}}},
    ]

    out = selection.prepare_wishlist_tracks_for_display(raw_tracks, category="singles", limit=1)

    assert out["tracks"][0]["id"] == "1"
    assert out["total"] == 2
    assert out["duplicates_found"] == 0
