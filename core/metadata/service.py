"""Compatibility metadata service facade.

The modern lookup code prefers standalone functions and shared registry
helpers, but the legacy `MetadataService` wrapper remains available for
call sites that still expect an object.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal

from core.metadata.registry import (
    get_client_for_source,
    get_primary_source,
    get_spotify_client,
)
from utils.logging_config import get_logger

logger = get_logger("metadata_service")

MetadataProvider = Literal["spotify", "itunes", "auto"]


class MetadataService:
    """
    Unified metadata service that seamlessly switches between Spotify and
    the configured fallback source.
    """

    def __init__(self, preferred_provider: MetadataProvider = "auto"):
        self.preferred_provider = preferred_provider
        try:
            self.spotify = get_spotify_client()
        except Exception:
            self.spotify = None
        self._fallback_source = get_primary_source()
        try:
            self.itunes = get_client_for_source(self._fallback_source)
        except Exception:
            self.itunes = None
        self._log_initialization()

    def _log_initialization(self):
        spotify_status = "Authenticated" if self.spotify and self.spotify.is_spotify_authenticated() else "Not authenticated"
        fallback_status = "Available" if self.itunes and getattr(self.itunes, "is_authenticated", lambda: False)() else "Not available"

        logger.info(
            "MetadataService initialized - Spotify: %s, %s: %s",
            spotify_status,
            self._fallback_source.capitalize(),
            fallback_status,
        )
        logger.info("Preferred provider: %s", self.preferred_provider)

    def get_active_provider(self) -> str:
        if self.preferred_provider == "spotify":
            return "spotify"
        if self.preferred_provider == "itunes":
            return self._fallback_source
        return get_primary_source()

    def _get_client(self):
        provider = self.get_active_provider()
        if provider == "spotify":
            if not self.spotify or not self.spotify.is_spotify_authenticated():
                logger.warning(
                    "Spotify requested but not authenticated, falling back to %s",
                    self._fallback_source,
                )
                return self.itunes
            return self.spotify
        return self.itunes

    def search_tracks(self, query: str, limit: int = 20) -> List:
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug("Searching tracks with %s: %r", provider, query)
        return client.search_tracks(query, limit)

    def search_artists(self, query: str, limit: int = 20) -> List:
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug("Searching artists with %s: %r", provider, query)
        return client.search_artists(query, limit)

    def search_albums(self, query: str, limit: int = 20) -> List:
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug("Searching albums with %s: %r", provider, query)
        return client.search_albums(query, limit)

    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        client = self._get_client()
        return client.get_track_details(track_id)

    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        client = self._get_client()
        return client.get_album(album_id)

    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug("Fetching album tracks with %s: %s", provider, album_id)
        return client.get_album_tracks(album_id)

    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        client = self._get_client()
        return client.get_artist(artist_id)

    def get_artist_albums(self, artist_id: str, album_type: str = "album,single", limit: int = 50) -> List:
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug("Fetching artist albums with %s: %s", provider, artist_id)
        return client.get_artist_albums(artist_id, album_type, limit)

    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        client = self._get_client()
        return client.get_track_features(track_id)

    def get_user_playlists(self) -> List:
        if self.spotify and self.spotify.is_spotify_authenticated():
            return self.spotify.get_user_playlists()
        logger.warning("User playlists only available with Spotify authentication")
        return []

    def get_saved_tracks(self) -> List:
        if self.spotify and self.spotify.is_spotify_authenticated():
            return self.spotify.get_saved_tracks()
        logger.warning("Saved tracks only available with Spotify authentication")
        return []

    def get_saved_tracks_count(self) -> int:
        if self.spotify and self.spotify.is_spotify_authenticated():
            return self.spotify.get_saved_tracks_count()
        return 0

    def is_authenticated(self) -> bool:
        return bool(self.spotify and self.spotify.is_spotify_authenticated()) or bool(
            self.itunes and getattr(self.itunes, "is_authenticated", lambda: False)()
        )

    def get_provider_info(self) -> Dict[str, Any]:
        spotify_authenticated = bool(self.spotify and self.spotify.is_spotify_authenticated())
        itunes_available = bool(self.itunes and getattr(self.itunes, "is_authenticated", lambda: False)())
        return {
            "active_provider": self.get_active_provider(),
            "spotify_authenticated": spotify_authenticated,
            "itunes_available": itunes_available,
            "fallback_source": self._fallback_source,
            "preferred_provider": self.preferred_provider,
            "can_access_user_data": spotify_authenticated,
        }

    def reload_config(self):
        logger.info("Reloading metadata service configuration")
        if self.spotify and hasattr(self.spotify, "reload_config"):
            self.spotify.reload_config()
        new_source = get_primary_source()
        self._fallback_source = new_source
        try:
            self.itunes = get_client_for_source(new_source)
        except Exception:
            self.itunes = None
        self._log_initialization()


_metadata_service_instance: Optional[MetadataService] = None


def get_metadata_service() -> MetadataService:
    global _metadata_service_instance
    if _metadata_service_instance is None:
        _metadata_service_instance = MetadataService()
    return _metadata_service_instance
