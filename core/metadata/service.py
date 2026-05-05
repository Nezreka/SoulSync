"""Compatibility metadata service facade.

The modern lookup code prefers standalone functions and shared registry
helpers, but the legacy `MetadataService` wrapper remains available for
call sites that still expect an object.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal

from core.metadata.engine import get_metadata_engine
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
            spotify_client = get_client_for_source("spotify")
            return spotify_client or self.itunes
        return get_client_for_source(provider)

    def _get_metadata_client(self, provider: str):
        provider = (provider or "").strip().lower()
        if provider == "hydrabase":
            return get_client_for_source(provider)
        return get_client_for_source(provider)

    def _get_spotify_fallback_client(self):
        if self.spotify and self.spotify.is_spotify_authenticated():
            return None
        return self.itunes

    def search_tracks(self, query: str, limit: int = 20) -> List:
        provider = self.get_active_provider()
        logger.debug("Searching tracks with %s: %r", provider, query)
        fallback_client = self._get_spotify_fallback_client() if provider == "spotify" else None
        if fallback_client and hasattr(fallback_client, "search_tracks"):
            return list(fallback_client.search_tracks(query, limit=limit))
        if provider == "hydrabase":
            client = self._get_metadata_client(provider)
            if client and hasattr(client, "search_tracks"):
                return list(client.search_tracks(query, limit=limit))
            return []
        outcome = get_metadata_engine().search_tracks(query, limit=limit, source_override=provider, allow_fallback=True)
        return list(outcome.items)

    def search_artists(self, query: str, limit: int = 20) -> List:
        provider = self.get_active_provider()
        logger.debug("Searching artists with %s: %r", provider, query)
        fallback_client = self._get_spotify_fallback_client() if provider == "spotify" else None
        if fallback_client and hasattr(fallback_client, "search_artists"):
            return list(fallback_client.search_artists(query, limit=limit))
        if provider == "hydrabase":
            client = self._get_metadata_client(provider)
            if client and hasattr(client, "search_artists"):
                return list(client.search_artists(query, limit=limit))
            return []
        outcome = get_metadata_engine().search_artists(query, limit=limit, source_override=provider, allow_fallback=True)
        return list(outcome.items)

    def search_albums(self, query: str, limit: int = 20) -> List:
        provider = self.get_active_provider()
        logger.debug("Searching albums with %s: %r", provider, query)
        fallback_client = self._get_spotify_fallback_client() if provider == "spotify" else None
        if fallback_client and hasattr(fallback_client, "search_albums"):
            return list(fallback_client.search_albums(query, limit=limit))
        if provider == "hydrabase":
            client = self._get_metadata_client(provider)
            if client and hasattr(client, "search_albums"):
                return list(client.search_albums(query, limit=limit))
            return []
        outcome = get_metadata_engine().search_albums(query, limit=limit, source_override=provider, allow_fallback=True)
        return list(outcome.items)

    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        provider = self.get_active_provider()
        fallback_client = self._get_spotify_fallback_client() if provider == "spotify" else None
        if fallback_client and hasattr(fallback_client, "get_track_details"):
            return fallback_client.get_track_details(track_id)
        if provider == "hydrabase":
            client = self._get_metadata_client(provider)
            if client and hasattr(client, "get_track_details"):
                return client.get_track_details(track_id)
            return None
        outcome = get_metadata_engine().get_track_details(track_id, source_override=provider, allow_fallback=True)
        return outcome.value

    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        provider = self.get_active_provider()
        fallback_client = self._get_spotify_fallback_client() if provider == "spotify" else None
        if fallback_client and hasattr(fallback_client, "get_album"):
            return fallback_client.get_album(album_id)
        if provider == "hydrabase":
            client = self._get_metadata_client(provider)
            if client and hasattr(client, "get_album"):
                return client.get_album(album_id)
            return None
        outcome = get_metadata_engine().get_album(album_id, source_override=provider, allow_fallback=True)
        return outcome.value

    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        provider = self.get_active_provider()
        logger.debug("Fetching album tracks with %s: %s", provider, album_id)
        fallback_client = self._get_spotify_fallback_client() if provider == "spotify" else None
        if fallback_client and hasattr(fallback_client, "get_album_tracks"):
            return fallback_client.get_album_tracks(album_id)
        if provider == "hydrabase":
            client = self._get_metadata_client(provider)
            if client and hasattr(client, "get_album_tracks"):
                return client.get_album_tracks(album_id)
            return None
        outcome = get_metadata_engine().get_album_tracks(album_id, source_override=provider, allow_fallback=True)
        return outcome.value

    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        provider = self.get_active_provider()
        fallback_client = self._get_spotify_fallback_client() if provider == "spotify" else None
        if fallback_client and hasattr(fallback_client, "get_artist"):
            return fallback_client.get_artist(artist_id)
        if provider == "hydrabase":
            client = self._get_metadata_client(provider)
            if client and hasattr(client, "get_artist"):
                return client.get_artist(artist_id)
            return None
        outcome = get_metadata_engine().get_artist(artist_id, source_override=provider, allow_fallback=True)
        return outcome.value

    def get_artist_albums(self, artist_id: str, album_type: str = "album,single", limit: int = 50) -> List:
        provider = self.get_active_provider()
        logger.debug("Fetching artist albums with %s: %s", provider, artist_id)
        fallback_client = self._get_spotify_fallback_client() if provider == "spotify" else None
        if fallback_client and hasattr(fallback_client, "get_artist_albums"):
            return list(fallback_client.get_artist_albums(artist_id, album_type=album_type, limit=limit))
        if provider == "hydrabase":
            client = self._get_metadata_client(provider)
            if client and hasattr(client, "get_artist_albums"):
                return list(client.get_artist_albums(artist_id, album_type=album_type, limit=limit))
            return []
        outcome = get_metadata_engine().get_artist_albums(
            artist_id,
            album_type=album_type,
            limit=limit,
            source_override=provider,
            allow_fallback=True,
        )
        return list(outcome.items)

    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        provider = self.get_active_provider()
        fallback_client = self._get_spotify_fallback_client() if provider == "spotify" else None
        if fallback_client and hasattr(fallback_client, "get_track_features"):
            return fallback_client.get_track_features(track_id)
        if provider == "hydrabase":
            client = self._get_metadata_client(provider)
            if client and hasattr(client, "get_track_features"):
                return client.get_track_features(track_id)
            return None
        outcome = get_metadata_engine().get_track_features(track_id, source_override=provider, allow_fallback=True)
        return outcome.value

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
        get_metadata_engine().reload_config()
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
