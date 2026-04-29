"""Metadata package public surface."""

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
    get_artist_detail_discography,
    get_artist_discography,
)
from core.metadata.lookup import MetadataLookupOptions
from core.metadata.registry import (
    METADATA_SOURCE_PRIORITY,
    clear_cached_metadata_client,
    clear_cached_metadata_clients,
    get_client_for_source,
    get_deezer_client,
    get_discogs_client,
    get_hydrabase_client,
    get_itunes_client,
    get_primary_client,
    get_primary_source,
    get_registered_runtime_client,
    get_source_priority,
    get_spotify_client,
    is_hydrabase_enabled,
    register_runtime_clients,
)
from core.metadata.service import MetadataProvider, MetadataService, get_metadata_service
from core.metadata.similar_artists import (
    get_musicmap_similar_artists,
    iter_musicmap_similar_artist_events,
)

__all__ = [
    "METADATA_SOURCE_PRIORITY",
    "MetadataCache",
    "MetadataLookupOptions",
    "MetadataProvider",
    "MetadataService",
    "check_album_completion",
    "check_artist_discography_completion",
    "check_single_completion",
    "clear_cached_metadata_client",
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
    "get_metadata_cache",
    "get_metadata_service",
    "get_musicmap_similar_artists",
    "get_primary_client",
    "get_primary_source",
    "get_registered_runtime_client",
    "get_spotify_client",
    "get_source_priority",
    "iter_artist_discography_completion_events",
    "iter_musicmap_similar_artist_events",
    "is_hydrabase_enabled",
    "register_runtime_clients",
    "resolve_album_reference",
]
