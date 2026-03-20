"""
Metadata Service - Hot-swappable Spotify/iTunes/Deezer provider

Automatically uses Spotify when authenticated, falls back to the configured
fallback source (iTunes or Deezer) when not.
Provides unified interface for all metadata operations.
"""

from typing import List, Optional, Dict, Any, Literal
from core.spotify_client import SpotifyClient
from core.itunes_client import iTunesClient
from utils.logging_config import get_logger

logger = get_logger("metadata_service")

MetadataProvider = Literal["spotify", "itunes", "auto"]


def _get_configured_fallback_source():
    """Get the configured metadata fallback source ('itunes' or 'deezer')."""
    try:
        from config.settings import config_manager
        return config_manager.get('metadata.fallback_source', 'itunes') or 'itunes'
    except Exception:
        return 'itunes'


def _create_fallback_client():
    """Create the configured fallback metadata client."""
    source = _get_configured_fallback_source()
    if source == 'deezer':
        from core.deezer_client import DeezerClient
        return DeezerClient()
    if source == 'hydrabase':
        try:
            from core.hydrabase_client import HydrabaseClient
            # Hydrabase client is managed globally — try to import the running instance
            import importlib
            ws_module = importlib.import_module('web_server')
            client = getattr(ws_module, 'hydrabase_client', None)
            if client and client.is_connected():
                return client
        except Exception:
            pass
        # Hydrabase not available — fall back to iTunes
        return iTunesClient()
    return iTunesClient()


class MetadataService:
    """
    Unified metadata service that seamlessly switches between Spotify and
    the configured fallback source (iTunes or Deezer).

    Usage:
        service = MetadataService()
        tracks = service.search_tracks("Radiohead OK Computer")
        # Uses Spotify if authenticated, otherwise configured fallback
    """

    def __init__(self, preferred_provider: MetadataProvider = "auto"):
        """
        Initialize metadata service.

        Args:
            preferred_provider: "spotify", "itunes", or "auto" (default)
                - "auto": Use Spotify if authenticated, else configured fallback
                - "spotify": Always use Spotify (may fail if not authenticated)
                - "itunes": Always use configured fallback source
        """
        self.preferred_provider = preferred_provider
        self.spotify = SpotifyClient()
        self._fallback_source = _get_configured_fallback_source()
        self.itunes = _create_fallback_client()  # May be iTunesClient or DeezerClient

        self._log_initialization()

    def _log_initialization(self):
        """Log initialization status"""
        spotify_status = "✅ Authenticated" if self.spotify.is_spotify_authenticated() else "❌ Not authenticated"
        fallback_status = "✅ Available" if self.itunes.is_authenticated() else "❌ Not available"

        logger.info(f"MetadataService initialized - Spotify: {spotify_status}, {self._fallback_source.capitalize()}: {fallback_status}")
        logger.info(f"Preferred provider: {self.preferred_provider}")

    def get_active_provider(self) -> str:
        """
        Get the currently active metadata provider.

        Returns:
            "spotify" or the configured fallback source name
        """
        if self.preferred_provider == "spotify":
            return "spotify"
        elif self.preferred_provider == "itunes":
            return self._fallback_source
        else:  # auto
            # Use is_spotify_authenticated() to check actual Spotify auth status
            # (is_authenticated() always returns True due to fallback)
            return "spotify" if self.spotify.is_spotify_authenticated() else self._fallback_source

    def _get_client(self):
        """Get the appropriate client based on provider selection"""
        provider = self.get_active_provider()

        if provider == "spotify":
            if not self.spotify.is_spotify_authenticated():
                logger.warning(f"Spotify requested but not authenticated, falling back to {self._fallback_source}")
                return self.itunes
            return self.spotify
        else:
            return self.itunes
    
    # ==================== Search Methods ====================
    
    def search_tracks(self, query: str, limit: int = 20) -> List:
        """
        Search for tracks using active provider.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of Track objects
        """
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Searching tracks with {provider}: '{query}'")
        return client.search_tracks(query, limit)
    
    def search_artists(self, query: str, limit: int = 20) -> List:
        """
        Search for artists using active provider.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of Artist objects
        """
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Searching artists with {provider}: '{query}'")
        return client.search_artists(query, limit)
    
    def search_albums(self, query: str, limit: int = 20) -> List:
        """
        Search for albums using active provider.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of Album objects
        """
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Searching albums with {provider}: '{query}'")
        return client.search_albums(query, limit)
    
    # ==================== Detail Fetching ====================
    
    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed track information"""
        client = self._get_client()
        return client.get_track_details(track_id)
    
    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album information"""
        client = self._get_client()
        return client.get_album(album_id)
    
    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get all tracks from an album"""
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Fetching album tracks with {provider}: {album_id}")
        return client.get_album_tracks(album_id)
    
    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """Get artist information"""
        client = self._get_client()
        return client.get_artist(artist_id)
    
    def get_artist_albums(self, artist_id: str, album_type: str = "album,single", limit: int = 50) -> List:
        """Get artist's albums/discography"""
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Fetching artist albums with {provider}: {artist_id}")
        return client.get_artist_albums(artist_id, album_type, limit)
    
    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        """
        Get track audio features (Spotify only).
        Returns None for iTunes.
        """
        client = self._get_client()
        return client.get_track_features(track_id)
    
    # ==================== User Library (Spotify only) ====================
    
    def get_user_playlists(self) -> List:
        """Get user playlists (Spotify only)"""
        if self.spotify.is_spotify_authenticated():
            return self.spotify.get_user_playlists()
        logger.warning("User playlists only available with Spotify authentication")
        return []

    def get_saved_tracks(self) -> List:
        """Get user's saved/liked tracks (Spotify only)"""
        if self.spotify.is_spotify_authenticated():
            return self.spotify.get_saved_tracks()
        logger.warning("Saved tracks only available with Spotify authentication")
        return []

    def get_saved_tracks_count(self) -> int:
        """Get count of user's saved tracks (Spotify only)"""
        if self.spotify.is_spotify_authenticated():
            return self.spotify.get_saved_tracks_count()
        return 0

    # ==================== Utility Methods ====================

    def is_authenticated(self) -> bool:
        """Check if any provider is available"""
        return self.spotify.is_spotify_authenticated() or self.itunes.is_authenticated()

    def get_provider_info(self) -> Dict[str, Any]:
        """Get information about available providers"""
        return {
            "active_provider": self.get_active_provider(),
            "spotify_authenticated": self.spotify.is_spotify_authenticated(),
            "itunes_available": self.itunes.is_authenticated(),
            "fallback_source": self._fallback_source,
            "preferred_provider": self.preferred_provider,
            "can_access_user_data": self.spotify.is_spotify_authenticated(),
        }
    
    def reload_config(self):
        """Reload configuration for both clients"""
        logger.info("Reloading metadata service configuration")
        self.spotify.reload_config()
        # Re-create fallback client in case the setting changed
        new_source = _get_configured_fallback_source()
        if new_source != self._fallback_source:
            self._fallback_source = new_source
            self.itunes = _create_fallback_client()
        elif hasattr(self.itunes, 'reload_config'):
            self.itunes.reload_config()
        self._log_initialization()


# Convenience singleton instance
_metadata_service_instance: Optional[MetadataService] = None


def get_metadata_service() -> MetadataService:
    """
    Get global metadata service instance (singleton pattern).
    
    Returns:
        MetadataService instance
    """
    global _metadata_service_instance
    if _metadata_service_instance is None:
        _metadata_service_instance = MetadataService()
    return _metadata_service_instance
