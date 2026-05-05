"""Shared metadata source constants."""

from __future__ import annotations

METADATA_SOURCE_PRIORITY = ("deezer", "itunes", "spotify", "discogs", "hydrabase")

METADATA_SOURCE_LABELS = {
    "spotify": "Spotify",
    "itunes": "iTunes",
    "deezer": "Deezer",
    "discogs": "Discogs",
    "hydrabase": "Hydrabase",
}

METADATA_PROVIDER_SOURCES = ("spotify", "deezer", "itunes", "discogs")
