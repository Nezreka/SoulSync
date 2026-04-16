"""
Metadata Service - Centralized metadata source selection

ALL metadata source decisions flow through this module. Other files import
get_primary_source() and get_primary_client() instead of reimplementing
the logic. This prevents bugs where different files have different defaults
or auth checks.
"""

import threading
from typing import List, Optional, Dict, Any, Literal
from core.spotify_client import SpotifyClient
from core.itunes_client import iTunesClient
from utils.logging_config import get_logger

logger = get_logger("metadata_service")

MetadataProvider = Literal["spotify", "itunes", "auto"]

# Ordered by fallback preference. Higher-priority sources appear earlier.
METADATA_SOURCE_PRIORITY = ('deezer', 'itunes', 'spotify', 'discogs', 'hydrabase')

_client_cache_lock = threading.RLock()
_client_cache: Dict[str, Any] = {}


# =============================================================================
# CANONICAL SOURCE SELECTION — all code should use these two functions
# =============================================================================

def get_primary_source() -> str:
    """Get the user's configured primary metadata source.

    Returns 'spotify', 'deezer', 'itunes', 'discogs', or 'hydrabase'.
    If the user selected Spotify but it's not authenticated, falls back to 'deezer'.

    This is THE single source of truth for "which metadata source should I use?"
    All other modules should import this function instead of reading config directly.
    """
    try:
        from config.settings import config_manager
        source = config_manager.get('metadata.fallback_source', 'deezer') or 'deezer'
    except Exception:
        return 'deezer'

    # Validate Spotify selection — can't use it if not authenticated
    if source == 'spotify':
        try:
            import importlib
            ws = importlib.import_module('web_server')
            sc = getattr(ws, 'spotify_client', None)
            if not sc or not sc.is_spotify_authenticated():
                return 'deezer'
        except Exception:
            return 'deezer'

    return source


def get_primary_client():
    """Get the client object for the user's configured primary metadata source.

    Returns a SpotifyClient, DeezerClient, iTunesClient, DiscogsClient,
    or HydrabaseClient instance.

    This is THE single source of truth for "which client should I call?"
    """
    return get_client_for_source(get_primary_source())


def get_source_priority(preferred_source: str):
    """Return supported sources with the preferred source first."""
    ordered = []

    if preferred_source in METADATA_SOURCE_PRIORITY:
        ordered.append(preferred_source)

    for source in METADATA_SOURCE_PRIORITY:
        if source not in ordered:
            ordered.append(source)

    return ordered


def get_client_for_source(source: str):
    """Get the client object for an exact metadata source.

    Returns the matching client or None if that source is unavailable.
    No fallback swaps.
    """
    if source == 'spotify':
        try:
            import importlib
            ws = importlib.import_module('web_server')
            sc = getattr(ws, 'spotify_client', None)
            if sc and sc.is_spotify_authenticated():
                return sc
        except Exception:
            pass
        return None

    if source == 'deezer':
        return get_deezer_client()

    if source == 'discogs':
        return get_discogs_client()

    if source == 'hydrabase':
        return get_hydrabase_client(allow_fallback=False)

    if source == 'itunes':
        return get_itunes_client()

    return None


def get_album_tracks_for_source(source: str, album_id: str):
    """Get album tracks for an exact source.

    Returns Spotify-compatible dict/list data or None.
    No fallback swaps.
    """
    client = get_client_for_source(source)
    if not client:
        return None

    try:
        fetch = getattr(client, 'get_album_tracks_dict', None) if source == 'hydrabase' else getattr(client, 'get_album_tracks', None)
        if not fetch:
            return None
        if source == 'spotify':
            return fetch(album_id, allow_fallback=False)
        return fetch(album_id)
    except Exception:
        return None


def get_deezer_client():
    """Get cached Deezer client.

    Deezer client is safe to reuse across requests because it owns no
    request-specific state beyond the current access token.
    """
    from core.deezer_client import DeezerClient
    try:
        from config.settings import config_manager
        current_token = config_manager.get('deezer.access_token', None)
    except Exception:
        current_token = None

    cache_key = f"deezer::{current_token or ''}"
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = DeezerClient()
            _client_cache[cache_key] = client
        return client


def get_itunes_client():
    """Get cached iTunes client."""
    with _client_cache_lock:
        client = _client_cache.get("itunes")
        if client is None:
            client = iTunesClient()
            _client_cache["itunes"] = client
        return client


def get_discogs_client(token: Optional[str] = None):
    """Get cached Discogs client.

    Discogs auth changes are token-driven, so the cache key tracks the
    current configured token.
    """
    if token is None:
        try:
            from config.settings import config_manager
            current_token = config_manager.get('discogs.token', '') or ''
        except Exception:
            current_token = ''
    else:
        current_token = token or ''

    cache_key = f"discogs::{current_token}"
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            from core.discogs_client import DiscogsClient
            client = DiscogsClient(token=current_token or None)
            _client_cache[cache_key] = client
        return client


def get_hydrabase_client(allow_fallback: bool = True):
    """Return current Hydrabase client if connected.

    If allow_fallback is True, return iTunes fallback when Hydrabase is not
    connected. If False, return None instead.
    """
    try:
        import importlib
        ws = importlib.import_module('web_server')
        client = getattr(ws, 'hydrabase_client', None)
        if client and client.is_connected():
            return client
    except Exception:
        pass
    if allow_fallback:
        return get_itunes_client()
    return None


def clear_cached_metadata_clients():
    """Clear cached metadata clients.

    Useful for tests and config reload flows.
    """
    with _client_cache_lock:
        _client_cache.clear()


def _get_client_for_source(source: str):
    if source == 'spotify':
        try:
            import importlib
            ws = importlib.import_module('web_server')
            sc = getattr(ws, 'spotify_client', None)
            if sc and sc.is_spotify_authenticated():
                return sc
        except Exception:
            pass
        return get_deezer_client()

    if source == 'deezer':
        return get_deezer_client()

    if source == 'discogs':
        return get_discogs_client()

    if source == 'hydrabase':
        return get_hydrabase_client()

    return get_itunes_client()


# =============================================================================
# LEGACY ALIASES — kept for backward compatibility, delegate to canonical funcs
# =============================================================================

def _get_configured_fallback_source():
    """Legacy alias for get_primary_source(). Use get_primary_source() instead."""
    return get_primary_source()


def _create_fallback_client():
    """Legacy alias for get_primary_client(). Use get_primary_client() instead."""
    return get_primary_client()


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
        self._fallback_source = get_primary_source()
        self.itunes = get_client_for_source(self._fallback_source)

        self._log_initialization()

    def _log_initialization(self):
        """Log initialization status"""
        spotify_status = "Authenticated" if self.spotify.is_spotify_authenticated() else "Not authenticated"
        fallback_status = "Available" if self.itunes.is_authenticated() else "Not available"

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
        else:  # auto — use the centralized source selection
            return get_primary_source()

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
        new_source = get_primary_source()
        self._fallback_source = new_source
        self.itunes = get_client_for_source(new_source)
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
