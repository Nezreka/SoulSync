import pytest

from core.playlists.source_refs import (
    normalize_mirrored_source_ref,
    require_refresh_url,
)


def test_spotify_public_url_stores_hash_and_canonical_url():
    out = normalize_mirrored_source_ref(
        "spotify_public",
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc",
    )

    assert out.source_playlist_id == "5e7de827abd1"
    assert out.description == "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"


def test_spotify_public_raw_id_defaults_to_playlist_url():
    out = normalize_mirrored_source_ref("spotify_public", "37i9dQZF1DXcBWIGoYBM5M")

    assert out.source_playlist_id == "5e7de827abd1"
    assert out.description == "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"


def test_youtube_url_stores_hash_and_canonical_url():
    out = normalize_mirrored_source_ref(
        "youtube",
        "https://music.youtube.com/playlist?list=PL123&si=abc",
    )

    assert out.source_playlist_id == "e5f5ab31b8f0"
    assert out.description == "https://youtube.com/playlist?list=PL123"


def test_direct_id_sources_preserve_existing_description():
    out = normalize_mirrored_source_ref("tidal", "abc123", "Original service description")

    assert out.source_playlist_id == "abc123"
    assert out.description == "Original service description"


def test_hash_backed_refresh_requires_url():
    with pytest.raises(ValueError, match="missing its original source URL"):
        require_refresh_url("spotify_public", "", "Release Radar")
