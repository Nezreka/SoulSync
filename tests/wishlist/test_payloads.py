from types import SimpleNamespace

from core.wishlist import payloads


def test_sanitize_track_data_for_processing_normalizes_artists_and_album():
    track = {
        "name": "Song",
        "album": 123,
        "artists": [{"name": "Artist One"}, "Artist Two", SimpleNamespace(name="Artist Three")],
    }

    out = payloads.sanitize_track_data_for_processing(track)

    assert out["album"] == "123"
    assert out["artists"] == ["Artist One", "Artist Two", "namespace(name='Artist Three')"]


def test_get_track_artist_name_prefers_artists_list_then_artist_field():
    assert payloads.get_track_artist_name({"artists": [{"name": "Artist One"}]}) == "Artist One"
    assert payloads.get_track_artist_name({"artist": "Solo Artist"}) == "Solo Artist"
    assert payloads.get_track_artist_name({}) == "Unknown Artist"


def test_ensure_spotify_track_format_preserves_existing_shape():
    track = {
        "id": "sp-1",
        "name": "Song",
        "artists": [{"name": "Artist One"}],
        "album": {"name": "Album", "album_type": "ep", "total_tracks": 4},
    }

    out = payloads.ensure_spotify_track_format(track)

    assert out is track


def test_ensure_spotify_track_format_builds_webui_shape():
    track = {
        "name": "Song",
        "artist": "Artist One",
        "album": {"name": "Album One", "release_date": "2024-01-01"},
        "duration_ms": 1234,
        "track_number": 7,
        "disc_number": 2,
        "preview_url": "https://example.test/preview",
        "external_urls": {"spotify": "https://open.spotify.com/track/1"},
        "popularity": 42,
    }

    out = payloads.ensure_spotify_track_format(track)

    assert out["name"] == "Song"
    assert out["artists"] == [{"name": "Artist One"}]
    assert out["album"]["name"] == "Album One"
    assert out["album"]["album_type"] == "album"
    assert out["album"]["total_tracks"] == 0
    assert out["source"] == "webui_modal"
