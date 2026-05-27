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


def test_ensure_wishlist_track_format_aliases_the_spotify_helper():
    track = {
        "name": "Song",
        "artist": "Artist One",
        "album": {"name": "Album One"},
    }

    out = payloads.ensure_wishlist_track_format(track)

    assert out["name"] == "Song"
    assert out["artists"] == [{"name": "Artist One"}]
    assert out["album"]["name"] == "Album One"


def test_extract_spotify_track_from_modal_info_converts_trackresult_like_object():
    track_info = {
        "spotify_track": SimpleNamespace(
            title="Song Two",
            artist="Artist Two",
            album="Album Two",
        )
    }

    out = payloads.extract_spotify_track_from_modal_info(track_info)

    assert out["source"] == "trackresult"
    assert out["name"] == "Song Two"
    assert out["artists"] == [{"name": "Artist Two"}]
    assert out["album"]["name"] == "Album Two"


def test_extract_spotify_track_from_modal_info_reconstructs_from_slskd_result():
    track_info = {
        "slskd_result": SimpleNamespace(
            title="Song Three",
            artist="Artist Three",
            album="Album Three",
        )
    }

    out = payloads.extract_spotify_track_from_modal_info(track_info)

    assert out["reconstructed"] is True
    assert out["name"] == "Song Three"
    assert out["artists"] == [{"name": "Artist Three"}]
    assert out["album"]["name"] == "Album Three"


# ---------------------------------------------------------------------------
# track_number / disc_number preservation through the wishlist payload
# helpers — pins the bug A fix from PR 2/4. Pre-fix the helpers
# defaulted missing numbers to 1, which locked every wishlist retry
# to track 01 because the import pipeline's filename-extract fallback
# only fires when the value is None (not the pre-filled 1).
# ---------------------------------------------------------------------------


def test_ensure_wishlist_track_format_preserves_real_track_number():
    """Real track positions must survive the format helper. Pre-fix
    the helper read ``track_info.get('track_number', 1)`` which always
    returned 1 if the upstream payload had dropped the key — the
    desired number was lost on every round-trip."""
    track = {
        "name": "No Sleep Till Brooklyn",
        "artist": "Beastie Boys",
        "album": {"name": "Licensed to Ill", "release_date": "1986-11-15"},
        "track_number": 8,
        "disc_number": 1,
    }
    out = payloads.ensure_wishlist_track_format(track)
    assert out["track_number"] == 8
    assert out["disc_number"] == 1


def test_ensure_wishlist_track_format_keeps_missing_track_number_as_none():
    """When the upstream payload doesn't carry a track number, the
    helper must NOT pre-fill 1 — that poisons the chain and locks the
    file to track 01. Leave None so the import pipeline's filename
    fallback at ``core/imports/pipeline.py:652`` can fire."""
    track = {
        "name": "Mystery Track",
        "artist": "Artist",
        "album": {"name": "Album"},
    }
    out = payloads.ensure_wishlist_track_format(track)
    assert out["track_number"] is None
    assert out["disc_number"] is None


def test_build_cancelled_task_wishlist_payload_preserves_track_number():
    """Cancellation→re-add path was the worst offender — the payload
    builder dropped track_number from the saved data entirely (didn't
    even include the key). Next wishlist cycle saw missing key →
    helper defaulted to 1 → file imported as 01. Now both the
    cancellation payload AND the helper preserve real positions."""
    task = {
        "track_info": {
            "id": "trk-1", "name": "Brass Monkey",
            "artists": [{"name": "Beastie Boys"}],
            "album": {"name": "Licensed to Ill", "release_date": "1986-11-15"},
            "track_number": 11,
            "disc_number": 1,
        },
        "playlist_name": "Wishlist",
        "playlist_id": "p1",
    }
    out = payloads.build_cancelled_task_wishlist_payload(task)
    td = out["track_data"]
    assert td["track_number"] == 11
    assert td["disc_number"] == 1
    # Album release_date survives the round-trip so the path template
    # renders the year in the folder name.
    assert td["album"]["release_date"] == "1986-11-15"


def test_build_cancelled_task_wishlist_payload_string_album_pulls_release_date_from_track_info():
    """When the source ``album`` field is a bare string, the payload
    builder constructs an album dict from scratch — it must pull
    release_date / album_image_url / etc. from the adjacent
    track_info fields rather than dropping them silently."""
    task = {
        "track_info": {
            "id": "trk-2", "name": "Song",
            "artists": [{"name": "Artist"}],
            "album": "Bare String Album",
            "release_date": "2020-06-01",
        },
    }
    out = payloads.build_cancelled_task_wishlist_payload(task)
    album = out["track_data"]["album"]
    assert album["name"] == "Bare String Album"
    assert album["release_date"] == "2020-06-01"


def test_ensure_wishlist_track_format_defaults_non_dict_album_to_album_type():
    """When ``album`` arrives as a non-dict (legacy/reconstruction path) we
    must not stamp ``album_type='single'`` — that lies about the origin
    and routes the wishlist requeue through the single_path template
    instead of album_path, dumping album tracks into the Singles tree.
    Default to 'album' / total_tracks=0 (unknown) so downstream code can
    fall through to the real release-type detection logic."""
    track = {
        "name": "Song",
        "artist": "Artist One",
        "album": "Album From Legacy String",
    }

    out = payloads.ensure_wishlist_track_format(track)

    assert out["album"]["name"] == "Album From Legacy String"
    assert out["album"]["album_type"] == "album"
    assert out["album"]["total_tracks"] == 0


def test_extract_spotify_track_from_modal_info_slskd_reconstruction_defaults_to_album():
    """Slskd-result reconstruction is a last-resort path; defaulting to
    ``album_type='single'`` corrupted the requeue routing for album
    batches. Same fix as ensure_wishlist_track_format: default 'album'."""
    track_info = {
        "slskd_result": SimpleNamespace(
            title="Song Three",
            artist="Artist Three",
            album="Album Three",
        )
    }

    out = payloads.extract_spotify_track_from_modal_info(track_info)

    assert out["album"]["album_type"] == "album"
    assert out["album"]["total_tracks"] == 0


def test_extract_wishlist_track_from_modal_info_uses_track_data_key():
    track_info = {
        "track_data": {
            "id": "track-1",
            "name": "Song Four",
            "artists": [{"name": "Artist Four"}],
            "album": {"name": "Album Four"},
        }
    }

    out = payloads.extract_wishlist_track_from_modal_info(track_info)

    assert out["id"] == "track-1"
    assert out["name"] == "Song Four"
    assert out["artists"] == [{"name": "Artist Four"}]
