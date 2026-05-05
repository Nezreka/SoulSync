"""Metadata provider adapters."""

from .spotify import SpotifyMetadataAdapter
from .deezer import DeezerMetadataAdapter
from .itunes import ITunesMetadataAdapter
from .discogs import DiscogsMetadataAdapter

__all__ = [
    "SpotifyMetadataAdapter",
    "DeezerMetadataAdapter",
    "ITunesMetadataAdapter",
    "DiscogsMetadataAdapter",
]
