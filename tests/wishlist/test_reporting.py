from core.wishlist import reporting


def test_count_wishlist_tracks_by_category_dedupes_before_counting():
    raw_tracks = [
        {"id": "1", "spotify_data": {"album": {"album_type": "single"}}},
        {"id": "1", "spotify_data": {"album": {"album_type": "single"}}},
        {"id": "2", "spotify_data": {"album": {"total_tracks": 8}}},
        {"id": "3", "spotify_data": {"album": {"album_type": "ep"}}},
    ]

    out = reporting.count_wishlist_tracks_by_category(raw_tracks)

    assert out == {"singles": 2, "albums": 1, "total": 3}


def test_build_wishlist_stats_payload_combines_counts_and_metadata():
    raw_tracks = [
        {"id": "1", "spotify_data": {"album": {"album_type": "single"}}},
        {"id": "2", "spotify_data": {"album": {"total_tracks": 8}}},
    ]

    out = reporting.build_wishlist_stats_payload(
        raw_tracks,
        next_run_in_seconds=42,
        is_auto_processing=True,
        current_cycle="singles",
    )

    assert out == {
        "singles": 1,
        "albums": 1,
        "total": 2,
        "next_run_in_seconds": 42,
        "is_auto_processing": True,
        "current_cycle": "singles",
    }
