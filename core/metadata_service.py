"""Legacy metadata shim.

This module keeps the historical ``core.metadata_service`` import path alive
while re-exporting the refactored metadata helpers from their package modules.
"""

from __future__ import annotations

import requests

from core.metadata.album_tracks import (
    get_album_for_source,
    get_album_tracks_for_source,
    get_artist_album_tracks,
    get_artist_albums_for_source,
    resolve_album_reference,
)
from core.metadata.artist_image import get_artist_image_url
from core.metadata.cache import MetadataCache, get_metadata_cache
from core.metadata.completion import (
    check_album_completion,
    check_artist_discography_completion,
    check_single_completion,
    iter_artist_discography_completion_events,
)
from core.metadata.discography import (
    _build_artist_detail_release_card,
    _build_discography_release_dict,
    _dedup_variant_releases,
    _extract_release_artist_name,
    _normalize_artist_name,
    _pick_best_artist_match,
    _search_albums_for_source,
    _search_artists_for_source,
    _sort_discography_releases,
    get_artist_detail_discography,
    get_artist_discography,
)
from core.metadata.lookup import MetadataLookupOptions
from core.metadata.registry import (
    METADATA_SOURCE_PRIORITY,
    clear_cached_metadata_client,
    clear_cached_metadata_clients,
    clear_cached_profile_spotify_client,
    get_client_for_source,
    get_deezer_client,
    get_discogs_client,
    get_hydrabase_client,
    get_itunes_client,
    get_primary_client,
    get_primary_source,
    get_spotify_client_for_profile,
    get_registered_runtime_client,
    get_source_priority,
    get_spotify_client,
    is_hydrabase_enabled,
    register_profile_spotify_credentials_provider,
    register_runtime_clients,
)
from core.metadata.service import MetadataProvider, MetadataService, get_metadata_service
from core.metadata.similar_artists import (
    get_musicmap_similar_artists,
    iter_musicmap_similar_artist_events,
)

try:
    from core.spotify_client import SpotifyClient
except Exception:  # pragma: no cover - optional dependency fallback
    SpotifyClient = None  # type: ignore[assignment]

try:
    from core.itunes_client import iTunesClient
except Exception:  # pragma: no cover - optional dependency fallback
    iTunesClient = None  # type: ignore[assignment]

__all__ = [
    "METADATA_SOURCE_PRIORITY",
    "MetadataCache",
    "MetadataLookupOptions",
    "MetadataProvider",
    "MetadataService",
    "SpotifyClient",
    "iTunesClient",
    "_build_artist_detail_release_card",
    "_build_discography_release_dict",
    "_dedup_variant_releases",
    "_extract_release_artist_name",
    "_normalize_artist_name",
    "_pick_best_artist_match",
    "_search_albums_for_source",
    "_search_artists_for_source",
    "_sort_discography_releases",
    "check_album_completion",
    "check_artist_discography_completion",
    "check_single_completion",
    "clear_cached_metadata_client",
    "clear_cached_metadata_clients",
    "clear_cached_profile_spotify_client",
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
    "get_metadata_cache",
    "get_metadata_service",
    "get_musicmap_similar_artists",
    "get_primary_client",
    "get_primary_source",
    "get_spotify_client_for_profile",
    "get_registered_runtime_client",
    "get_spotify_client",
    "get_source_priority",
    "iter_artist_discography_completion_events",
    "iter_musicmap_similar_artist_events",
    "is_hydrabase_enabled",
    "register_profile_spotify_credentials_provider",
    "register_runtime_clients",
    "requests",
    "resolve_album_reference",
]
