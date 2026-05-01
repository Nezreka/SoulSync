import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.metadata import registry


def test_spotify_disconnect_source_uses_deezer_when_spotify_is_primary():
    assert registry.get_spotify_disconnect_source("spotify") == "deezer"


def test_spotify_disconnect_source_keeps_non_spotify_primary():
    assert registry.get_spotify_disconnect_source("discogs") == "discogs"


def test_metadata_source_label_maps_known_sources():
    assert registry.get_metadata_source_label("spotify") == "Spotify"
    assert registry.get_metadata_source_label("itunes") == "iTunes"
    assert registry.get_metadata_source_label("deezer") == "Deezer"
    assert registry.get_metadata_source_label("discogs") == "Discogs"
    assert registry.get_metadata_source_label("hydrabase") == "Hydrabase"


def test_metadata_source_label_falls_back_to_unmapped():
    assert registry.get_metadata_source_label("apple_music") == "Unmapped"
