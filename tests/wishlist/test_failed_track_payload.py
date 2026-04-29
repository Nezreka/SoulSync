from core.wishlist import payloads


def test_build_failed_track_wishlist_context_uses_source_track_info():
    track_info = {
        "name": "Song One",
        "artist": "Artist One",
        "artists": [{"name": "Artist One"}],
        "album": {"name": "Album One", "album_type": "ep"},
    }

    out = payloads.build_failed_track_wishlist_context(
        track_info,
        track_index=3,
        retry_count=2,
        failure_reason="Download cancelled",
        candidates=[{"title": "candidate"}],
    )

    assert out["download_index"] == 3
    assert out["table_index"] == 3
    assert out["track_name"] == "Song One"
    assert out["artist_name"] == "Artist One"
    assert out["retry_count"] == 2
    assert out["failure_reason"] == "Download cancelled"
    assert out["candidates"] == [{"title": "candidate"}]
    assert out["spotify_track"]["artists"] == [{"name": "Artist One"}]
