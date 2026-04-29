"""Compatibility facade for package-owned metadata API.

Explicit re-exports keep the old import path working while staying visible to
static analysis tools such as Pylance.
"""

from __future__ import annotations

import sys

import requests

from core.metadata.api import (
    METADATA_SOURCE_PRIORITY,
    MetadataLookupOptions,
    MetadataProvider,
    MetadataService,
    SpotifyClient,
    iTunesClient,
    _search_albums_for_source,
    check_album_completion,
    check_artist_discography_completion,
    check_single_completion,
    clear_cached_metadata_clients,
    get_album_for_source,
    get_album_tracks_for_source,
    get_artist_album_tracks,
    get_artist_albums_for_source,
    get_artist_detail_discography,
    get_artist_discography,
    get_artist_image_url,
    get_client_for_source,
    get_deezer_client,
    get_discogs_client,
    get_hydrabase_client,
    get_itunes_client,
    get_metadata_service,
    get_musicmap_similar_artists,
    get_primary_client,
    get_primary_source,
    get_spotify_client,
    get_source_priority,
    iter_artist_discography_completion_events,
    iter_musicmap_similar_artist_events,
    is_hydrabase_enabled,
    resolve_album_reference,
)
from core.metadata import api as _api

__all__ = [
    "METADATA_SOURCE_PRIORITY",
    "MetadataLookupOptions",
    "MetadataProvider",
    "MetadataService",
    "SpotifyClient",
    "iTunesClient",
    "_search_albums_for_source",
    "check_album_completion",
    "check_artist_discography_completion",
    "check_single_completion",
    "clear_cached_metadata_clients",
    "get_album_for_source",
    "get_album_tracks_for_source",
    "get_artist_album_tracks",
    "get_artist_albums_for_source",
    "get_artist_detail_discography",
    "get_artist_discography",
    "get_artist_image_url",
    "get_client_for_source",
    "get_deezer_client",
    "get_discogs_client",
    "get_hydrabase_client",
    "get_itunes_client",
    "get_metadata_service",
    "get_musicmap_similar_artists",
    "get_primary_client",
    "get_primary_source",
    "get_spotify_client",
    "get_source_priority",
    "iter_artist_discography_completion_events",
    "iter_musicmap_similar_artist_events",
    "is_hydrabase_enabled",
    "resolve_album_reference",
    "requests",
]

sys.modules[__name__] = _api
