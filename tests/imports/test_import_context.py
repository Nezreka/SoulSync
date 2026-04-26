import pytest

from core.imports.context import (
    build_import_album_info,
    get_import_clean_album,
    get_import_clean_artist,
    get_import_clean_title,
    get_import_has_clean_metadata,
    get_import_has_full_metadata,
    get_import_source,
    get_import_source_ids,
    get_library_source_id_columns,
    get_source_tag_names,
    normalize_import_context,
)


def test_normalize_import_context_promotes_neutral_fields_without_legacy_aliases():
    context = {
        "source": "deezer",
        "spotify_artist": {"name": "Artist One", "id": "artist-1"},
        "spotify_album": {
            "name": "Album One",
            "id": "album-1",
            "release_date": "2024-01-01",
            "total_tracks": 12,
            "album_type": "album",
            "image_url": "https://img.example/album.jpg",
        },
        "track_info": {
            "name": "Song One",
            "id": "track-1",
            "track_number": 3,
            "disc_number": 2,
            "artists": [{"name": "Artist One"}],
        },
        "original_search_result": {
            "spotify_clean_title": "Song One",
            "spotify_clean_album": "Album One",
            "spotify_clean_artist": "Artist One",
        },
        "has_clean_spotify_data": True,
        "has_full_spotify_metadata": False,
    }

    normalized = normalize_import_context(context)

    assert normalized["artist"]["name"] == "Artist One"
    assert normalized["album"]["name"] == "Album One"
    assert "spotify_artist" not in normalized
    assert "spotify_album" not in normalized
    assert normalized["original_search_result"]["clean_title"] == "Song One"
    assert normalized["original_search_result"]["clean_album"] == "Album One"
    assert normalized["original_search_result"]["clean_artist"] == "Artist One"
    assert "spotify_clean_title" not in normalized["original_search_result"]
    assert "spotify_clean_album" not in normalized["original_search_result"]
    assert "spotify_clean_artist" not in normalized["original_search_result"]
    assert get_import_clean_title(normalized) == "Song One"
    assert get_import_clean_album(normalized) == "Album One"
    assert get_import_clean_artist(normalized) == "Artist One"
    assert get_import_source(normalized) == "deezer"
    assert get_import_has_clean_metadata(normalized) is True
    assert get_import_has_full_metadata(normalized) is False


def test_normalize_import_context_promotes_legacy_source_alias():
    context = {
        "_source": "spotify",
        "artist": {"name": "Artist One", "id": "artist-1"},
        "album": {"name": "Album One", "id": "album-1"},
        "track_info": {"name": "Song One", "id": "track-1"},
        "original_search_result": {"title": "Song One"},
    }

    normalized = normalize_import_context(context)

    assert normalized["source"] == "spotify"
    assert "_source" not in normalized
    assert get_import_source(normalized) == "spotify"


def test_neutral_import_context_helpers_work_without_legacy_aliases():
    context = {
        "source": "deezer",
        "artist": {"name": "Artist One", "id": "artist-1"},
        "album": {
            "name": "Album One",
            "id": "album-1",
            "release_date": "2024-01-01",
            "total_tracks": 12,
            "album_type": "album",
            "image_url": "https://img.example/album.jpg",
        },
        "track_info": {
            "name": "Song One",
            "id": "track-1",
            "track_number": 3,
            "disc_number": 2,
            "artists": [{"name": "Artist One"}],
        },
        "original_search_result": {
            "title": "Song One",
            "artist": "Artist One",
            "album": "Album One",
            "clean_title": "Song One",
            "clean_album": "Album One",
            "clean_artist": "Artist One",
        },
        "has_clean_metadata": True,
        "has_full_metadata": True,
    }

    assert get_import_clean_title(context) == "Song One"
    assert get_import_clean_album(context) == "Album One"
    assert get_import_clean_artist(context) == "Artist One"
    assert get_import_source(context) == "deezer"

    normalized = normalize_import_context(context)
    assert normalized["artist"]["name"] == "Artist One"
    assert normalized["album"]["name"] == "Album One"
    assert normalized["original_search_result"]["clean_title"] == "Song One"
    assert "spotify_artist" not in normalized
    assert "spotify_album" not in normalized
    assert get_import_has_clean_metadata(normalized) is True
    assert get_import_has_full_metadata(normalized) is True


@pytest.mark.parametrize(
    "source,expected_tags,expected_columns",
    [
        (
            "spotify",
            {"track": "SPOTIFY_TRACK_ID", "artist": "SPOTIFY_ARTIST_ID", "album": "SPOTIFY_ALBUM_ID"},
            {"artist": "spotify_artist_id", "album": "spotify_album_id", "track": "spotify_track_id"},
        ),
        (
            "itunes",
            {"track": "ITUNES_TRACK_ID", "artist": "ITUNES_ARTIST_ID", "album": "ITUNES_ALBUM_ID"},
            {"artist": "itunes_artist_id", "album": "itunes_album_id", "track": "itunes_track_id"},
        ),
        (
            "deezer",
            {"track": "DEEZER_TRACK_ID", "artist": "DEEZER_ARTIST_ID", "album": None},
            {"artist": "deezer_id", "album": "deezer_id", "track": "deezer_id"},
        ),
        (
            "hydrabase",
            {"track": None, "artist": None, "album": None},
            {"artist": "soul_id", "album": "soul_id", "track": "soul_id", "track_album": "album_soul_id"},
        ),
        (
            "discogs",
            {"track": None, "artist": None, "album": None},
            {"artist": "discogs_id", "album": "discogs_id", "track": None},
        ),
    ],
)
def test_source_tag_and_library_column_mappings(source, expected_tags, expected_columns):
    assert get_source_tag_names(source) == expected_tags
    assert get_library_source_id_columns(source) == expected_columns


def test_get_import_source_ids_prefers_nested_source_specific_ids():
    context = normalize_import_context(
        {
            "source": "deezer",
            "artist": {"deezer_id": "deezer-artist-1"},
            "album": {"deezer_id": "deezer-album-1"},
            "track_info": {"deezer_id": "deezer-track-1"},
            "original_search_result": {"source_artist_id": "deezer-artist-1"},
        }
    )

    assert get_import_source_ids(context) == {
        "track_id": "deezer-track-1",
        "artist_id": "deezer-artist-1",
        "album_id": "deezer-album-1",
    }


def test_build_import_album_info_uses_normalized_album_context():
    context = normalize_import_context(
        {
            "source": "deezer",
            "artist": {"name": "Artist One"},
            "album": {
                "name": "Album One",
                "image_url": "https://img.example/album.jpg",
                "release_date": "2024-05-01",
                "total_tracks": 8,
                "album_type": "album",
            },
            "track_info": {
                "name": "Song One",
            "track_number": 4,
            "disc_number": 2,
            "duration_ms": 240000,
            "artists": [{"name": "Artist One"}],
        },
        "original_search_result": {
            "title": "Song One",
            "album": "Album One",
            "clean_title": "Song One",
            "clean_album": "Album One",
            "clean_artist": "Artist One",
        },
    }
    )

    album_info = build_import_album_info(context)

    assert album_info["is_album"] is True
    assert album_info["album_name"] == "Album One"
    assert album_info["track_number"] == 4
    assert album_info["disc_number"] == 2
    assert album_info["clean_track_name"] == "Song One"
    assert album_info["album_image_url"] == "https://img.example/album.jpg"
    assert album_info["source"] == "deezer"
