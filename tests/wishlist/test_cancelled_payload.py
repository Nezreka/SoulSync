from core.wishlist import payloads


def test_build_cancelled_task_wishlist_payload_normalizes_track_and_context():
    task = {
        "playlist_name": "My Playlist",
        "playlist_id": "pl-1",
        "track_info": {
            "id": "trk-1",
            "name": "Song One",
            "duration_ms": 123456,
            "artists": [
                "Artist One",
                {"name": "Artist Two"},
                {"name": {"name": "Nested Artist"}},
            ],
            "album": {"name": "Album One"},
            "album_image_url": "https://img.example/cover.jpg",
        },
    }

    out = payloads.build_cancelled_task_wishlist_payload(task, profile_id=7)

    assert out["profile_id"] == 7
    assert out["failure_reason"] == "Download cancelled by user (v2)"
    assert out["source_type"] == "playlist"
    assert out["source_context"] == {
        "playlist_name": "My Playlist",
        "playlist_id": "pl-1",
        "added_from": "modal_cancellation_v2",
    }
    assert out["spotify_track_data"]["artists"] == [
        {"name": "Artist One"},
        {"name": "Artist Two"},
        {"name": "Nested Artist"},
    ]
    assert out["spotify_track_data"]["album"]["images"] == [{"url": "https://img.example/cover.jpg"}]
