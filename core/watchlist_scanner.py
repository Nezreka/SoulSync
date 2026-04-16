#!/usr/bin/env python3

"""
Watchlist Scanner Service - Monitors watched artists for new releases
"""

from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
import re
import time
import requests
from bs4 import BeautifulSoup
from database.music_database import get_database, WatchlistArtist
from core.spotify_client import SpotifyClient
from core.metadata_service import (
    get_album_tracks_for_source,
    get_client_for_source,
    get_primary_source,
    get_source_priority,
)
from core.wishlist_service import get_wishlist_service
from core.matching_engine import MusicMatchingEngine
from utils.logging_config import get_logger

logger = get_logger("watchlist_scanner")

# Rate limiting constants for watchlist operations
DELAY_BETWEEN_ARTISTS = 4.0      # 4 seconds between different artists (was 2s, increased to reduce Spotify rate limit risk)
DELAY_BETWEEN_ALBUMS = 0.5       # 500ms between albums for same artist
DELAY_BETWEEN_API_BATCHES = 1.0  # 1 second between API batch operations

# iTunes API retry configuration
ITUNES_MAX_RETRIES = 3
ITUNES_BASE_DELAY = 1.0  # Base delay in seconds for exponential backoff


def _get_fallback_metadata_client():
    """Get the configured metadata client — delegates to centralized metadata_service."""
    from core.metadata_service import get_primary_source, get_primary_client
    return get_primary_client(), get_primary_source()


def itunes_api_call_with_retry(func, *args, max_retries=ITUNES_MAX_RETRIES, **kwargs):
    """
    Execute an iTunes API call with exponential backoff retry logic.

    Args:
        func: The function to call
        *args: Arguments to pass to the function
        max_retries: Maximum number of retry attempts
        **kwargs: Keyword arguments to pass to the function

    Returns:
        The result of the function call, or None if all retries failed
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            result = func(*args, **kwargs)
            return result
        except requests.exceptions.HTTPError as e:
            # Handle rate limiting (429) and server errors (5xx)
            if e.response is not None and e.response.status_code == 429:
                delay = ITUNES_BASE_DELAY * (2 ** attempt)
                logger.warning(f"[iTunes] Rate limited, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                last_error = e
            elif e.response is not None and e.response.status_code >= 500:
                delay = ITUNES_BASE_DELAY * (2 ** attempt)
                logger.warning(f"[iTunes] Server error {e.response.status_code}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                last_error = e
            else:
                raise  # Don't retry on client errors (4xx except 429)
        except requests.exceptions.RequestException as e:
            # Retry on connection errors
            delay = ITUNES_BASE_DELAY * (2 ** attempt)
            logger.warning(f"[iTunes] Connection error, retrying in {delay}s (attempt {attempt + 1}/{max_retries}): {e}")
            time.sleep(delay)
            last_error = e
        except Exception as e:
            # Don't retry on other exceptions
            raise

    if last_error:
        logger.error(f"[iTunes] All {max_retries} retry attempts failed: {last_error}")
    return None


def clean_track_name_for_search(track_name):
    """
    Intelligently cleans a track name for searching by removing noise while preserving important version information.
    Removes: (feat. Artist), (Explicit), (Clean), etc.
    Keeps: (Extended Version), (Live), (Acoustic), (Remix), etc.
    """
    if not track_name or not isinstance(track_name, str):
        return track_name

    cleaned_name = track_name
    
    # Define patterns to REMOVE (noise that doesn't affect track identity)
    remove_patterns = [
        r'\s*\(explicit\)',           # (Explicit)
        r'\s*\(clean\)',              # (Clean) 
        r'\s*\(radio\s*edit\)',       # (Radio Edit)
        r'\s*\(radio\s*version\)',    # (Radio Version)
        r'\s*\(feat\.?\s*[^)]+\)',    # (feat. Artist) or (ft. Artist)
        r'\s*\(ft\.?\s*[^)]+\)',      # (ft Artist)
        r'\s*\(featuring\s*[^)]+\)',  # (featuring Artist)
        r'\s*\(with\s*[^)]+\)',       # (with Artist)
        r'\s*\[[^\]]*explicit[^\]]*\]', # [Explicit] in brackets
        r'\s*\[[^\]]*clean[^\]]*\]',    # [Clean] in brackets
    ]
    
    # Apply removal patterns
    for pattern in remove_patterns:
        cleaned_name = re.sub(pattern, '', cleaned_name, flags=re.IGNORECASE).strip()
    
    # PRESERVE important version information (do NOT remove these)
    # These patterns are intentionally NOT in the remove list:
    # - (Extended Version), (Extended), (Long Version)
    # - (Live), (Live Version), (Concert)
    # - (Acoustic), (Acoustic Version)  
    # - (Remix), (Club Mix), (Dance Mix)
    # - (Remastered), (Remaster)
    # - (Demo), (Studio Version)
    # - (Instrumental)
    # - Album/year info like (2023), (Deluxe Edition)
    
    # If cleaning results in an empty string, return the original track name
    if not cleaned_name.strip():
        return track_name
        
    # Log cleaning if significant changes were made
    if cleaned_name != track_name:
        logger.debug(f"Intelligent track cleaning: '{track_name}' -> '{cleaned_name}'")
    
    return cleaned_name

def is_live_version(track_name: str, album_name: str = "") -> bool:
    """
    Detect if a track or album is a live version.

    Args:
        track_name: Track name to check
        album_name: Album name to check (optional)

    Returns:
        True if this is a live version, False otherwise
    """
    if not track_name:
        return False

    # Combine track and album names for comprehensive checking
    text_to_check = f"{track_name} {album_name}".lower()

    # Live version patterns
    live_patterns = [
        r'\blive\b',                    # (Live), Live at, etc.
        r'\blive at\b',                 # Live at Madison Square Garden
        r'\bconcert\b',                 # Concert, Live Concert
        r'\bin concert\b',              # In Concert
        r'\bunplugged\b',               # MTV Unplugged (usually live)
        r'\blive session\b',            # Live Session
        r'\blive from\b',               # Live from...
        r'\blive recording\b',          # Live Recording
        r'\bon stage\b',                # On Stage
    ]

    for pattern in live_patterns:
        if re.search(pattern, text_to_check, re.IGNORECASE):
            return True

    return False

def is_remix_version(track_name: str, album_name: str = "") -> bool:
    """
    Detect if a track is a remix.

    Args:
        track_name: Track name to check
        album_name: Album name to check (optional)

    Returns:
        True if this is a remix, False otherwise
    """
    if not track_name:
        return False

    # Combine track and album names for comprehensive checking
    text_to_check = f"{track_name} {album_name}".lower()

    # Remix patterns (but NOT remaster/remastered)
    remix_patterns = [
        r'\bremix\b',                   # Remix, Remixed
        r'\bmix\b(?!.*\bremaster)',     # Mix (but not if followed by remaster)
        r'\bedit\b',                    # Radio Edit, Extended Edit
        r'\bversion\b(?=.*\bmix\b)',    # Version with Mix (e.g., "Dance Version Mix")
        r'\bclub mix\b',                # Club Mix
        r'\bdance mix\b',               # Dance Mix
        r'\bradio edit\b',              # Radio Edit
        r'\bextended\b(?=.*\bmix\b)',   # Extended Mix
        r'\bdub\b',                     # Dub version
        r'\bvip mix\b',                 # VIP Mix
    ]

    # But exclude remaster/remastered - those are originals
    if re.search(r'\bremaster(ed)?\b', text_to_check, re.IGNORECASE):
        return False

    for pattern in remix_patterns:
        if re.search(pattern, text_to_check, re.IGNORECASE):
            return True

    return False

def is_acoustic_version(track_name: str, album_name: str = "") -> bool:
    """
    Detect if a track is an acoustic version.

    Args:
        track_name: Track name to check
        album_name: Album name to check (optional)

    Returns:
        True if this is an acoustic version, False otherwise
    """
    if not track_name:
        return False

    # Combine track and album names for comprehensive checking
    text_to_check = f"{track_name} {album_name}".lower()

    # Acoustic version patterns
    acoustic_patterns = [
        r'\bacoustic\b',                # Acoustic, Acoustic Version
        r'\bstripped\b',                # Stripped version
        r'\bpiano version\b',           # Piano Version
        r'\bunplugged\b',               # MTV Unplugged (can be acoustic)
    ]

    for pattern in acoustic_patterns:
        if re.search(pattern, text_to_check, re.IGNORECASE):
            return True

    return False

def is_instrumental_version(track_name: str, album_name: str = "") -> bool:
    """
    Detect if a track is an instrumental version.

    Args:
        track_name: Track name to check
        album_name: Album name to check (optional)

    Returns:
        True if this is an instrumental version, False otherwise
    """
    if not track_name:
        return False

    text_to_check = f"{track_name} {album_name}".lower()

    instrumental_patterns = [
        r'\binstrumental\b',            # Instrumental, Instrumental Version
        r'\binst\.\b',                  # Inst. (abbreviation)
        r'\bkaraoke\b',                 # Karaoke version
        r'\bbacking track\b',           # Backing Track
    ]

    for pattern in instrumental_patterns:
        if re.search(pattern, text_to_check, re.IGNORECASE):
            return True

    return False


def matches_custom_exclude_terms(track_name: str, album_name: str, exclude_terms: list) -> str:
    """
    Check if a track or album name contains any user-defined exclusion terms.

    Args:
        track_name: Track name to check
        album_name: Album name to check
        exclude_terms: List of terms to exclude (case-insensitive)

    Returns:
        The matched term if found, empty string if no match
    """
    if not exclude_terms:
        return ""

    text_to_check = f"{track_name} {album_name}".lower()

    for term in exclude_terms:
        term = term.strip().lower()
        if not term:
            continue
        if term in text_to_check:
            return term

    return ""


def is_compilation_album(album_name: str) -> bool:
    """
    Detect if an album is a compilation/greatest hits album.

    Args:
        album_name: Album name to check

    Returns:
        True if this is a compilation album, False otherwise
    """
    if not album_name:
        return False

    album_lower = album_name.lower()

    # Compilation album patterns
    compilation_patterns = [
        r'\bgreatest hits\b',           # Greatest Hits
        r'\bbest of\b',                 # Best Of
        r'\banthology\b',               # Anthology
        r'\bcollection\b',              # Collection
        r'\bcompilation\b',             # Compilation
        r'\bthe essential\b',           # The Essential...
        r'\bcomplete\b',                # Complete Collection
        r'\bhits\b',                    # Hits (standalone or at end)
        r'\btop\s+\d+\b',               # Top 10, Top 40, etc.
        r'\bvery best\b',               # Very Best Of
        r'\bdefinitive\b',              # Definitive Collection
    ]

    for pattern in compilation_patterns:
        if re.search(pattern, album_lower, re.IGNORECASE):
            return True

    return False

@dataclass
class ScanResult:
    """Result of scanning a single artist"""
    artist_name: str
    spotify_artist_id: str
    albums_checked: int
    new_tracks_found: int
    tracks_added_to_wishlist: int
    success: bool
    error_message: Optional[str] = None


@dataclass
class WatchlistDiscographyResult:
    """Resolved watchlist artist discography for a specific metadata source."""
    source: str
    artist_id: str
    albums: List[Any]
    image_url: Optional[str] = None

class WatchlistScanner:
    """Service for scanning watched artists for new releases"""
    
    def __init__(self, spotify_client: SpotifyClient = None, metadata_service=None, database_path: str = "database/music_library.db"):
        # Support both old (spotify_client) and new (metadata_service) initialization
        self.database_path = database_path
        self._database = None
        self._wishlist_service = None
        self._matching_engine = None
        self._rescan_cutoff_log_marker = None
        
        if metadata_service:
            self._metadata_service = metadata_service
            self.spotify_client = metadata_service.spotify  # For backward compatibility
        elif spotify_client:
            self.spotify_client = spotify_client
            self._metadata_service = None  # Lazy load if needed
        else:
            raise ValueError("Must provide either spotify_client or metadata_service")

        # Run-local Spotify suppression. One rate-limit hit disables Spotify
        # for rest of current scan, but keeps fallback providers running.
        self._spotify_disabled_for_run = False
        self._spotify_disabled_reason = None
    
    @property
    def database(self):
        """Get database instance (lazy loading)"""
        if self._database is None:
            self._database = get_database(self.database_path)
        return self._database
    
    @property
    def wishlist_service(self):
        """Get wishlist service instance (lazy loading)"""
        if self._wishlist_service is None:
            self._wishlist_service = get_wishlist_service()
        return self._wishlist_service
    
    @property
    def matching_engine(self):
        """Get matching engine instance (lazy loading)"""
        if self._matching_engine is None:
            self._matching_engine = MusicMatchingEngine()
        return self._matching_engine
    
    @property
    def metadata_service(self):
        """Get or create MetadataService instance (lazy loading)"""
        if self._metadata_service is None:
            from core.metadata_service import MetadataService
            self._metadata_service = MetadataService()
        return self._metadata_service

    def _disable_spotify_for_run(self, reason: str):
        """Disable Spotify for rest of current run, once."""
        if not self._spotify_disabled_for_run:
            logger.warning(f"Spotify disabled for rest of run: {reason}")
        self._spotify_disabled_for_run = True
        self._spotify_disabled_reason = reason

    def _spotify_available_for_run(self) -> bool:
        """Check if Spotify should be used for this run."""
        if self._spotify_disabled_for_run:
            return False
        if not self.spotify_client:
            return False
        return self.spotify_client.is_spotify_authenticated()

    def _spotify_is_primary_source(self) -> bool:
        """Check if Spotify is both authenticated and the configured primary metadata source.

        Use this (not _spotify_available_for_run) when deciding whether to fetch
        album/artist data from Spotify.  Plain auth is not sufficient — the user
        may have Spotify connected only for playlist sync while Deezer/iTunes
        serves as the metadata source, and calling Spotify for data in that case
        burns API quota unnecessarily.

        _spotify_available_for_run() is still used for Spotify-specific features
        (e.g. library-cache sync) that must run regardless of primary source.
        """
        if not self._spotify_available_for_run():
            return False
        try:
            return get_primary_source() == 'spotify'
        except Exception:
            return False

    def _watchlist_source_priority(self) -> List[str]:
        """Return watchlist scan sources in the configured priority order."""
        return list(get_source_priority(get_primary_source()))

    def _discovery_source_priority(self) -> List[str]:
        """Return discovery sources in configured priority order.

        Discovery pool writes only support Spotify, iTunes, and Deezer IDs, so
        we filter the broader metadata priority list down to those sources.
        """
        return [source for source in self._watchlist_source_priority() if source in {'spotify', 'itunes', 'deezer'}]

    @staticmethod
    def _artist_id_attribute_for_source(source: str) -> Optional[str]:
        """Return the watchlist artist attribute that stores the given source ID."""
        return {
            'spotify': 'spotify_artist_id',
            'itunes': 'itunes_artist_id',
            'deezer': 'deezer_artist_id',
            'discogs': 'discogs_artist_id',
        }.get(source)

    @staticmethod
    def _extract_entity_id(value: Any) -> Optional[str]:
        """Extract an ID from a dataclass, dict, or plain object."""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return value.get('id') or value.get('artist_id') or value.get('release_id')
        return getattr(value, 'id', None) or getattr(value, 'artist_id', None) or getattr(value, 'release_id', None)

    def _cache_watchlist_artist_source_id(self, watchlist_artist: WatchlistArtist, source: str, source_id: str) -> None:
        """Cache a resolved artist ID for a watchlist artist when we have a storage column."""
        if not source_id:
            return

        if source == 'spotify':
            self.database.update_watchlist_spotify_id(watchlist_artist.id, source_id)
            watchlist_artist.spotify_artist_id = source_id
        elif source == 'itunes':
            self.database.update_watchlist_itunes_id(watchlist_artist.id, source_id)
            watchlist_artist.itunes_artist_id = source_id
        elif source == 'deezer':
            self.database.update_watchlist_deezer_id(watchlist_artist.id, source_id)
            watchlist_artist.deezer_artist_id = source_id
        elif source == 'discogs':
            self.database.update_watchlist_discogs_id(watchlist_artist.id, source_id)
            watchlist_artist.discogs_artist_id = source_id

    def _resolve_watchlist_artist_source_id(self, watchlist_artist: WatchlistArtist, source: str, client: Any) -> Optional[str]:
        """Resolve the artist ID for an exact source, searching by name if needed."""
        attr = self._artist_id_attribute_for_source(source)
        stored_id = getattr(watchlist_artist, attr, None) if attr else None
        if stored_id:
            return stored_id

        if not client or not hasattr(client, 'search_artists'):
            return None

        try:
            search_kwargs = {'limit': 1}
            if source == 'spotify':
                search_kwargs['allow_fallback'] = False
            search_results = client.search_artists(watchlist_artist.artist_name, **search_kwargs)
        except Exception as e:
            logger.debug("Could not search %s for %s: %s", source, watchlist_artist.artist_name, e)
            return None

        if not search_results:
            return None

        found_id = self._extract_entity_id(search_results[0])
        if found_id and attr:
            self._cache_watchlist_artist_source_id(watchlist_artist, source, found_id)
        return found_id

    @staticmethod
    def _get_artist_image_from_data(artist_data: Any) -> Optional[str]:
        """Extract an image URL from artist payloads across providers."""
        if not artist_data:
            return None

        if isinstance(artist_data, dict):
            images = artist_data.get('images') or []
            if images:
                first_image = images[0]
                if isinstance(first_image, dict):
                    return first_image.get('url')
            return (
                artist_data.get('image_url')
                or artist_data.get('thumb_url')
                or artist_data.get('cover_image')
                or artist_data.get('picture_xl')
                or artist_data.get('picture_big')
                or artist_data.get('picture_medium')
            )

        images = getattr(artist_data, 'images', None)
        if images:
            first_image = images[0]
            if isinstance(first_image, dict):
                return first_image.get('url')
        return (
            getattr(artist_data, 'image_url', None)
            or getattr(artist_data, 'thumb_url', None)
            or getattr(artist_data, 'cover_image', None)
        )

    def _get_artist_image_for_source(self, watchlist_artist: WatchlistArtist, source: str, client: Any, artist_id: str) -> Optional[str]:
        """Fetch an artist image for a specific source."""
        if not client or not artist_id or not hasattr(client, 'get_artist'):
            return None

        try:
            if source == 'spotify':
                artist_data = client.get_artist(artist_id, allow_fallback=False)
            else:
                artist_data = client.get_artist(artist_id)
        except Exception as e:
            logger.debug("Could not fetch artist image for %s on %s: %s", watchlist_artist.artist_name, source, e)
            return None

        return self._get_artist_image_from_data(artist_data)

    def _get_album_data_for_source(self, source: str, album_id: str, album_name: str = '') -> Optional[Dict[str, Any]]:
        """Fetch album data for a specific source and normalize track payloads when needed."""
        client = get_client_for_source(source)
        if not client or not album_id or not hasattr(client, 'get_album'):
            return None

        try:
            if source == 'spotify':
                album_data = client.get_album(album_id, allow_fallback=False)
            else:
                album_data = client.get_album(album_id)
        except Exception as e:
            logger.debug("Could not fetch album %s on %s: %s", album_id, source, e)
            album_data = None

        if not album_data:
            return None

        # Some providers return album metadata without embedded tracks; normalize that shape.
        tracks = album_data.get('tracks') if isinstance(album_data, dict) else None
        if not tracks:
            track_items = get_album_tracks_for_source(source, album_id)
            if track_items:
                if not isinstance(album_data, dict):
                    try:
                        album_data = dict(album_data)
                    except Exception:
                        album_data = {'name': album_name or album_id}
                if isinstance(track_items, dict):
                    album_data['tracks'] = track_items
                else:
                    album_data['tracks'] = {'items': track_items}

        return album_data

    @staticmethod
    def _extract_track_items(album_data: Any) -> List[Dict[str, Any]]:
        """Normalize track payloads from different album formats to a list of items."""
        if not album_data:
            return []

        tracks = None
        if isinstance(album_data, dict):
            tracks = album_data.get('tracks')
        else:
            tracks = getattr(album_data, 'tracks', None)

        if not tracks:
            return []

        if isinstance(tracks, dict):
            items = tracks.get('items') or tracks.get('data') or []
            return list(items) if isinstance(items, list) else []

        if isinstance(tracks, list):
            return tracks

        return []

    def _resolve_watchlist_discography_for_source(
        self,
        watchlist_artist: WatchlistArtist,
        source: str,
        last_scan_timestamp: Optional[datetime] = None,
    ) -> Optional[WatchlistDiscographyResult]:
        """Resolve a watchlist artist to a specific source and fetch its discography."""
        client = get_client_for_source(source)
        if not client:
            return None

        artist_id = self._resolve_watchlist_artist_source_id(watchlist_artist, source, client)
        if not artist_id:
            return None

        albums = self._get_artist_discography_with_client(
            client,
            artist_id,
            last_scan_timestamp,
            lookback_days=watchlist_artist.lookback_days,
        )
        if not albums:
            return None

        image_url = self._get_artist_image_for_source(watchlist_artist, source, client, artist_id)
        return WatchlistDiscographyResult(
            source=source,
            artist_id=artist_id,
            albums=albums,
            image_url=image_url,
        )

    def get_artist_image_url(self, watchlist_artist: WatchlistArtist) -> Optional[str]:
        """
        Get artist image URL using the configured source priority.

        Returns:
            Image URL string or None if not available
        """
        for source in self._watchlist_source_priority():
            client = get_client_for_source(source)
            if not client:
                continue
            artist_id = self._resolve_watchlist_artist_source_id(watchlist_artist, source, client)
            if not artist_id:
                continue
            image_url = self._get_artist_image_for_source(watchlist_artist, source, client, artist_id)
            if image_url:
                return image_url
        return None

    def _get_artist_albums_for_source(
        self,
        source: str,
        artist_id: str,
        album_type: str = 'album,single,ep',
        limit: int = 50,
        skip_cache: bool = True,
        max_pages: int = 0,
    ) -> List[Any]:
        """Fetch artist albums for a specific source, keeping Spotify strict."""
        client = get_client_for_source(source)
        if not client or not artist_id or not hasattr(client, 'get_artist_albums'):
            return []

        try:
            kwargs = {
                'album_type': album_type,
                'limit': limit,
            }
            if source == 'spotify':
                kwargs['skip_cache'] = skip_cache
                kwargs['max_pages'] = max_pages
                kwargs['allow_fallback'] = False
            return client.get_artist_albums(artist_id, **kwargs) or []
        except Exception as e:
            logger.debug("Could not fetch artist albums for %s on %s: %s", artist_id, source, e)
            return []

    def _get_artist_data_for_source(self, source: str, artist_id: str) -> Optional[Dict[str, Any]]:
        """Fetch artist metadata for a specific source, keeping Spotify strict."""
        client = get_client_for_source(source)
        if not client or not artist_id or not hasattr(client, 'get_artist'):
            return None

        try:
            if source == 'spotify':
                return client.get_artist(artist_id, allow_fallback=False)
            return client.get_artist(artist_id)
        except Exception as e:
            logger.debug("Could not fetch artist data for %s on %s: %s", artist_id, source, e)
            return None

    def _search_albums_for_source(self, source: str, query: str, limit: int = 1):
        """Search albums for a specific source, keeping Spotify strict."""
        client = get_client_for_source(source)
        if not client or not hasattr(client, 'search_albums'):
            return []

        try:
            if source == 'spotify':
                return client.search_albums(query, limit=limit, allow_fallback=False) or []
            return client.search_albums(query, limit=limit) or []
        except Exception as e:
            logger.debug("Could not search albums for %s on %s: %s", query, source, e)
            return []

    def _resolve_artist_id_for_source(
        self,
        source: str,
        artist_name: str,
        stored_id: Optional[str] = None,
        cache_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """Resolve an artist ID for a specific source, searching by name if needed."""
        if stored_id:
            return stored_id

        client = get_client_for_source(source)
        if not client or not hasattr(client, 'search_artists'):
            return None

        try:
            search_kwargs = {'limit': 1}
            if source == 'spotify':
                search_kwargs['allow_fallback'] = False
            results = client.search_artists(artist_name, **search_kwargs)
        except Exception as e:
            logger.debug("Could not resolve %s artist ID for %s: %s", source, artist_name, e)
            return None

        if not results:
            return None

        found_id = self._extract_entity_id(results[0])
        if found_id and cache_callback:
            try:
                cache_callback(found_id)
            except Exception as e:
                logger.debug("Could not cache %s artist ID for %s: %s", source, artist_name, e)
        return found_id

    def backfill_watchlist_artist_images(self, profile_id: int) -> int:
        """Backfill missing watchlist artist images using cached metadata and existing album art."""
        try:
            conn = self.database._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, artist_name, spotify_artist_id, itunes_artist_id,
                       deezer_artist_id, discogs_artist_id
                FROM watchlist_artists
                WHERE profile_id = ? AND (image_url IS NULL OR image_url = '' OR image_url = 'None'
                      OR image_url NOT LIKE 'http%')
            """, (profile_id,))
            imageless = cursor.fetchall()

            if not imageless:
                return 0

            logger.info("Backfilling images for %s watchlist artists (profile %s)...", len(imageless), profile_id)
            filled = 0
            for row in imageless:
                name = row['artist_name']
                img = None

                # 1. Check metadata cache for artist image
                cursor.execute("""
                    SELECT image_url FROM metadata_cache_entities
                    WHERE entity_type = 'artist' AND name = ? COLLATE NOCASE
                      AND image_url IS NOT NULL AND image_url LIKE 'http%'
                    LIMIT 1
                """, (name,))
                cr = cursor.fetchone()
                if cr:
                    img = cr['image_url']

                # 2. Deezer direct URL (no API call needed)
                if not img and row['deezer_artist_id']:
                    img = f"https://api.deezer.com/artist/{row['deezer_artist_id']}/image?size=big"

                # 3. Deezer ID from cache (artist may have a Deezer match we haven't stored on watchlist)
                if not img:
                    cursor.execute("""
                        SELECT entity_id FROM metadata_cache_entities
                        WHERE entity_type = 'artist' AND source = 'deezer'
                          AND name = ? COLLATE NOCASE LIMIT 1
                    """, (name,))
                    dz = cursor.fetchone()
                    if dz and dz['entity_id']:
                        img = f"https://api.deezer.com/artist/{dz['entity_id']}/image?size=big"

                # 4. Album art fallback (iTunes artists have no artist images)
                if not img:
                    cursor.execute("""
                        SELECT image_url FROM metadata_cache_entities
                        WHERE entity_type = 'album' AND image_url LIKE 'http%'
                          AND artist_name = ? COLLATE NOCASE LIMIT 1
                    """, (name,))
                    alb = cursor.fetchone()
                    if alb:
                        img = alb['image_url']

                if img:
                    aid = (row['spotify_artist_id'] or row['itunes_artist_id']
                           or row['deezer_artist_id'] or row['discogs_artist_id'])
                    if aid:
                        self.database.update_watchlist_artist_image(aid, img)
                    else:
                        # No external IDs — update by internal row ID directly
                        cursor.execute("""
                            UPDATE watchlist_artists SET image_url = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (img, row['id']))
                        conn.commit()
                    filled += 1

            if filled:
                logger.info("Backfilled %s/%s watchlist artist images (profile %s)", filled, len(imageless), profile_id)
            return filled
        except Exception as e:
            logger.debug("Error backfilling watchlist artist images for profile %s: %s", profile_id, e, exc_info=True)
            return 0

    def get_artist_discography_for_watchlist(self, watchlist_artist: WatchlistArtist, last_scan_timestamp: Optional[datetime] = None) -> Optional[WatchlistDiscographyResult]:
        """
        Get artist's discography using the configured source priority, with proper ID resolution.
        Returns the first provider that can actually return albums.

        Args:
            watchlist_artist: WatchlistArtist object (has both spotify and itunes IDs)
            last_scan_timestamp: Only return releases after this date (for incremental scans)

        Returns:
            WatchlistDiscographyResult or None on error
        """
        for source in self._watchlist_source_priority():
            result = self._resolve_watchlist_discography_for_source(watchlist_artist, source, last_scan_timestamp)
            if result:
                return result

        logger.warning(f"No valid client/ID for {watchlist_artist.artist_name}")
        return None

    def _apply_global_watchlist_overrides(self, watchlist_artists: List[WatchlistArtist]):
        """Apply global watchlist release-type overrides to a batch of artists."""
        try:
            from config.settings import config_manager
        except Exception:
            return

        if not config_manager.get('watchlist.global_override_enabled', False):
            return

        g_albums = config_manager.get('watchlist.global_include_albums', True)
        g_eps = config_manager.get('watchlist.global_include_eps', True)
        g_singles = config_manager.get('watchlist.global_include_singles', True)
        g_live = config_manager.get('watchlist.global_include_live', False)
        g_remixes = config_manager.get('watchlist.global_include_remixes', False)
        g_acoustic = config_manager.get('watchlist.global_include_acoustic', False)
        g_compilations = config_manager.get('watchlist.global_include_compilations', False)
        g_instrumentals = config_manager.get('watchlist.global_include_instrumentals', False)

        logger.info(
            "Applying global watchlist override to %s artists "
            "(albums=%s, eps=%s, singles=%s, live=%s, remixes=%s, acoustic=%s, compilations=%s, instrumentals=%s)",
            len(watchlist_artists),
            g_albums,
            g_eps,
            g_singles,
            g_live,
            g_remixes,
            g_acoustic,
            g_compilations,
            g_instrumentals,
        )

        for artist in watchlist_artists:
            artist.include_albums = g_albums
            artist.include_eps = g_eps
            artist.include_singles = g_singles
            artist.include_live = g_live
            artist.include_remixes = g_remixes
            artist.include_acoustic = g_acoustic
            artist.include_compilations = g_compilations
            artist.include_instrumentals = g_instrumentals

    def scan_watchlist_profile(
        self,
        profile_id: int,
        watchlist_artists: Optional[List[WatchlistArtist]] = None,
        *,
        scan_state: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        artist_index_offset: int = 0,
        total_artists_override: Optional[int] = None,
        apply_global_overrides: bool = True,
    ) -> List[ScanResult]:
        """Scan a single watchlist profile using the shared watchlist scan engine."""
        if watchlist_artists is None:
            watchlist_artists = self.database.get_watchlist_artists(profile_id=profile_id)

        if apply_global_overrides:
            self._apply_global_watchlist_overrides(watchlist_artists)

        return self.scan_watchlist_artists(
            watchlist_artists,
            profile_id=profile_id,
            scan_state=scan_state,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            artist_index_offset=artist_index_offset,
            total_artists_override=total_artists_override,
        )

    def scan_watchlist_artists(
        self,
        watchlist_artists: List[WatchlistArtist],
        *,
        profile_id: int = 1,
        scan_state: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        artist_index_offset: int = 0,
        total_artists_override: Optional[int] = None,
    ) -> List[ScanResult]:
        """Scan a list of watchlist artists using the shared web watchlist scan flow."""
        scan_results: List[ScanResult] = []
        if not watchlist_artists:
            if scan_state is not None:
                scan_state.update({
                    'status': 'completed',
                    'total_artists': 0,
                    'current_artist_index': 0,
                    'current_artist_name': '',
                    'current_artist_image_url': '',
                    'current_phase': 'completed',
                    'albums_to_check': 0,
                    'albums_checked': 0,
                    'current_album': '',
                    'current_album_image_url': '',
                    'current_track_name': '',
                    'tracks_found_this_scan': 0,
                    'tracks_added_this_scan': 0,
                    'recent_wishlist_additions': [],
                    'results': [],
                    'summary': {
                        'total_artists': 0,
                        'successful_scans': 0,
                        'new_tracks_found': 0,
                        'tracks_added_to_wishlist': 0,
                    },
                    'completed_at': datetime.now(),
                    'error': None,
                })
            return scan_results

        if scan_state is not None:
            scan_state.update({
                'status': 'scanning',
                'started_at': scan_state.get('started_at') or datetime.now(),
                'total_artists': total_artists_override if total_artists_override is not None else len(watchlist_artists),
                'current_artist_index': scan_state.get('current_artist_index', artist_index_offset),
                'current_artist_name': scan_state.get('current_artist_name', ''),
                'current_artist_image_url': scan_state.get('current_artist_image_url', ''),
                'current_phase': 'starting',
                'albums_to_check': 0,
                'albums_checked': 0,
                'current_album': '',
                'current_album_image_url': '',
                'current_track_name': '',
                'tracks_found_this_scan': scan_state.get('tracks_found_this_scan', 0),
                'tracks_added_this_scan': scan_state.get('tracks_added_this_scan', 0),
                'recent_wishlist_additions': scan_state.get('recent_wishlist_additions', []),
                'results': scan_state.get('results', []),
                'summary': scan_state.get('summary', {}),
                'error': None,
            })

        def _emit(event_type: str, **payload):
            if progress_callback:
                try:
                    progress_callback(event_type, payload)
                except Exception:
                    logger.debug("Watchlist scan progress callback failed for %s", event_type, exc_info=True)

        _emit('scan_started', profile_id=profile_id, total_artists=len(watchlist_artists))

        # Keep this as a plain source list; resolve the client right before each use.
        providers_to_backfill = [
            source for source in self._watchlist_source_priority()
            if source in {'spotify', 'itunes', 'deezer', 'discogs'}
        ]

        for provider in providers_to_backfill:
            try:
                logger.info("Checking for missing %s IDs in watchlist...", provider)
                self._backfill_missing_ids(watchlist_artists, provider)
            except Exception as backfill_error:
                logger.warning("Error during %s ID backfilling: %s", provider, backfill_error)

        lookback_period = self._get_lookback_period_setting()
        is_full_discography = (lookback_period == 'all')
        artist_count = len(watchlist_artists)

        base_artist_delay = DELAY_BETWEEN_ARTISTS
        base_album_delay = DELAY_BETWEEN_ALBUMS
        if is_full_discography:
            base_artist_delay *= 2.0
            base_album_delay *= 2.0
        if artist_count > 200:
            base_artist_delay *= 1.5
            base_album_delay *= 1.25
        elif artist_count > 100:
            base_artist_delay *= 1.25

        artist_delay = base_artist_delay
        album_delay = base_album_delay
        logger.info(
            "Scan parameters: %s artists, lookback=%s, delays: %.1fs/artist, %.1fs/album",
            artist_count,
            lookback_period,
            artist_delay,
            album_delay,
        )

        for i, artist in enumerate(watchlist_artists):
            if cancel_check and cancel_check():
                logger.info("Watchlist scan cancelled after %s/%s artists", i, len(watchlist_artists))
                if scan_state is not None:
                    successful_scans = [r for r in scan_results if r.success]
                    scan_state['status'] = 'cancelled'
                    scan_state['current_phase'] = 'cancelled'
                    scan_state['summary'] = {
                        'total_artists': i,
                        'successful_scans': len(successful_scans),
                        'new_tracks_found': sum(r.new_tracks_found for r in successful_scans),
                        'tracks_added_to_wishlist': sum(r.tracks_added_to_wishlist for r in successful_scans),
                        'cancelled': True,
                    }
                _emit('cancelled', processed=i, total=len(watchlist_artists))
                break

            source_artist_id = (
                artist.spotify_artist_id
                or artist.itunes_artist_id
                or artist.deezer_artist_id
                or artist.discogs_artist_id
                or str(artist.id)
            )

            try:
                discography_result = self.get_artist_discography_for_watchlist(artist, artist.last_scan_timestamp)
                if discography_result is None:
                    scan_results.append(ScanResult(
                        artist_name=artist.artist_name,
                        spotify_artist_id=source_artist_id,
                        albums_checked=0,
                        new_tracks_found=0,
                        tracks_added_to_wishlist=0,
                        success=False,
                        error_message="Failed to get artist discography",
                    ))
                    _emit(
                        'artist_error',
                        artist_name=artist.artist_name,
                        profile_id=profile_id,
                        error_message="Failed to get artist discography",
                    )
                    continue

                if isinstance(discography_result, list):
                    albums = discography_result
                    artist_image_url = self.get_artist_image_url(artist) or ''
                    album_fetcher = lambda album_id, album_name='': self.metadata_service.get_album(album_id)
                else:
                    source = discography_result.source
                    albums = discography_result.albums
                    source_artist_id = discography_result.artist_id
                    artist_image_url = discography_result.image_url or self.get_artist_image_url(artist) or ''
                    album_fetcher = lambda album_id, album_name='': self._get_album_data_for_source(source, album_id, album_name)

                absolute_index = artist_index_offset + i + 1
                if scan_state is not None:
                    scan_state.update({
                        'current_artist_index': absolute_index,
                        'current_artist_name': artist.artist_name,
                        'current_artist_image_url': artist_image_url,
                        'current_phase': 'fetching_discography',
                        'albums_to_check': 0,
                        'albums_checked': 0,
                        'current_album': '',
                        'current_album_image_url': '',
                        'current_track_name': '',
                    })

                _emit(
                    'artist_started',
                    artist_name=artist.artist_name,
                    artist_index=absolute_index,
                    total_artists=total_artists_override if total_artists_override is not None else len(watchlist_artists),
                    profile_id=profile_id,
                    artist_image_url=artist_image_url,
                )

                if scan_state is not None:
                    scan_state.update({
                        'current_phase': 'checking_albums',
                        'albums_to_check': len(albums),
                        'albums_checked': 0,
                    })

                artist_new_tracks = 0
                artist_added_tracks = 0

                for album_index, album in enumerate(albums):
                    try:
                        album_data = album_fetcher(album.id, getattr(album, 'name', ''))
                        tracks = self._extract_track_items(album_data)
                        if not album_data or not tracks:
                            logger.debug("Skipping album %s (id=%s): no track data returned", album.name, album.id)
                            continue

                        album_name = getattr(album, 'name', '')
                        if isinstance(album_data, dict):
                            album_name = album_data.get('name', album_name)
                        else:
                            album_name = getattr(album_data, 'name', album_name)

                        if self._has_placeholder_tracks(tracks):
                            logger.info("Skipping album with placeholder tracks: %s", album_name)
                            continue
                        if not self._should_include_release(len(tracks), artist):
                            continue

                        album_image_url = ''
                        album_images = []
                        if isinstance(album_data, dict):
                            album_images = album_data.get('images') or []
                        else:
                            album_images = getattr(album_data, 'images', None) or []
                        if album_images:
                            first_image = album_images[0]
                            if isinstance(first_image, dict):
                                album_image_url = first_image.get('url', '')

                        if scan_state is not None:
                            scan_state.update({
                                'albums_checked': album_index + 1,
                                'current_album': album_name,
                                'current_album_image_url': album_image_url,
                                'current_phase': f'checking_album_{album_index + 1}_of_{len(albums)}',
                            })

                        _emit(
                            'album_started',
                            artist_name=artist.artist_name,
                            album_name=album_name,
                            album_index=album_index + 1,
                            total_albums=len(albums),
                            album_image_url=album_image_url,
                        )

                        for track in tracks:
                            if not self._should_include_track(track, album_data, artist):
                                continue

                            track_name = track.get('name', 'Unknown Track')
                            if scan_state is not None:
                                scan_state['current_track_name'] = track_name

                            if self.is_track_missing_from_library(track, album_name=album_name):
                                artist_new_tracks += 1
                                if scan_state is not None:
                                    scan_state['tracks_found_this_scan'] += 1

                                if self.add_track_to_wishlist(track, album_data, artist):
                                    artist_added_tracks += 1
                                    if scan_state is not None:
                                        scan_state['tracks_added_this_scan'] += 1

                                    track_artists = track.get('artists', [])
                                    track_artist_name = track_artists[0].get('name', 'Unknown Artist') if track_artists else 'Unknown Artist'
                                    if scan_state is not None:
                                        scan_state['recent_wishlist_additions'].insert(0, {
                                            'track_name': track_name,
                                            'artist_name': track_artist_name,
                                            'album_image_url': album_image_url,
                                        })
                                        if len(scan_state['recent_wishlist_additions']) > 10:
                                            scan_state['recent_wishlist_additions'].pop()

                        if album_index < len(albums) - 1:
                            time.sleep(album_delay)

                    except Exception as e:
                        logger.warning("Error checking album %s: %s", album.name, e)
                        continue

                self.update_artist_scan_timestamp(artist)

                scan_results.append(ScanResult(
                    artist_name=artist.artist_name,
                    spotify_artist_id=source_artist_id or artist.spotify_artist_id or '',
                    albums_checked=len(albums),
                    new_tracks_found=artist_new_tracks,
                    tracks_added_to_wishlist=artist_added_tracks,
                    success=True,
                ))

                _emit(
                    'artist_completed',
                    artist_name=artist.artist_name,
                    artist_index=absolute_index,
                    total_artists=total_artists_override if total_artists_override is not None else len(watchlist_artists),
                    profile_id=profile_id,
                    albums_checked=len(albums),
                    new_tracks_found=artist_new_tracks,
                    tracks_added_to_wishlist=artist_added_tracks,
                )

                try:
                    if scan_state is not None:
                        scan_state['current_phase'] = 'fetching_similar_artists'
                    artist_profile_id = getattr(artist, 'profile_id', profile_id)
                    spotify_authenticated = self.spotify_client and self.spotify_client.is_spotify_authenticated()
                    if self.database.has_fresh_similar_artists(source_artist_id, days_threshold=30, require_spotify=spotify_authenticated, profile_id=artist_profile_id):
                        logger.info("Similar artists for %s are cached and fresh (profile %s)", artist.artist_name, artist_profile_id)
                        self._backfill_similar_artists_itunes_ids(source_artist_id, profile_id=artist_profile_id)
                    else:
                        logger.info("Fetching similar artists for %s (profile %s)...", artist.artist_name, artist_profile_id)
                        self.update_similar_artists(artist, profile_id=artist_profile_id, source_artist_id=source_artist_id)
                        logger.info("Similar artists updated for %s", artist.artist_name)
                except Exception as similar_error:
                    logger.warning("Failed to update similar artists for %s: %s", artist.artist_name, similar_error)

                if i < len(watchlist_artists) - 1:
                    if scan_state is not None:
                        scan_state['current_phase'] = 'rate_limiting'
                    time.sleep(artist_delay)

            except Exception as e:
                logger.error("Error scanning artist %s: %s", artist.artist_name, e)
                scan_results.append(ScanResult(
                    artist_name=artist.artist_name,
                    spotify_artist_id=source_artist_id,
                    albums_checked=0,
                    new_tracks_found=0,
                    tracks_added_to_wishlist=0,
                    success=False,
                    error_message=str(e),
                ))
                _emit(
                    'artist_error',
                    artist_name=artist.artist_name,
                    artist_index=artist_index_offset + i + 1,
                    total_artists=total_artists_override if total_artists_override is not None else len(watchlist_artists),
                    profile_id=profile_id,
                    error_message=str(e),
                )

        if scan_state is not None:
            successful_scans = [r for r in scan_results if r.success]
            total_new_tracks = sum(r.new_tracks_found for r in successful_scans)
            total_added_to_wishlist = sum(r.tracks_added_to_wishlist for r in successful_scans)
            scan_state['results'] = list(scan_state.get('results', [])) + scan_results
            if scan_state.get('status') != 'cancelled':
                scan_state['status'] = 'completed'
                scan_state['completed_at'] = datetime.now()
                scan_state['current_phase'] = 'completed'
                scan_state['summary'] = {
                    'total_artists': len(scan_results),
                    'successful_scans': len(successful_scans),
                    'new_tracks_found': total_new_tracks,
                    'tracks_added_to_wishlist': total_added_to_wishlist,
                }

        _emit(
            'scan_completed',
            profile_id=profile_id,
            total_artists=len(watchlist_artists),
            total_scanned=len(scan_results),
            successful_scans=len([r for r in scan_results if r.success]),
            new_tracks_found=sum(r.new_tracks_found for r in scan_results if r.success),
            tracks_added_to_wishlist=sum(r.tracks_added_to_wishlist for r in scan_results if r.success),
        )
        return scan_results
    
    def get_artist_discography(
        self,
        spotify_artist_id: str,
        last_scan_timestamp: Optional[datetime] = None,
        lookback_days: Optional[int] = None,
    ) -> Optional[List]:
        """
        Get artist's discography from Spotify, optionally filtered by release date.

        Args:
            spotify_artist_id: Spotify artist ID
            last_scan_timestamp: Only return releases after this date (for incremental scans)
                                If None, uses lookback period setting from database
            lookback_days: Optional per-artist override for lookback period
        """
        try:
            return self._get_artist_discography_with_client(
                self.spotify_client,
                spotify_artist_id,
                last_scan_timestamp,
                lookback_days=lookback_days,
            )

        except Exception as e:
            logger.error(f"Error getting discography for artist {spotify_artist_id}: {e}")
            return None

    def _get_artist_discography_with_client(self, client, artist_id: str, last_scan_timestamp: Optional[datetime] = None, lookback_days: Optional[int] = None) -> Optional[List]:
        """
        Get artist's discography using the specified client, optionally filtered by release date.

        Args:
            client: The metadata client to use (spotify or itunes)
            artist_id: Artist ID for the given client
            last_scan_timestamp: Only return releases after this date (for incremental scans)
                                If None, uses lookback period setting from database
            lookback_days: Per-artist override for lookback period (None = use global setting)
        """
        try:
            # Determine if we need full discography or just recent releases BEFORE fetching.
            # Spotify returns albums newest-first, so for time-bounded scans we only need
            # the first page (50 albums) — cuts API calls by ~90% for prolific artists.
            lookback_period = self._get_lookback_period_setting()
            needs_full_discog = False

            if lookback_period == 'all':
                cutoff_timestamp = None
                needs_full_discog = True
            elif last_scan_timestamp is not None:
                cutoff_timestamp = last_scan_timestamp

                # Check if a lookback period change requires a one-time wider window
                rescan_cutoff = self._get_rescan_cutoff()
                if rescan_cutoff == 'all':
                    if self._rescan_cutoff_log_marker != 'all':
                        logger.info(f"Lookback period changed to 'all' — returning full discography")
                        self._rescan_cutoff_log_marker = 'all'
                    cutoff_timestamp = None
                    needs_full_discog = True
                elif rescan_cutoff is not None:
                    scan_ts = cutoff_timestamp
                    if scan_ts.tzinfo is None:
                        scan_ts = scan_ts.replace(tzinfo=timezone.utc)
                    if rescan_cutoff.tzinfo is None:
                        rescan_cutoff = rescan_cutoff.replace(tzinfo=timezone.utc)
                    if rescan_cutoff < scan_ts:
                        marker = rescan_cutoff.isoformat()
                        if self._rescan_cutoff_log_marker != marker:
                            logger.info(f"Lookback period change detected — expanding cutoff from {cutoff_timestamp} to {rescan_cutoff}")
                            self._rescan_cutoff_log_marker = marker
                        cutoff_timestamp = rescan_cutoff
            else:
                # No scan timestamp — first scan, use lookback period
                if lookback_days is not None:
                    days = lookback_days
                else:
                    days = int(lookback_period)
                cutoff_timestamp = datetime.now(timezone.utc) - timedelta(days=days)
                logger.info(f"Using lookback period: {days} days (cutoff: {cutoff_timestamp})")

            # Fetch albums — limit pagination unless full discography is needed
            logger.debug(f"Fetching discography for artist {artist_id}" +
                         (" (full)" if needs_full_discog else " (recent only, max 1 page)"))
            _skip = {'skip_cache': True} if hasattr(client, 'sp') else {}
            _max_pages = 0 if needs_full_discog else 1
            # Only pass max_pages to clients that support it (spotify_client)
            if hasattr(client, 'sp'):
                _skip['max_pages'] = _max_pages
            albums = client.get_artist_albums(artist_id, album_type='album,single', limit=50, **_skip)

            if not albums:
                logger.warning(f"No albums found for artist {artist_id}")
                return []

            # Add small delay after fetching artist discography to be extra safe
            time.sleep(0.3)  # 300ms breathing room

            # Filter by release date if we have a cutoff timestamp
            if cutoff_timestamp:
                filtered_albums = []
                for album in albums:
                    if self.is_album_after_timestamp(album, cutoff_timestamp):
                        filtered_albums.append(album)

                logger.info(f"Filtered {len(albums)} albums to {len(filtered_albums)} released after {cutoff_timestamp}")
                albums = filtered_albums

            # Skip future/unreleased albums — no real audio available yet
            now = datetime.now(timezone.utc)
            released = [a for a in albums if not self._is_future_release(a, now)]
            skipped = len(albums) - len(released)
            if skipped:
                logger.info(f"Skipped {skipped} future/unreleased albums (will be picked up after release)")
            return released

        except Exception as e:
            logger.error(f"Error getting discography for artist {artist_id}: {e}")
            return None

    def _backfill_missing_ids(self, artists: List[WatchlistArtist], provider: str):
        """
        Proactively match ALL artists missing IDs for the current provider.
        
        Example: User has 50 artists with only Spotify IDs.
        When iTunes becomes active, this matches ALL 50 to iTunes in one batch.
        """
        # Find artists missing IDs for the active provider (regardless of which other IDs they have)
        id_attr = {
            'spotify': 'spotify_artist_id',
            'itunes': 'itunes_artist_id',
            'deezer': 'deezer_artist_id',
            'discogs': 'discogs_artist_id',
        }.get(provider)

        if not id_attr:
            logger.debug(f"Backfill not supported for provider: {provider}")
            return

        artists_to_match = [a for a in artists if not getattr(a, id_attr, None)]

        if not artists_to_match:
            logger.info(f"All artists already have {provider} IDs")
            return

        logger.info(f"Backfilling {len(artists_to_match)} artists with {provider} IDs...")

        match_fn = {
            'spotify': self._match_to_spotify,
            'itunes': self._match_to_itunes,
            'deezer': self._match_to_deezer,
            'discogs': self._match_to_discogs,
        }.get(provider)

        update_fn = {
            'spotify': self.database.update_watchlist_spotify_id,
            'itunes': self.database.update_watchlist_itunes_id,
            'deezer': self.database.update_watchlist_deezer_id,
            'discogs': self.database.update_watchlist_discogs_id,
        }.get(provider)

        if not match_fn or not update_fn:
            logger.debug(f"No match/update function available for provider: {provider}")
            return

        matched_count = 0
        unmatched_names = []
        for artist in artists_to_match:
            try:
                new_id = match_fn(artist.artist_name)
                if new_id:
                    update_fn(artist.id, new_id)
                    setattr(artist, id_attr, new_id)
                    matched_count += 1
                    logger.info(f"Matched '{artist.artist_name}' to {provider}: {new_id}")
                else:
                    unmatched_names.append(artist.artist_name)

                time.sleep(0.3)

            except Exception as e:
                logger.warning(f"Could not match '{artist.artist_name}' to {provider}: {e}")
                unmatched_names.append(artist.artist_name)
                continue

        logger.info(f"Backfilled {matched_count}/{len(artists_to_match)} artists with {provider} IDs")
        if unmatched_names:
            logger.warning(f"Could not confidently match {len(unmatched_names)} artists: {', '.join(unmatched_names[:10])}"
                          f"{'...' if len(unmatched_names) > 10 else ''} — use Watchlist Settings to link manually")

    @staticmethod
    def _normalize_artist_name(name: str) -> str:
        """Normalize artist name for comparison."""
        if not name:
            return ""
        s = name.lower().strip()
        # Remove "the " prefix
        s = re.sub(r'^the\s+', '', s)
        # Remove non-alphanumeric except spaces
        s = re.sub(r'[^\w\s]', '', s)
        # Collapse whitespace
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    @staticmethod
    def _artist_name_similarity(name_a: str, name_b: str) -> float:
        """Calculate similarity between two artist names (0.0-1.0)."""
        from difflib import SequenceMatcher
        na = WatchlistScanner._normalize_artist_name(name_a)
        nb = WatchlistScanner._normalize_artist_name(name_b)
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        return SequenceMatcher(None, na, nb).ratio()

    def _best_artist_match(self, results, artist_name: str) -> Optional[str]:
        """Pick the best matching artist from search results using name similarity.

        Returns the artist ID only if we're confident it's the right match.
        """
        if not results:
            return None

        # Exact normalized match gets immediate acceptance
        for r in results:
            if self._normalize_artist_name(r.name) == self._normalize_artist_name(artist_name):
                logger.info(f"  Exact match: '{r.name}' (id={r.id})")
                return r.id

        # Score all results by name similarity + popularity bonus
        candidates = []
        for r in results:
            sim = self._artist_name_similarity(artist_name, r.name)
            # Small popularity bonus (max 0.05) to break ties between similar names
            pop_bonus = (getattr(r, 'popularity', 0) / 100) * 0.05
            score = sim + pop_bonus
            candidates.append((r, sim, score))
            logger.debug(f"  Candidate: '{r.name}' sim={sim:.2f} pop={getattr(r, 'popularity', 0)} score={score:.3f}")

        # Sort by score descending
        candidates.sort(key=lambda x: x[2], reverse=True)
        best, best_sim, best_score = candidates[0]

        # Require high similarity to accept (0.85 threshold)
        if best_sim >= 0.85:
            logger.info(f"  Best match: '{best.name}' (sim={best_sim:.2f}, id={best.id})")
            return best.id

        # Between 0.70-0.85: accept only if it's clearly better than runner-up
        if best_sim >= 0.70 and len(candidates) > 1:
            runner_up_sim = candidates[1][1]
            if best_sim - runner_up_sim >= 0.15:
                logger.info(f"  Best match (clear winner): '{best.name}' (sim={best_sim:.2f}, id={best.id})")
                return best.id

        logger.warning(f"  No confident match for '{artist_name}' — best was '{best.name}' (sim={best_sim:.2f})")
        return None

    def _match_to_spotify(self, artist_name: str) -> Optional[str]:
        """Match artist name to Spotify ID using fuzzy name comparison."""
        try:
            client = get_client_for_source('spotify')
            if not client:
                return None

            results = client.search_artists(artist_name, limit=5, allow_fallback=False)

            return self._best_artist_match(results, artist_name)
        except Exception as e:
            logger.warning(f"Could not match {artist_name} to Spotify: {e}")
        return None

    def _match_to_itunes(self, artist_name: str) -> Optional[str]:
        """Match artist name to iTunes ID using fuzzy name comparison."""
        try:
            if hasattr(self, '_metadata_service') and self._metadata_service:
                results = self._metadata_service.itunes.search_artists(artist_name, limit=5)
            else:
                logger.warning(f"Cannot match to iTunes - MetadataService not available")
                return None

            return self._best_artist_match(results, artist_name)
        except Exception as e:
            logger.warning(f"Could not match {artist_name} to iTunes: {e}")
        return None

    def _match_to_deezer(self, artist_name: str) -> Optional[str]:
        """Match artist name to Deezer ID using fuzzy name comparison."""
        try:
            # Try MetadataService fallback client (if it's Deezer)
            if hasattr(self, '_metadata_service') and self._metadata_service:
                client = self._metadata_service.itunes  # Named 'itunes' but may be DeezerClient
                from core.deezer_client import DeezerClient
                if isinstance(client, DeezerClient):
                    results = client.search_artists(artist_name, limit=5)
                    return self._best_artist_match(results, artist_name)

            # Fallback: use cached Deezer client
            from core.metadata_service import get_deezer_client
            client = get_deezer_client()
            results = client.search_artists(artist_name, limit=5)
            return self._best_artist_match(results, artist_name)
        except Exception as e:
            logger.warning(f"Could not match {artist_name} to Deezer: {e}")
        return None

    def _match_to_discogs(self, artist_name: str) -> Optional[str]:
        """Match artist name to Discogs ID using fuzzy name comparison."""
        try:
            from core.metadata_service import get_discogs_client
            client = get_discogs_client()
            results = client.search_artists(artist_name, limit=5)
            return self._best_artist_match(results, artist_name)
        except Exception as e:
            logger.warning(f"Could not match {artist_name} to Discogs: {e}")
        return None

    def _get_lookback_period_setting(self) -> str:
        """
        Get the discovery lookback period setting from database.

        Returns:
            str: Period value ('7', '30', '90', '180', or 'all')
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM metadata WHERE key = 'discovery_lookback_period'")
                row = cursor.fetchone()

                if row:
                    return row['value']
                else:
                    # Default to 30 days if not set
                    return '30'

        except Exception as e:
            logger.warning(f"Error getting lookback period setting, defaulting to 30 days: {e}")
            return '30'

    def _get_rescan_cutoff(self):
        """
        Check if a lookback period change requires a one-time wider scan window.

        When the lookback period is expanded, a 'watchlist_rescan_cutoff' metadata key
        is set with the new cutoff date. This method returns that cutoff so the scanner
        can use the wider window for artists scanned before the change. After a full
        scan cycle, the key is cleared by _clear_rescan_cutoff().

        Returns:
            datetime cutoff if a rescan is pending with a specific date,
            'all' string if lookback was set to entire discography,
            None if no rescan is pending
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM metadata WHERE key = 'watchlist_rescan_cutoff'")
                row = cursor.fetchone()
                if row is not None:
                    val = row['value']
                    if val == '':
                        return 'all'  # Lookback set to 'all' — scan everything
                    return datetime.fromisoformat(val)
        except Exception as e:
            logger.debug(f"Error reading rescan cutoff: {e}")
        return None

    def _clear_rescan_cutoff(self):
        """Clear the one-time rescan cutoff after a full scan cycle completes."""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM metadata WHERE key = 'watchlist_rescan_cutoff'")
                conn.commit()
                logger.info("Cleared watchlist rescan cutoff flag")
                self._rescan_cutoff_log_marker = None
        except Exception as e:
            logger.debug(f"Error clearing rescan cutoff: {e}")

    def is_album_after_timestamp(self, album, timestamp: datetime) -> bool:
        """Check if album was released after the given timestamp"""
        try:
            if not album.release_date:
                return True  # Include albums with unknown release dates to be safe
            
            # Parse release date - Spotify provides different precisions
            release_date_str = album.release_date
            
            # Handle different date formats
            if len(release_date_str) == 4:  # Year only (e.g., "2023")
                album_date = datetime(int(release_date_str), 1, 1, tzinfo=timezone.utc)
            elif len(release_date_str) == 7:  # Year-month (e.g., "2023-10")
                year, month = release_date_str.split('-')
                album_date = datetime(int(year), int(month), 1, tzinfo=timezone.utc)
            elif len(release_date_str) == 10:  # Full date (e.g., "2023-10-15")
                album_date = datetime.strptime(release_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            elif 'T' in release_date_str:  # ISO 8601 with time (e.g., "2017-12-08T08:00:00Z" from iTunes)
                # Strip the time portion and parse just the date
                date_part = release_date_str.split('T')[0]
                album_date = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            else:
                logger.warning(f"Unknown release date format: {release_date_str}")
                return True  # Include if we can't parse
            
            # Ensure timestamp has timezone info
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            
            return album_date > timestamp

        except Exception as e:
            logger.warning(f"Error comparing album date {album.release_date} with timestamp {timestamp}: {e}")
            return True  # Include if we can't determine

    def _is_future_release(self, album, now: datetime) -> bool:
        """Check if an album's release date is in the future. Returns False for unknown dates (safe default)."""
        try:
            if not album.release_date:
                return False  # Unknown date — assume released
            release_date_str = album.release_date
            if len(release_date_str) == 4:
                album_date = datetime(int(release_date_str), 1, 1, tzinfo=timezone.utc)
            elif len(release_date_str) == 7:
                year, month = release_date_str.split('-')
                album_date = datetime(int(year), int(month), 1, tzinfo=timezone.utc)
            elif len(release_date_str) == 10:
                album_date = datetime.strptime(release_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            elif 'T' in release_date_str:
                date_part = release_date_str.split('T')[0]
                album_date = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            else:
                return False  # Can't parse — assume released
            return album_date > now
        except Exception:
            return False  # Error — assume released

    def _has_placeholder_tracks(self, tracks: list) -> bool:
        """Check if an album's tracks are mostly placeholders (unreleased/unannounced tracklist).
        Spotify uses 'Track 1', 'Track 2', etc. for tracks whose names haven't been revealed."""
        if not tracks or len(tracks) == 0:
            return False
        import re
        placeholder_count = 0
        for track in tracks:
            name = track.get('name', '') if isinstance(track, dict) else getattr(track, 'name', '')
            # Match "Track 1", "Track 2", ..., "Track 99" (case-insensitive)
            if re.match(r'^track\s+\d+$', name.strip(), re.IGNORECASE):
                placeholder_count += 1
        # If more than half the tracks are placeholders, skip the album
        # (some albums legitimately have a track called "Track X" but not most of them)
        return placeholder_count > len(tracks) / 2

    def _should_include_release(self, track_count: int, watchlist_artist: WatchlistArtist) -> bool:
        """
        Check if a release should be included based on user's preferences.

        Categorization:
        - Singles: 1-3 tracks
        - EPs: 4-6 tracks
        - Albums: 7+ tracks

        Args:
            track_count: Number of tracks in the release
            watchlist_artist: WatchlistArtist object with user preferences

        Returns:
            True if release should be included, False if should be skipped
        """
        try:
            # Default to including everything if preferences aren't set (backwards compatibility)
            include_albums = getattr(watchlist_artist, 'include_albums', True)
            include_eps = getattr(watchlist_artist, 'include_eps', True)
            include_singles = getattr(watchlist_artist, 'include_singles', True)

            # Determine release type based on track count
            if track_count >= 7:
                # This is an album
                return include_albums
            elif track_count >= 4:
                # This is an EP (4-6 tracks)
                return include_eps
            else:
                # This is a single (1-3 tracks)
                return include_singles

        except Exception as e:
            logger.warning(f"Error checking release inclusion: {e}")
            return True  # Default to including on error

    def _should_include_track(self, track, album_data, watchlist_artist: WatchlistArtist) -> bool:
        """
        Check if a track should be included based on content type filters.

        Filters:
        - Live versions
        - Remixes
        - Acoustic versions
        - Compilation albums

        Args:
            track: Track object or dict
            album_data: Album data object or dict
            watchlist_artist: WatchlistArtist object with user preferences

        Returns:
            True if track should be included, False if should be skipped
        """
        try:
            # Get track name and album name
            if isinstance(track, dict):
                track_name = track.get('name', '')
            else:
                track_name = getattr(track, 'name', '')

            if isinstance(album_data, dict):
                album_name = album_data.get('name', '')
            else:
                album_name = getattr(album_data, 'name', '')

            # Get user preferences (default to False = exclude by default)
            include_live = getattr(watchlist_artist, 'include_live', False)
            include_remixes = getattr(watchlist_artist, 'include_remixes', False)
            include_acoustic = getattr(watchlist_artist, 'include_acoustic', False)
            include_compilations = getattr(watchlist_artist, 'include_compilations', False)
            include_instrumentals = getattr(watchlist_artist, 'include_instrumentals', False)

            # Check compilation albums (album-level filter)
            if not include_compilations:
                if is_compilation_album(album_name):
                    logger.debug(f"Skipping compilation album: {album_name}")
                    return False

            # Check track content type filters
            if not include_live:
                if is_live_version(track_name, album_name):
                    logger.debug(f"Skipping live version: {track_name}")
                    return False

            if not include_remixes:
                if is_remix_version(track_name, album_name):
                    logger.debug(f"Skipping remix: {track_name}")
                    return False

            if not include_acoustic:
                if is_acoustic_version(track_name, album_name):
                    logger.debug(f"Skipping acoustic version: {track_name}")
                    return False

            # Check instrumental versions
            if not include_instrumentals:
                if is_instrumental_version(track_name, album_name):
                    logger.debug(f"Skipping instrumental version: {track_name}")
                    return False

            # Check custom exclusion terms
            try:
                from config.settings import config_manager as _cfg
                exclude_terms_str = _cfg.get('watchlist.exclude_terms', '')
                if exclude_terms_str:
                    exclude_terms = [t.strip() for t in exclude_terms_str.split(',') if t.strip()]
                    matched_term = matches_custom_exclude_terms(track_name, album_name, exclude_terms)
                    if matched_term:
                        logger.debug(f"Skipping track '{track_name}' — matched custom exclusion term: '{matched_term}'")
                        return False
            except Exception as e:
                logger.warning(f"Error checking custom exclusion terms: {e}")

            # Track passes all filters
            return True

        except Exception as e:
            logger.warning(f"Error checking track content type inclusion: {e}")
            return True  # Default to including on error

    def is_track_missing_from_library(self, track, album_name: str = None) -> bool:
        """
        Check if a track is missing from the local library.
        Uses the same matching logic as the download missing tracks modals.
        """
        try:
            # Handle both dict and object track formats
            if isinstance(track, dict):
                original_title = track.get('name', 'Unknown')
                track_artists = track.get('artists', [])
                artists_to_search = [artist.get('name', 'Unknown') for artist in track_artists] if track_artists else ["Unknown"]
            else:
                original_title = track.name
                artists_to_search = [artist.name for artist in track.artists] if track.artists else ["Unknown"]
            
            # Generate title variations (same logic as sync page)
            title_variations = [original_title]
            
            # Only add cleaned version if it removes clear noise
            cleaned_for_search = clean_track_name_for_search(original_title)
            if cleaned_for_search.lower() != original_title.lower():
                title_variations.append(cleaned_for_search)

            # Use matching engine's conservative clean_title
            base_title = self.matching_engine.clean_title(original_title)
            if base_title.lower() not in [t.lower() for t in title_variations]:
                title_variations.append(base_title)
            
            unique_title_variations = list(dict.fromkeys(title_variations))
            
            # Search for each artist with each title variation
            from config.settings import config_manager
            active_server = config_manager.get_active_media_server()
            allow_duplicates = config_manager.get('wishlist.allow_duplicate_tracks', True)

            for artist_name in artists_to_search:
                for query_title in unique_title_variations:
                    # When allow_duplicates is on, skip album hint so we get title+artist matches only
                    search_album = None if allow_duplicates else album_name
                    db_track, confidence = self.database.check_track_exists(query_title, artist_name, confidence_threshold=0.7, server_source=active_server, album=search_album)

                    if db_track and confidence >= 0.7:
                        # When allow_duplicates is on, only skip if the exact same album
                        if allow_duplicates and album_name:
                            lib_album = getattr(db_track, 'album_title', '') or ''
                            if lib_album:
                                from difflib import SequenceMatcher
                                album_sim = SequenceMatcher(None, album_name.lower(), lib_album.lower()).ratio()
                                if album_sim < 0.85:
                                    logger.info(f"[AllowDup] Different album — allowing: '{original_title}' (wanted: '{album_name}', library: '{lib_album}', sim: {album_sim:.2f})")
                                    continue  # Different album — allow it
                                else:
                                    logger.info(f"[AllowDup] Same album — skipping: '{original_title}' (wanted: '{album_name}', library: '{lib_album}', sim: {album_sim:.2f})")
                            else:
                                # No album info in library — can't compare, allow it
                                logger.info(f"[AllowDup] No album info in library — allowing: '{original_title}'")
                                continue
                        logger.debug(f"Track found in library: '{original_title}' by '{artist_name}' (confidence: {confidence:.2f})")
                        return False  # Track exists in library
            
            # No match found with any variation or artist
            logger.info(f"Track missing from library: '{original_title}' by '{artists_to_search[0] if artists_to_search else 'Unknown'}' - adding to wishlist")
            return True  # Track is missing
            
        except Exception as e:
            # Handle both dict and object track formats for error logging
            track_name = track.get('name', 'Unknown') if isinstance(track, dict) else getattr(track, 'name', 'Unknown')
            logger.warning(f"Error checking if track exists: {track_name}: {e}")
            return True  # Assume missing if we can't check
    
    def add_track_to_wishlist(self, track, album, watchlist_artist: WatchlistArtist) -> bool:
        """Add a missing track to the wishlist"""
        try:
            # Handle both dict and object track/album formats
            if isinstance(track, dict):
                track_id = track.get('id', '')
                track_name = track.get('name', 'Unknown')
                track_artists = track.get('artists', [])
                track_duration = track.get('duration_ms', 0)
                track_explicit = track.get('explicit', False)
                track_external_urls = track.get('external_urls', {})
                track_popularity = track.get('popularity', 0)
                track_preview_url = track.get('preview_url', None)
                track_number = track.get('track_number', 1)
                disc_number = track.get('disc_number', 1)
                track_uri = track.get('uri', '')
            else:
                track_id = track.id
                track_name = track.name
                track_artists = [{'name': artist.name, 'id': artist.id} for artist in track.artists]
                track_duration = getattr(track, 'duration_ms', 0)
                track_explicit = getattr(track, 'explicit', False)
                track_external_urls = getattr(track, 'external_urls', {})
                track_popularity = getattr(track, 'popularity', 0)
                track_preview_url = getattr(track, 'preview_url', None)
                track_number = getattr(track, 'track_number', 1)
                disc_number = getattr(track, 'disc_number', 1)
                track_uri = getattr(track, 'uri', '')
            
            if isinstance(album, dict):
                album_name = album.get('name', 'Unknown')
                album_id = album.get('id', '')
                album_release_date = album.get('release_date', '')
                album_images = album.get('images', [])
                album_type = album.get('album_type', 'album')  # 'album', 'single', or 'ep'
                total_tracks = album.get('total_tracks', 0)
                album_artists = album.get('artists', [])
            else:
                album_name = album.name
                album_id = album.id
                album_release_date = album.release_date
                album_images = album.images if hasattr(album, 'images') else []
                album_type = album.album_type if hasattr(album, 'album_type') else 'album'
                total_tracks = album.total_tracks if hasattr(album, 'total_tracks') else 0
                album_artists = album.artists if hasattr(album, 'artists') else []

            # Create Spotify track data structure
            spotify_track_data = {
                'id': track_id,
                'name': track_name,
                'artists': track_artists,
                'album': {
                    'name': album_name,
                    'id': album_id,
                    'release_date': album_release_date,
                    'images': album_images,
                    'album_type': album_type,  # Store album type for category filtering
                    'total_tracks': total_tracks,  # Store track count for accurate categorization
                    'artists': album_artists
                },
                'duration_ms': track_duration,
                'explicit': track_explicit,
                'external_urls': track_external_urls,
                'popularity': track_popularity,
                'preview_url': track_preview_url,
                'track_number': track_number,
                'disc_number': disc_number,
                'uri': track_uri,
                'is_local': False
            }
            
            # Add to wishlist with watchlist context (scoped to artist's profile)
            success = self.database.add_to_wishlist(
                spotify_track_data=spotify_track_data,
                failure_reason="Missing from library (found by watchlist scan)",
                source_type="watchlist",
                source_info={
                    'watchlist_artist_name': watchlist_artist.artist_name,
                    'watchlist_artist_id': watchlist_artist.spotify_artist_id,
                    'album_name': album_name,
                    'scan_timestamp': datetime.now().isoformat()
                },
                profile_id=getattr(watchlist_artist, 'profile_id', 1)
            )
            
            if success:
                first_artist = track_artists[0].get('name', 'Unknown') if track_artists else 'Unknown'
                logger.debug(f"Added track to wishlist: {track_name} by {first_artist}")
            else:
                logger.warning(f"Failed to add track to wishlist: {track_name}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error adding track to wishlist: {track_name}: {e}")
            return False
    
    def update_artist_scan_timestamp(self, artist) -> bool:
        """Update the last scan timestamp for an artist.

        Args:
            artist: WatchlistArtist object, or a string spotify_artist_id for backward compat
        """
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Support both WatchlistArtist objects and raw string IDs
                if hasattr(artist, 'id'):
                    # WatchlistArtist object - use database primary key (always reliable)
                    cursor.execute("""
                        UPDATE watchlist_artists
                        SET last_scan_timestamp = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (artist.id,))
                    artist_label = f"{artist.artist_name} (id={artist.id})"
                else:
                    # Backward compat: raw string ID (try spotify, then itunes)
                    cursor.execute("""
                        UPDATE watchlist_artists
                        SET last_scan_timestamp = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                        WHERE spotify_artist_id = ? OR itunes_artist_id = ?
                    """, (artist, artist))
                    artist_label = f"ID {artist}"

                conn.commit()

                if cursor.rowcount > 0:
                    logger.debug(f"Updated scan timestamp for artist {artist_label}")
                    return True
                else:
                    logger.warning(f"No artist found for {artist_label}")
                    return False

        except Exception as e:
            logger.error(f"Error updating scan timestamp: {e}")
            return False

    def _fetch_similar_artists_from_musicmap(self, artist_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Fetch similar artists from MusicMap and match them to both Spotify and iTunes.

        Args:
            artist_name: The artist name to find similar artists for
            limit: Maximum number of similar artists to return (default: 20)

        Returns:
            List of matched artist dictionaries with both Spotify and iTunes IDs when available
        """
        try:
            logger.info(f"Fetching similar artists from MusicMap for: {artist_name}")

            # Construct MusicMap URL
            url_artist = artist_name.lower().replace(' ', '+')
            musicmap_url = f'https://www.music-map.com/{url_artist}'

            # Set headers to mimic a browser
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }

            # Fetch MusicMap page
            response = requests.get(musicmap_url, headers=headers, timeout=10)
            response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            gnod_map = soup.find(id='gnodMap')

            if not gnod_map:
                logger.warning(f"Could not find artist map on MusicMap for {artist_name}")
                return []

            # Extract similar artist names
            all_anchors = gnod_map.find_all('a')
            searched_artist_lower = artist_name.lower().strip()

            similar_artist_names = []
            for anchor in all_anchors:
                artist_text = anchor.get_text(strip=True)

                # Skip if this is the searched artist
                if artist_text.lower() == searched_artist_lower:
                    continue

                similar_artist_names.append(artist_text)

            logger.info(f"Found {len(similar_artist_names)} similar artists from MusicMap")

            # Get fallback metadata client for matching (iTunes or Deezer)
            itunes_client, fallback_source = _get_fallback_metadata_client()

            # Get the searched artist's IDs to exclude them
            searched_spotify_id = None
            searched_fallback_id = None
            try:
                # Try Spotify search (only when Spotify is the configured primary source)
                if self._spotify_is_primary_source():
                    searched_results = self.spotify_client.search_artists(artist_name, limit=1, allow_fallback=False)
                    if searched_results and len(searched_results) > 0:
                        searched_spotify_id = searched_results[0].id
            except Exception as e:
                logger.debug(f"Could not get searched artist Spotify ID: {e}")

            try:
                # Try fallback source (iTunes/Deezer) search
                fallback_results = itunes_client.search_artists(artist_name, limit=1)
                if fallback_results and len(fallback_results) > 0:
                    searched_fallback_id = fallback_results[0].id
            except Exception as e:
                logger.debug(f"Could not get searched artist {fallback_source} ID: {e}")

            # Match each artist to both Spotify and fallback source (iTunes/Deezer)
            matched_artists = []
            seen_names = set()  # Track seen artist names to prevent duplicates

            for artist_name_to_match in similar_artist_names[:limit]:
                try:
                    # Skip if we've already matched this artist name
                    name_lower = artist_name_to_match.lower().strip()
                    if name_lower in seen_names:
                        continue

                    artist_data = {
                        'name': artist_name_to_match,
                        'spotify_id': None,
                        'itunes_id': None,
                        'deezer_id': None,
                        'image_url': None,
                        'genres': [],
                        'popularity': 0
                    }

                    # Try to match on Spotify (only when Spotify is the configured primary source)
                    if self._spotify_is_primary_source():
                        try:
                            spotify_results = self.spotify_client.search_artists(
                                artist_name_to_match,
                                limit=1,
                                allow_fallback=False,
                            )
                            if spotify_results and len(spotify_results) > 0:
                                spotify_artist = spotify_results[0]
                                # Skip if this is the searched artist
                                if spotify_artist.id != searched_spotify_id:
                                    artist_data['spotify_id'] = spotify_artist.id
                                    artist_data['name'] = spotify_artist.name  # Use canonical name
                                    artist_data['image_url'] = spotify_artist.image_url if hasattr(spotify_artist, 'image_url') else None
                                    artist_data['genres'] = spotify_artist.genres if hasattr(spotify_artist, 'genres') else []
                                    artist_data['popularity'] = spotify_artist.popularity if hasattr(spotify_artist, 'popularity') else 0
                        except Exception as e:
                            logger.debug(f"Spotify match failed for {artist_name_to_match}: {e}")

                    # Try to match on fallback source (iTunes/Deezer) with retry for rate limiting
                    try:
                        fallback_results = itunes_api_call_with_retry(
                            itunes_client.search_artists, artist_name_to_match, limit=1
                        )
                        if fallback_results and len(fallback_results) > 0:
                            fallback_artist = fallback_results[0]
                            # Skip if this is the searched artist
                            if fallback_artist.id != searched_fallback_id:
                                # Store under the appropriate key based on fallback source
                                if fallback_source == 'deezer':
                                    artist_data['deezer_id'] = fallback_artist.id
                                else:
                                    artist_data['itunes_id'] = fallback_artist.id
                                # Use fallback name if we don't have Spotify
                                if not artist_data['spotify_id']:
                                    artist_data['name'] = fallback_artist.name
                                # Use fallback genres if we don't have Spotify genres
                                if not artist_data['genres'] and hasattr(fallback_artist, 'genres'):
                                    artist_data['genres'] = fallback_artist.genres
                        else:
                            logger.info(f"  [{fallback_source}] No match found for: {artist_name_to_match}")
                    except Exception as e:
                        logger.info(f"  [{fallback_source}] Match failed for {artist_name_to_match}: {e}")

                    # Only add if we got at least one ID
                    fallback_id_key = 'deezer_id' if fallback_source == 'deezer' else 'itunes_id'
                    if artist_data['spotify_id'] or artist_data.get(fallback_id_key):
                        seen_names.add(name_lower)
                        matched_artists.append(artist_data)
                        logger.debug(f"  Matched: {artist_data['name']} (Spotify: {artist_data['spotify_id']}, {fallback_source}: {artist_data.get(fallback_id_key)})")

                except Exception as match_error:
                    logger.debug(f"Error matching {artist_name_to_match}: {match_error}")
                    continue

            # Log detailed matching statistics
            fallback_id_key = 'deezer_id' if fallback_source == 'deezer' else 'itunes_id'
            fallback_matched = sum(1 for a in matched_artists if a.get(fallback_id_key))
            spotify_matched = sum(1 for a in matched_artists if a.get('spotify_id'))
            both_matched = sum(1 for a in matched_artists if a.get(fallback_id_key) and a.get('spotify_id'))
            logger.info(f"Matched {len(matched_artists)} similar artists - {fallback_source}: {fallback_matched}, Spotify: {spotify_matched}, Both: {both_matched}")
            return matched_artists

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching from MusicMap: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching similar artists from MusicMap: {e}")
            return []

    def _backfill_similar_artists_itunes_ids(self, source_artist_id: str, profile_id: int = 1) -> int:
        """
        Backfill missing iTunes IDs for cached similar artists.
        This ensures seamless dual-source support without clearing cached data.

        Args:
            source_artist_id: The source artist ID to backfill similar artists for
            profile_id: Profile to scope the backfill to

        Returns:
            Number of similar artists updated with iTunes IDs
        """
        try:
            # Get fallback metadata client (iTunes or Deezer)
            fallback_client, fallback_source = _get_fallback_metadata_client()

            # Get similar artists that are missing IDs for the active fallback source
            similar_artists = self.database.get_similar_artists_missing_fallback_ids(source_artist_id, fallback_source, profile_id=profile_id)

            if not similar_artists:
                return 0

            logger.info(f"Backfilling {fallback_source} IDs for {len(similar_artists)} similar artists")

            updated_count = 0
            for similar_artist in similar_artists:
                try:
                    results = fallback_client.search_artists(similar_artist.similar_artist_name, limit=1)
                    if results and len(results) > 0:
                        found_id = results[0].id
                        # Update the similar artist with the correct source ID
                        if fallback_source == 'deezer':
                            success = self.database.update_similar_artist_deezer_id(similar_artist.id, found_id)
                        else:
                            success = self.database.update_similar_artist_itunes_id(similar_artist.id, found_id)
                        if success:
                            updated_count += 1
                            logger.debug(f"  Backfilled {fallback_source} ID {found_id} for {similar_artist.similar_artist_name}")
                except Exception as e:
                    logger.debug(f"  Could not backfill {fallback_source} ID for {similar_artist.similar_artist_name}: {e}")
                    continue

            if updated_count > 0:
                logger.info(f"Backfilled {updated_count} similar artists with {fallback_source} IDs")

            return updated_count

        except Exception as e:
            logger.error(f"Error backfilling similar artists {fallback_source if 'fallback_source' in dir() else 'fallback'} IDs: {e}")
            return 0

    def update_similar_artists(
        self,
        watchlist_artist: WatchlistArtist,
        limit: int = 10,
        profile_id: int = 1,
        source_artist_id: Optional[str] = None,
    ) -> bool:
        """
        Fetch and store similar artists for a watchlist artist.
        Called after each artist scan to build discovery pool.
        Uses MusicMap to find similar artists and matches them to both Spotify and iTunes.
        """
        try:
            logger.info(f"Fetching similar artists for {watchlist_artist.artist_name}")

            # Get similar artists from MusicMap (returns list of artist dicts with both IDs)
            similar_artists = self._fetch_similar_artists_from_musicmap(watchlist_artist.artist_name, limit=limit)

            if not similar_artists:
                logger.debug(f"No similar artists found for {watchlist_artist.artist_name}")
                return True  # Not an error, just no recommendations

            logger.info(f"Found {len(similar_artists)} similar artists for {watchlist_artist.artist_name}")

            # Use the ID that matched the scan source when available; otherwise fall back to any known ID.
            source_artist_id = (
                source_artist_id
                or watchlist_artist.spotify_artist_id
                or watchlist_artist.itunes_artist_id
                or watchlist_artist.deezer_artist_id
                or watchlist_artist.discogs_artist_id
                or str(watchlist_artist.id)
            )

            # Store each similar artist in database
            stored_count = 0
            for rank, similar_artist in enumerate(similar_artists, 1):
                try:
                    # similar_artist has 'name', 'spotify_id', 'itunes_id', 'deezer_id', 'image_url', 'genres', 'popularity'
                    success = self.database.add_or_update_similar_artist(
                        source_artist_id=source_artist_id,
                        similar_artist_name=similar_artist['name'],
                        similar_artist_spotify_id=similar_artist.get('spotify_id'),
                        similar_artist_itunes_id=similar_artist.get('itunes_id'),
                        similarity_rank=rank,
                        profile_id=profile_id,
                        image_url=similar_artist.get('image_url'),
                        genres=similar_artist.get('genres'),
                        popularity=similar_artist.get('popularity', 0),
                        similar_artist_deezer_id=similar_artist.get('deezer_id')
                    )

                    if success:
                        stored_count += 1
                        fallback_id = similar_artist.get('deezer_id') or similar_artist.get('itunes_id')
                        fallback_label = 'Deezer' if similar_artist.get('deezer_id') else 'iTunes'
                        logger.debug(f"  #{rank}: {similar_artist['name']} (Spotify: {similar_artist.get('spotify_id')}, {fallback_label}: {fallback_id})")

                except Exception as e:
                    logger.warning(f"Error storing similar artist {similar_artist.get('name', 'Unknown')}: {e}")
                    continue

            logger.info(f"Stored {stored_count}/{len(similar_artists)} similar artists for {watchlist_artist.artist_name}")
            return True

        except Exception as e:
            logger.error(f"Error fetching similar artists for {watchlist_artist.artist_name}: {e}")
            return False

    def populate_discovery_pool(self, top_artists_limit: int = 50, albums_per_artist: int = 10, profile_id: int = 1, progress_callback=None):
        """
        Populate discovery pool with tracks from top similar artists.
        Called after watchlist scan completes.

        Supports both Spotify and iTunes sources - populates for whichever is available.
        - Checks if pool was updated in last 24 hours (prevents over-polling)
        - Includes albums, singles, and EPs for comprehensive coverage
        - Appends to existing pool instead of replacing it
        - Cleans up tracks older than 365 days (maintains 1 year rolling window)
        """
        try:
            from datetime import datetime, timedelta
            import random

            # Check if we should run discovery pool population (prevents over-polling)
            skip_pool_population = not self.database.should_populate_discovery_pool(hours_threshold=24, profile_id=profile_id)

            if skip_pool_population:
                logger.info("Discovery pool was populated recently (< 24 hours ago). Skipping pool population.")
                logger.info("But still refreshing recent albums cache and curated playlists...")
                if progress_callback:
                    progress_callback('skip', 'Discovery pool recently updated, skipping')
                # Still run these even when skipping main pool population
                if progress_callback:
                    progress_callback('phase', 'Caching recent albums...')
                self.cache_discovery_recent_albums(profile_id=profile_id)
                if progress_callback:
                    progress_callback('phase', 'Curating playlists...')
                self.curate_discovery_playlists(profile_id=profile_id)
                return

            logger.info("Populating discovery pool from similar artists...")

            discovery_sources = self._discovery_source_priority()
            if not discovery_sources:
                logger.warning("No music sources available to populate discovery pool")
                return

            logger.info("Discovery source priority: %s", discovery_sources)

            # Get top similar artists for this profile's watchlist (ordered by occurrence_count)
            similar_artists = self.database.get_top_similar_artists(limit=top_artists_limit, profile_id=profile_id)

            if not similar_artists:
                logger.info("No similar artists found to populate discovery pool from similar artists")
                logger.info("But still caching recent albums from watchlist artists and curating playlists...")
                if progress_callback:
                    progress_callback('skip', 'No similar artists found')
                # Still run these even without similar artists - they use watchlist artists
                if progress_callback:
                    progress_callback('phase', 'Caching recent albums...')
                self.cache_discovery_recent_albums(profile_id=profile_id)
                if progress_callback:
                    progress_callback('phase', 'Curating playlists...')
                self.curate_discovery_playlists(profile_id=profile_id)
                return

            logger.info(f"Processing {len(similar_artists)} top similar artists for discovery pool")

            total_tracks_added = 0

            for artist_idx, similar_artist in enumerate(similar_artists, 1):
                try:
                    logger.info(f"[{artist_idx}/{len(similar_artists)}] Processing {similar_artist.similar_artist_name} (occurrence: {similar_artist.occurrence_count})")
                    if progress_callback:
                        progress_callback('artist', f'{similar_artist.similar_artist_name} ({artist_idx}/{len(similar_artists)})')

                    # Resolve the first source that can actually produce albums.
                    selected_source = None
                    selected_artist_id = None
                    selected_albums = []
                    artist_genres: List[str] = []

                    for source in discovery_sources:
                        source_attr = self._artist_id_attribute_for_source(source)
                        stored_id = getattr(similar_artist, source_attr, None) if source_attr else None

                        cache_callback = None
                        if source == 'itunes':
                            cache_callback = lambda found_id, artist_id=similar_artist.id: self.database.update_similar_artist_itunes_id(artist_id, found_id)
                        elif source == 'deezer':
                            cache_callback = lambda found_id, artist_id=similar_artist.id: self.database.update_similar_artist_deezer_id(artist_id, found_id)

                        artist_id = self._resolve_artist_id_for_source(
                            source,
                            similar_artist.similar_artist_name,
                            stored_id=stored_id,
                            cache_callback=cache_callback,
                        )
                        if not artist_id:
                            continue

                        all_albums = self._get_artist_albums_for_source(
                            source,
                            artist_id,
                            album_type='album,single,ep',
                            limit=50,
                            skip_cache=False,
                            max_pages=2,
                        )
                        if not all_albums:
                            logger.debug(f"No albums found for {similar_artist.similar_artist_name} on {source}")
                            continue

                        artist_data = self._get_artist_data_for_source(source, artist_id)
                        if artist_data and 'genres' in artist_data:
                            artist_genres = artist_data['genres']

                        albums = [a for a in all_albums if hasattr(a, 'album_type') and a.album_type == 'album']
                        singles_eps = [a for a in all_albums if hasattr(a, 'album_type') and a.album_type in ['single', 'ep']]
                        selected_albums = []

                        latest_releases = all_albums[:3]
                        selected_albums.extend(latest_releases)

                        remaining_slots = albums_per_artist - len(selected_albums)
                        if remaining_slots > 0:
                            remaining_content = all_albums[3:]
                            if len(remaining_content) > remaining_slots:
                                selected_albums.extend(random.sample(remaining_content, remaining_slots))
                            else:
                                selected_albums.extend(remaining_content)

                        selected_source = source
                        selected_artist_id = artist_id
                        logger.info(
                            f"  [{source}] Selected {len(selected_albums)} releases from {len(all_albums)} available "
                            f"(albums: {len(albums)}, singles/EPs: {len(singles_eps)})"
                        )
                        break

                    if not selected_source or not selected_artist_id or not selected_albums:
                        logger.debug(f"No valid source/albums for {similar_artist.similar_artist_name}, skipping")
                        continue

                    # Process each selected album from the winning source.
                    for album_idx, album in enumerate(selected_albums, 1):
                        try:
                            album_data = self._get_album_data_for_source(selected_source, album.id, album_name=album.name)
                            if not album_data:
                                continue

                            tracks = self._extract_track_items(album_data)
                            logger.debug(f"    Album {album_idx}: {album_data.get('name', 'Unknown')} ({len(tracks)} tracks)")

                            if self._has_placeholder_tracks(tracks):
                                logger.info(f"    Skipping album with placeholder tracks: {album_data.get('name', 'Unknown')}")
                                continue

                            is_new = False
                            try:
                                release_date_str = album_data.get('release_date', '')
                                if release_date_str and len(release_date_str) >= 10:
                                    release_date = datetime.strptime(release_date_str[:10], "%Y-%m-%d")
                                    is_new = (datetime.now() - release_date).days <= 30
                            except Exception:
                                pass

                            for track in tracks:
                                try:
                                    enhanced_track = {
                                        **track,
                                        'album': {
                                            'id': album_data['id'],
                                            'name': album_data.get('name', 'Unknown Album'),
                                            'images': album_data.get('images', []),
                                            'release_date': album_data.get('release_date', ''),
                                            'album_type': album_data.get('album_type', 'album'),
                                            'total_tracks': album_data.get('total_tracks', 0)
                                        },
                                        '_source': selected_source
                                    }

                                    raw_popularity = album_data.get('popularity', 0)
                                    if selected_source in ('itunes', 'deezer') and raw_popularity == 0:
                                        synth_pop = 45
                                        if is_new:
                                            synth_pop += 25
                                        else:
                                            try:
                                                release_str = album_data.get('release_date', '')
                                                if release_str and len(release_str) >= 10:
                                                    rel_date = datetime.strptime(release_str[:10], "%Y-%m-%d")
                                                    age_days = (datetime.now() - rel_date).days
                                                    if age_days <= 90:
                                                        synth_pop += 15
                                                    elif age_days <= 365:
                                                        synth_pop += 5
                                            except Exception:
                                                pass
                                        if similar_artist.occurrence_count >= 3:
                                            synth_pop += 10
                                        elif similar_artist.occurrence_count >= 2:
                                            synth_pop += 5
                                        raw_popularity = min(synth_pop, 100)

                                    track_data = {
                                        'track_name': track.get('name', 'Unknown Track'),
                                        'artist_name': similar_artist.similar_artist_name,
                                        'album_name': album_data.get('name', 'Unknown Album'),
                                        'album_cover_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                                        'duration_ms': track.get('duration_ms', 0),
                                        'popularity': raw_popularity,
                                        'release_date': album_data.get('release_date', ''),
                                        'is_new_release': is_new,
                                        'track_data_json': enhanced_track,
                                        'artist_genres': artist_genres
                                    }

                                    if selected_source == 'spotify':
                                        track_data['spotify_track_id'] = track.get('id')
                                        track_data['spotify_album_id'] = album_data.get('id')
                                        track_data['spotify_artist_id'] = selected_artist_id
                                    elif selected_source == 'deezer':
                                        track_data['deezer_track_id'] = track.get('id')
                                        track_data['deezer_album_id'] = album_data.get('id')
                                        track_data['deezer_artist_id'] = selected_artist_id
                                    else:
                                        track_data['itunes_track_id'] = track.get('id')
                                        track_data['itunes_album_id'] = album_data.get('id')
                                        track_data['itunes_artist_id'] = selected_artist_id

                                    if self.database.add_to_discovery_pool(track_data, source=selected_source, profile_id=profile_id):
                                        total_tracks_added += 1
                                except Exception as track_error:
                                    logger.debug(f"Error adding track to discovery pool: {track_error}")
                                    continue

                            time.sleep(DELAY_BETWEEN_ALBUMS)
                        except Exception as album_error:
                            logger.warning(f"Error processing album on {selected_source}: {album_error}")
                            continue

                    if artist_idx < len(similar_artists):
                        time.sleep(DELAY_BETWEEN_ARTISTS)

                except Exception as artist_error:
                    logger.warning(f"Error processing artist {similar_artist.similar_artist_name}: {artist_error}")
                    continue

            logger.info(f"Discovery pool from similar artists complete: {total_tracks_added} tracks added")
            if progress_callback:
                progress_callback('success', f'Discovery pool: {total_tracks_added} tracks from {len(similar_artists)} artists')

            # Note: Watchlist artist albums are already in discovery pool from the watchlist scan itself
            # No need to re-fetch them here to avoid duplicate API calls

            # Add tracks from random database albums for extra variety (reduced to 5 to save API calls)
            logger.info("Adding tracks from database albums to discovery pool...")
            try:
                with self.database._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT DISTINCT a.title, ar.name as artist_name
                        FROM albums a
                        JOIN artists ar ON a.artist_id = ar.id
                        ORDER BY RANDOM()
                        LIMIT 5
                    """)
                    db_albums = cursor.fetchall()

                    logger.info(f"Processing {len(db_albums)} database albums for discovery pool")

                    for db_idx, album_row in enumerate(db_albums, 1):
                        try:
                            query = f"{album_row['title']} {album_row['artist_name']}"
                            album_data = None
                            tracks = []
                            db_source = None
                            artist_id_for_genres = None

                            for source in discovery_sources:
                                try:
                                    search_query = query if source != 'spotify' else f"album:{album_row['title']} artist:{album_row['artist_name']}"
                                    search_results = self._search_albums_for_source(source, search_query, limit=1)
                                    if not search_results:
                                        continue

                                    album_candidate = search_results[0]
                                    album_data = self._get_album_data_for_source(source, album_candidate.id, album_name=album_row['title'])
                                    if not album_data:
                                        continue

                                    tracks = self._extract_track_items(album_data)
                                    if not tracks:
                                        continue

                                    db_source = source
                                    if album_data.get('artists'):
                                        artist_id_for_genres = album_data['artists'][0].get('id')
                                    break
                                except Exception as e:
                                    logger.debug(f"{source} search failed for {album_row['title']}: {e}")

                            if not tracks or not album_data:
                                continue

                            artist_genres = []
                            try:
                                if artist_id_for_genres:
                                    artist_data = self._get_artist_data_for_source(db_source, artist_id_for_genres)
                                    if artist_data and 'genres' in artist_data:
                                        artist_genres = artist_data['genres']
                            except Exception as e:
                                logger.debug(f"Could not fetch genres for album artist: {e}")

                            is_new = False
                            try:
                                release_date_str = album_data.get('release_date', '')
                                if release_date_str and len(release_date_str) >= 10:
                                    release_date = datetime.strptime(release_date_str[:10], "%Y-%m-%d")
                                    is_new = (datetime.now() - release_date).days <= 30
                            except Exception:
                                pass

                            for track in tracks:
                                try:
                                    enhanced_track = {
                                        **track,
                                        'album': {
                                            'id': album_data['id'],
                                            'name': album_row['title'],
                                            'images': album_data.get('images', []),
                                            'release_date': album_data.get('release_date', ''),
                                            'album_type': album_data.get('album_type', 'album'),
                                            'total_tracks': album_data.get('total_tracks', 0)
                                        },
                                        '_source': db_source
                                    }

                                    track_data = {
                                        'track_name': track.get('name', 'Unknown Track'),
                                        'artist_name': album_row['artist_name'],
                                        'album_name': album_row['title'],
                                        'album_cover_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                                        'duration_ms': track.get('duration_ms', 0),
                                        'popularity': album_data.get('popularity', 0),
                                        'release_date': album_data.get('release_date', ''),
                                        'is_new_release': is_new,
                                        'track_data_json': enhanced_track,
                                        'artist_genres': artist_genres
                                    }

                                    if db_source == 'spotify':
                                        track_data['spotify_track_id'] = track.get('id')
                                        track_data['spotify_album_id'] = album_data.get('id')
                                        track_data['spotify_artist_id'] = artist_id_for_genres or ''
                                    elif db_source == 'deezer':
                                        track_data['deezer_track_id'] = track.get('id')
                                        track_data['deezer_album_id'] = album_data.get('id')
                                        track_data['deezer_artist_id'] = artist_id_for_genres or ''
                                    else:
                                        track_data['itunes_track_id'] = track.get('id')
                                        track_data['itunes_album_id'] = album_data.get('id')
                                        track_data['itunes_artist_id'] = artist_id_for_genres or ''

                                    if self.database.add_to_discovery_pool(track_data, source=db_source, profile_id=profile_id):
                                        total_tracks_added += 1
                                except Exception:
                                    continue

                            time.sleep(DELAY_BETWEEN_ALBUMS)
                        except Exception as album_error:
                            logger.debug(f"Error processing database album {album_row['title']}: {album_error}")
                            continue

                        # Rate limit between albums
                        if db_idx < len(db_albums):
                            time.sleep(DELAY_BETWEEN_ARTISTS)

            except Exception as db_error:
                logger.warning(f"Error processing database albums: {db_error}")

            logger.info(f"Discovery pool population complete: {total_tracks_added} total tracks added from all sources")

            # Clean up tracks older than 365 days (maintain 1 year rolling window)
            logger.info("Cleaning up discovery tracks older than 365 days...")
            deleted_count = self.database.cleanup_old_discovery_tracks(days_threshold=365)
            logger.info(f"Cleaned up {deleted_count} old tracks from discovery pool")

            # Get final track count for metadata
            with self.database._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as count FROM discovery_pool")
                final_count = cursor.fetchone()['count']

            # Update timestamp to mark when pool was last populated
            self.database.update_discovery_pool_timestamp(track_count=final_count, profile_id=profile_id)
            logger.info(f"Discovery pool now contains {final_count} total tracks (built over time)")

            # Cache recent albums for discovery page
            logger.info("Caching recent albums for discovery page...")
            if progress_callback:
                progress_callback('phase', 'Caching recent albums...')
            self.cache_discovery_recent_albums(profile_id=profile_id)

            # Curate playlists for consistent daily experience
            logger.info("Curating discovery playlists...")
            if progress_callback:
                progress_callback('phase', 'Curating playlists...')
            self.curate_discovery_playlists(profile_id=profile_id)

        except Exception as e:
            logger.error(f"Error populating discovery pool: {e}")
            import traceback
            traceback.print_exc()

    def update_discovery_pool_incremental(self, profile_id: int = 1):
        """
        Lightweight incremental update for discovery pool - runs every 6 hours.

        IMPROVED: Quick check for new releases from watchlist artists only
        - Much faster than full populate_discovery_pool (only checks watchlist, not similar artists)
        - Only fetches latest 5 releases per artist
        - Only adds tracks from releases in last 7 days
        - Respects 6-hour cooldown to avoid over-polling
        """
        try:
            from datetime import datetime, timedelta

            # Check if we should run (prevents over-polling Spotify)
            if not self.database.should_populate_discovery_pool(hours_threshold=6, profile_id=profile_id):
                logger.info("Discovery pool was updated recently (< 6 hours ago). Skipping incremental update.")
                return

            logger.info("Starting incremental discovery pool update (watchlist artists only)...")

            watchlist_artists = self.database.get_watchlist_artists(profile_id=profile_id)
            if not watchlist_artists:
                logger.info("No watchlist artists to check for incremental update")
                return

            cutoff_date = datetime.now() - timedelta(days=7)  # Only last week's releases
            total_tracks_added = 0

            for artist_idx, artist in enumerate(watchlist_artists, 1):
                try:
                    logger.info(f"[{artist_idx}/{len(watchlist_artists)}] Checking {artist.artist_name} for new releases...")

                    # Only fetch latest 5 releases (much faster than full scan)
                    recent_releases = self.spotify_client.get_artist_albums(
                        artist.spotify_artist_id,
                        album_type='album,single,ep',
                        limit=5,
                        skip_cache=True,
                        max_pages=1,
                    )

                    if not recent_releases:
                        continue

                    # Fetch artist genres once for all tracks of this artist
                    artist_genres = []
                    try:
                        artist_data = self.spotify_client.get_artist(artist.spotify_artist_id)
                        if artist_data and 'genres' in artist_data:
                            artist_genres = artist_data['genres']
                    except Exception as e:
                        logger.debug(f"Could not fetch genres for {artist.artist_name}: {e}")

                    for release in recent_releases:
                        try:
                            # Check if release is within cutoff
                            if not self.is_album_after_timestamp(release, cutoff_date):
                                continue  # Skip older releases

                            # Get full album data with tracks
                            album_data = self.spotify_client.get_album(release.id)
                            if not album_data or 'tracks' not in album_data:
                                continue

                            tracks = album_data['tracks'].get('items', [])
                            logger.debug(f"  New release: {release.name} ({len(tracks)} tracks)")

                            # Determine if this is a new release (within last 30 days)
                            is_new = False
                            try:
                                release_date_str = album_data.get('release_date', '')
                                if release_date_str and len(release_date_str) == 10:
                                    release_date = datetime.strptime(release_date_str, "%Y-%m-%d")
                                    days_old = (datetime.now() - release_date).days
                                    is_new = days_old <= 30
                            except:
                                pass

                            # Add each track to discovery pool
                            for track in tracks:
                                try:
                                    # Enhance track object with full album data (including album_type)
                                    enhanced_track = {
                                        **track,
                                        'album': {
                                            'id': album_data['id'],
                                            'name': album_data.get('name', 'Unknown Album'),
                                            'images': album_data.get('images', []),
                                            'release_date': album_data.get('release_date', ''),
                                            'album_type': album_data.get('album_type', 'album'),
                                            'total_tracks': album_data.get('total_tracks', 0)
                                        }
                                    }

                                    track_data = {
                                        'spotify_track_id': track['id'],
                                        'spotify_album_id': album_data['id'],
                                        'spotify_artist_id': artist.spotify_artist_id,
                                        'track_name': track['name'],
                                        'artist_name': artist.artist_name,
                                        'album_name': album_data.get('name', 'Unknown Album'),
                                        'album_cover_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                                        'duration_ms': track.get('duration_ms', 0),
                                        'popularity': album_data.get('popularity', 0),
                                        'release_date': album_data.get('release_date', ''),
                                        'is_new_release': is_new,
                                        'track_data_json': enhanced_track,  # Store enhanced track with full album data
                                        'artist_genres': artist_genres
                                    }

                                    if self.database.add_to_discovery_pool(track_data, profile_id=profile_id):
                                        total_tracks_added += 1

                                except Exception as track_error:
                                    logger.debug(f"Error adding track to discovery pool: {track_error}")
                                    continue

                        except Exception as release_error:
                            logger.warning(f"Error processing release: {release_error}")
                            continue

                    # Small delay between artists
                    if artist_idx < len(watchlist_artists):
                        time.sleep(DELAY_BETWEEN_ARTISTS)

                except Exception as artist_error:
                    logger.warning(f"Error checking {artist.artist_name}: {artist_error}")
                    continue

            logger.info(f"Incremental update complete: {total_tracks_added} new tracks added from watchlist artists")

            # Update timestamp
            if total_tracks_added > 0:
                # Get current track count
                with self.database._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) as count FROM discovery_pool")
                    current_count = cursor.fetchone()['count']

                self.database.update_discovery_pool_timestamp(track_count=current_count, profile_id=profile_id)
                logger.info(f"Discovery pool now contains {current_count} total tracks")

        except Exception as e:
            logger.error(f"Error during incremental discovery pool update: {e}")
            import traceback
            traceback.print_exc()

    def cache_discovery_recent_albums(self, profile_id: int = 1):
        """
        Cache recent albums from watchlist and similar artists for discover page.

        Supports both Spotify and iTunes sources - iTunes is always processed (baseline),
        Spotify is added when authenticated. Same pattern as discovery pool.
        """
        try:
            from datetime import datetime, timedelta

            logger.info("Caching recent albums for discover page...")

            if self.spotify_client and self.spotify_client.is_rate_limited():
                self._disable_spotify_for_run("global Spotify rate limit active")

            # Clear existing cache for this profile
            self.database.clear_discovery_recent_albums(profile_id=profile_id)

            # Adaptive window based on listening velocity
            days_lookback = 30
            try:
                profile = self._get_listening_profile(profile_id)
                if profile['has_data']:
                    if profile['avg_daily_plays'] < 5:
                        days_lookback = 60   # Casual listener — show more
                    elif profile['avg_daily_plays'] > 20:
                        days_lookback = 21   # Heavy listener — keep it fresh
                    logger.info(f"Recent albums window: {days_lookback} days (avg {profile['avg_daily_plays']:.1f} plays/day)")
            except Exception:
                pass
            cutoff_date = datetime.now() - timedelta(days=days_lookback)
            cached_count = {'spotify': 0, 'itunes': 0, 'deezer': 0}
            albums_checked = 0

            # Determine available sources
            spotify_available = self._spotify_is_primary_source()

            # Get fallback metadata client (iTunes or Deezer)
            itunes_client, fallback_source = _get_fallback_metadata_client()

            # Get artists to check (scoped to profile)
            watchlist_artists = self.database.get_watchlist_artists(profile_id=profile_id)
            similar_artists = self.database.get_top_similar_artists(limit=50, profile_id=profile_id)

            logger.info(f"Checking albums from {len(watchlist_artists)} watchlist + {len(similar_artists)} similar artists")
            logger.info(f"Sources: Spotify={spotify_available}, {fallback_source}=True")

            def process_album(album, artist_name, artist_spotify_id, artist_itunes_id, source, artist_deezer_id=None):
                """Helper to process and cache a single album"""
                nonlocal albums_checked
                try:
                    albums_checked += 1
                    release_str = album.release_date if hasattr(album, 'release_date') else None

                    if not release_str:
                        return False

                    # Handle iTunes/Deezer ISO format (2017-12-08T08:00:00Z)
                    if 'T' in release_str:
                        release_str = release_str.split('T')[0]

                    if len(release_str) >= 10:
                        release_date = datetime.strptime(release_str[:10], "%Y-%m-%d")
                        if release_date >= cutoff_date:
                            album_data = {
                                'album_spotify_id': album.id if source == 'spotify' else None,
                                'album_itunes_id': album.id if source == 'itunes' else None,
                                'album_deezer_id': album.id if source == 'deezer' else None,
                                'album_name': album.name,
                                'artist_name': artist_name,
                                'artist_spotify_id': artist_spotify_id,
                                'artist_itunes_id': artist_itunes_id,
                                'artist_deezer_id': artist_deezer_id,
                                'album_cover_url': album.image_url if hasattr(album, 'image_url') else None,
                                'release_date': release_str[:10],
                                'album_type': album.album_type if hasattr(album, 'album_type') else 'album'
                            }
                            if self.database.cache_discovery_recent_album(album_data, source=source, profile_id=profile_id):
                                cached_count[source] += 1
                                logger.debug(f"Cached [{source}] recent album: {album.name} by {artist_name} ({release_str})")
                                return True
                except Exception as e:
                    logger.debug(f"Error processing album: {e}")
                return False

            # Track resolution stats
            fallback_resolved = 0
            fallback_failed_resolve = 0

            # Process watchlist artists
            for artist in watchlist_artists:
                # Always process fallback source (iTunes or Deezer) as baseline
                fallback_id = artist.itunes_artist_id if fallback_source == 'itunes' else artist.deezer_artist_id
                if not fallback_id:
                    # Try to resolve fallback ID on-the-fly (with retry for rate limiting)
                    try:
                        results = itunes_api_call_with_retry(
                            itunes_client.search_artists, artist.artist_name, limit=1
                        )
                        if results and len(results) > 0:
                            fallback_id = results[0].id
                            fallback_resolved += 1
                            logger.debug(f"[{fallback_source}] Resolved ID for {artist.artist_name}: {fallback_id}")
                        else:
                            fallback_failed_resolve += 1
                            logger.info(f"[{fallback_source}] No artist found for: {artist.artist_name}")
                    except Exception as e:
                        fallback_failed_resolve += 1
                        logger.info(f"[{fallback_source}] Failed to resolve {artist.artist_name}: {e}")

                if fallback_id:
                    try:
                        albums = itunes_api_call_with_retry(
                            itunes_client.get_artist_albums, fallback_id, album_type='album,single,ep', limit=20
                        )
                        for album in albums or []:
                            process_album(
                                album, artist.artist_name, artist.spotify_artist_id,
                                fallback_id if fallback_source == 'itunes' else None,
                                fallback_source,
                                artist_deezer_id=fallback_id if fallback_source == 'deezer' else None
                            )
                    except Exception as e:
                        logger.info(f"[{fallback_source}] Error fetching albums for {artist.artist_name}: {e}")

                # Process Spotify if authenticated
                if spotify_available and artist.spotify_artist_id:
                    try:
                        albums = self.spotify_client.get_artist_albums(
                            artist.spotify_artist_id,
                            album_type='album,single,ep',
                            limit=20,
                            skip_cache=True,
                            max_pages=2,
                        )
                        for album in albums or []:
                            process_album(album, artist.artist_name, artist.spotify_artist_id, fallback_id if fallback_source == 'itunes' else None, 'spotify')
                    except Exception as e:
                        logger.debug(f"Error fetching Spotify albums for {artist.artist_name}: {e}")

                time.sleep(DELAY_BETWEEN_ARTISTS)

            # Process similar artists
            for artist in similar_artists:
                # Always process fallback source (iTunes or Deezer) as baseline
                fallback_id = artist.similar_artist_itunes_id if fallback_source == 'itunes' else getattr(artist, 'similar_artist_deezer_id', None)
                if not fallback_id:
                    # Try to resolve fallback ID on-the-fly (with retry for rate limiting)
                    try:
                        results = itunes_api_call_with_retry(
                            itunes_client.search_artists, artist.similar_artist_name, limit=1
                        )
                        if results and len(results) > 0:
                            fallback_id = results[0].id
                            # Cache for future
                            if fallback_source == 'deezer':
                                self.database.update_similar_artist_deezer_id(artist.id, fallback_id)
                            else:
                                self.database.update_similar_artist_itunes_id(artist.id, fallback_id)
                            fallback_resolved += 1
                            logger.debug(f"[{fallback_source}] Resolved ID for similar artist {artist.similar_artist_name}: {fallback_id}")
                        else:
                            fallback_failed_resolve += 1
                            logger.info(f"[{fallback_source}] No artist found for similar: {artist.similar_artist_name}")
                    except Exception as e:
                        fallback_failed_resolve += 1
                        logger.info(f"[{fallback_source}] Failed to resolve similar {artist.similar_artist_name}: {e}")

                if fallback_id:
                    try:
                        albums = itunes_api_call_with_retry(
                            itunes_client.get_artist_albums, fallback_id, album_type='album,single,ep', limit=20
                        )
                        for album in albums or []:
                            process_album(
                                album, artist.similar_artist_name, artist.similar_artist_spotify_id,
                                fallback_id if fallback_source == 'itunes' else None,
                                fallback_source,
                                artist_deezer_id=fallback_id if fallback_source == 'deezer' else None
                            )
                    except Exception as e:
                        logger.info(f"[{fallback_source}] Error fetching albums for similar {artist.similar_artist_name}: {e}")

                # Process Spotify if authenticated
                if spotify_available and artist.similar_artist_spotify_id:
                    try:
                        albums = self.spotify_client.get_artist_albums(
                            artist.similar_artist_spotify_id,
                            album_type='album,single,ep',
                            limit=20,
                            skip_cache=True,
                            max_pages=2,
                        )
                        for album in albums or []:
                            process_album(album, artist.similar_artist_name, artist.similar_artist_spotify_id, fallback_id if fallback_source == 'itunes' else None, 'spotify')
                    except Exception as e:
                        logger.debug(f"Error fetching Spotify albums for {artist.similar_artist_name}: {e}")

                time.sleep(DELAY_BETWEEN_ARTISTS)

            total_cached = cached_count['spotify'] + cached_count.get(fallback_source, 0)
            logger.info(f"Cached {total_cached} recent albums (Spotify: {cached_count['spotify']}, {fallback_source}: {cached_count.get(fallback_source, 0)}) from {albums_checked} albums checked")
            logger.info(f"[{fallback_source}] ID resolution stats: {fallback_resolved} resolved, {fallback_failed_resolve} failed")

        except Exception as e:
            logger.error(f"Error caching discovery recent albums: {e}")
            import traceback
            traceback.print_exc()

    def _get_listening_profile(self, profile_id: int = 1) -> dict:
        """Build a listening profile from the user's play history for personalized discovery.

        Returns a dict with top artists, genres, listening velocity, etc.
        Falls back to empty/default values if no listening data exists.
        """
        try:
            stats = self.database.get_listening_stats('30d')
            if not stats or stats.get('total_plays', 0) == 0:
                return {'has_data': False, 'top_artist_names': set(), 'top_genres': set(),
                        'genre_weights': {}, 'artist_play_counts': {}, 'avg_daily_plays': 0, 'listening_diversity': 0}

            top_artists = self.database.get_top_artists('30d', 20)
            top_artist_names = {a['name'].lower() for a in top_artists}

            # Build play count lookup for artist penalty scoring
            artist_play_counts = {a['name'].lower(): a['play_count'] for a in top_artists}

            genre_breakdown = self.database.get_genre_breakdown('30d')
            top_genres = {g['genre'].lower() for g in genre_breakdown[:5]} if genre_breakdown else set()
            genre_weights = {g['genre'].lower(): g['percentage'] for g in genre_breakdown} if genre_breakdown else {}

            return {
                'has_data': True,
                'top_artist_names': top_artist_names,
                'artist_play_counts': artist_play_counts,
                'top_genres': top_genres,
                'genre_weights': genre_weights,
                'avg_daily_plays': stats.get('total_plays', 0) / 30,
                'listening_diversity': stats.get('unique_artists', 0),
            }
        except Exception as e:
            logger.debug(f"Could not build listening profile: {e}")
            return {'has_data': False, 'top_artist_names': set(), 'top_genres': set(),
                    'genre_weights': {}, 'avg_daily_plays': 0, 'listening_diversity': 0}

    def curate_discovery_playlists(self, profile_id: int = 1):
        """
        Curate consistent playlist selections that stay the same until next discovery pool update.

        Supports both Spotify and iTunes sources - creates separate curated playlists for each.
        - Release Radar: Prioritizes freshness + popularity from recent releases
        - Discovery Weekly: Balanced mix of popular picks, deep cuts, and mid-tier tracks

        Uses listening stats (if available) to personalize scoring.
        """
        try:
            import random
            from datetime import datetime

            logger.info("Curating discovery playlists...")

            if self.spotify_client and self.spotify_client.is_rate_limited():
                self._disable_spotify_for_run("global Spotify rate limit active")

            # Build listening profile for personalization
            profile = self._get_listening_profile(profile_id)
            if profile['has_data']:
                logger.info(f"Listening profile: {len(profile['top_artist_names'])} top artists, "
                           f"{len(profile['top_genres'])} top genres, "
                           f"{profile['avg_daily_plays']:.1f} avg daily plays")

            # Determine available sources
            spotify_available = self._spotify_is_primary_source()
            itunes_client, fallback_source = _get_fallback_metadata_client()

            # Process each available source
            sources_to_process = [fallback_source]  # Fallback source (iTunes/Deezer) always available
            if spotify_available:
                sources_to_process.append('spotify')

            # Pre-build artist genre cache from local DB for genre affinity scoring
            _artist_genre_cache = {}
            if profile['has_data']:
                try:
                    import json as _json
                    _conn = self.database._get_connection()
                    _cur = _conn.cursor()
                    _cur.execute("SELECT name, genres FROM artists WHERE genres IS NOT NULL AND genres != ''")
                    for _row in _cur.fetchall():
                        if not _row[0]:
                            continue
                        try:
                            _parsed = _json.loads(_row[1])
                            if isinstance(_parsed, list):
                                _artist_genre_cache[_row[0].lower()] = {g.lower() for g in _parsed if g}
                        except (ValueError, TypeError):
                            _artist_genre_cache[_row[0].lower()] = {g.strip().lower() for g in _row[1].split(',') if g.strip()}
                    _conn.close()
                    logger.debug(f"Built genre cache for {len(_artist_genre_cache)} artists")
                except Exception:
                    pass

            logger.info(f"Curating playlists for sources: {sources_to_process}")

            for source in sources_to_process:
                logger.info(f"Curating Release Radar for {source}...")

                # 1. Curate Release Radar - 50 tracks from recent albums
                recent_albums = self.database.get_discovery_recent_albums(limit=50, source=source, profile_id=profile_id)
                release_radar_tracks = []

                if not recent_albums:
                    logger.warning(f"[{source.upper()}] No recent albums found for Release Radar - check cache_discovery_recent_albums()")

                if recent_albums:
                    # Group albums by artist for variety
                    albums_by_artist = {}
                    for album in recent_albums:
                        artist = album['artist_name']
                        if artist not in albums_by_artist:
                            albums_by_artist[artist] = []
                        albums_by_artist[artist].append(album)

                    # Get tracks from each album
                    artist_track_data = {}

                    for artist, albums in albums_by_artist.items():
                        artist_track_data[artist] = []

                        for album in albums:
                            try:
                                # Get album data from appropriate source
                                if source == 'spotify':
                                    album_id = album.get('album_spotify_id')
                                elif source == 'deezer':
                                    album_id = album.get('album_deezer_id')
                                else:
                                    album_id = album.get('album_itunes_id')
                                if not album_id:
                                    continue

                                if source == 'spotify':
                                    album_data = self.spotify_client.get_album(album_id)
                                else:
                                    album_data = itunes_api_call_with_retry(
                                        itunes_client.get_album, album_id
                                    )

                                if not album_data or 'tracks' not in album_data:
                                    continue

                                # Calculate days since release for recency score
                                days_old = 14
                                try:
                                    release_date_str = album.get('release_date', '')
                                    if release_date_str and len(release_date_str) >= 10:
                                        release_date = datetime.strptime(release_date_str[:10], "%Y-%m-%d")
                                        days_old = (datetime.now() - release_date).days
                                except:
                                    pass

                                for track in album_data['tracks'].get('items', []):
                                    track_id = track.get('id')
                                    if not track_id:
                                        continue

                                    # Calculate track score
                                    recency_score = max(0, 100 - (days_old * 7))
                                    popularity_score = track.get('popularity', album_data.get('popularity', 0))
                                    # iTunes/Deezer have no popularity — use recency-based synthetic score
                                    if source in ('itunes', 'deezer') and popularity_score == 0:
                                        popularity_score = max(40, 70 - days_old)
                                    is_single = album.get('album_type', 'album') == 'single'
                                    single_bonus = 20 if is_single else 0

                                    # Personalization bonuses (from listening profile)
                                    genre_bonus = 0
                                    artist_bonus = 0
                                    overplay_penalty = 0
                                    if profile['has_data']:
                                        artist_lower = artist.lower()
                                        # Genre affinity: check album/API genres, then use cached DB genres
                                        artist_genres_lower = {g.lower() for g in (album.get('genres') or album_data.get('genres') or [])}
                                        if not artist_genres_lower:
                                            artist_genres_lower = _artist_genre_cache.get(artist_lower, set())
                                        if artist_genres_lower & profile['top_genres']:
                                            genre_bonus = 10
                                        # Artist familiarity: boost tracks from artists user listens to
                                        if artist_lower in profile['top_artist_names']:
                                            artist_bonus = 15
                                        # Overplay penalty: reduce score for artists user has heard too much
                                        if profile['artist_play_counts'].get(artist_lower, 0) > 20:
                                            overplay_penalty = -10

                                    total_score = (recency_score * 0.45) + (popularity_score * 0.25) + single_bonus + genre_bonus + artist_bonus + overplay_penalty

                                    full_track = {
                                        'id': track_id,
                                        'name': track.get('name', 'Unknown'),
                                        'artists': track.get('artists', [{'name': artist}]),
                                        'album': {
                                            'id': album_data.get('id', ''),
                                            'name': album_data.get('name', 'Unknown Album'),
                                            'images': album_data.get('images', []),
                                            'release_date': album_data.get('release_date', ''),
                                            'album_type': album_data.get('album_type', 'album'),
                                        },
                                        'duration_ms': track.get('duration_ms', 0),
                                        'popularity': popularity_score,
                                        'score': total_score,
                                        'source': source
                                    }
                                    artist_track_data[artist].append(full_track)

                            except Exception as e:
                                logger.debug(f"Error processing album for {artist}: {e}")
                                continue

                    # Balance by artist - max 6 tracks per artist
                    balanced_track_data = []
                    for artist, tracks in artist_track_data.items():
                        sorted_tracks = sorted(tracks, key=lambda t: t['score'], reverse=True)
                        balanced_track_data.extend(sorted_tracks[:6])

                    # Sort by score and shuffle
                    balanced_track_data.sort(key=lambda t: t['score'], reverse=True)
                    top_tracks = balanced_track_data[:75]
                    random.shuffle(top_tracks)

                    # Take final 50 tracks
                    release_radar_tracks = [track['id'] for track in top_tracks[:50]]

                    # Add tracks to discovery pool
                    for track_data in top_tracks[:50]:
                        try:
                            artist_name = track_data['artists'][0].get('name', 'Unknown') if track_data['artists'] else 'Unknown'
                            formatted_track = {
                                'track_name': track_data['name'],
                                'artist_name': artist_name,
                                'album_name': track_data['album'].get('name', 'Unknown'),
                                'album_cover_url': track_data['album']['images'][0]['url'] if track_data['album'].get('images') else None,
                                'duration_ms': track_data.get('duration_ms', 0),
                                'popularity': track_data.get('popularity', 0),
                                'release_date': track_data['album'].get('release_date', ''),
                                'is_new_release': True,
                                'track_data_json': track_data,
                                'artist_genres': []
                            }
                            if source == 'spotify':
                                formatted_track['spotify_track_id'] = track_data['id']
                                formatted_track['spotify_album_id'] = track_data['album'].get('id', '')
                            elif source == 'deezer':
                                formatted_track['deezer_track_id'] = track_data['id']
                                formatted_track['deezer_album_id'] = track_data['album'].get('id', '')
                            else:
                                formatted_track['itunes_track_id'] = track_data['id']
                                formatted_track['itunes_album_id'] = track_data['album'].get('id', '')

                            self.database.add_to_discovery_pool(formatted_track, source=source, profile_id=profile_id)
                        except Exception as e:
                            continue

                # Save with source suffix for multi-source support
                playlist_key = f'release_radar_{source}'
                self.database.save_curated_playlist(playlist_key, release_radar_tracks, profile_id=profile_id)
                logger.info(f"Release Radar ({source}) curated: {len(release_radar_tracks)} tracks")

                # 2. Curate Discovery Weekly - 50 tracks from discovery pool
                logger.info(f"Curating Discovery Weekly for {source}...")
                discovery_tracks = self.database.get_discovery_pool_tracks(limit=2000, new_releases_only=False, source=source, profile_id=profile_id)

                if not discovery_tracks:
                    logger.warning(f"[{source.upper()}] No discovery pool tracks found for Discovery Weekly - check populate_discovery_pool()")

                discovery_weekly_tracks = []
                if discovery_tracks:
                    # Separate tracks by popularity tiers
                    popular_picks = []
                    balanced_mix = []
                    deep_cuts = []

                    for track in discovery_tracks:
                        popularity = track.popularity if hasattr(track, 'popularity') else 50
                        if popularity >= 60:
                            popular_picks.append(track)
                        elif popularity >= 40:
                            balanced_mix.append(track)
                        else:
                            deep_cuts.append(track)

                    logger.info(f"Discovery pool ({source}): {len(popular_picks)} popular, {len(balanced_mix)} mid-tier, {len(deep_cuts)} deep cuts")

                    # Serendipity-weighted selection within each tier
                    def _serendipity_sort(tracks_list):
                        """Sort by serendipity: prefer unknown artists in genres user likes."""
                        if not profile['has_data']:
                            random.shuffle(tracks_list)
                            return tracks_list

                        for t in tracks_list:
                            score = 1.0
                            t_artist = (t.artist_name or '').lower()
                            t_genres = _artist_genre_cache.get(t_artist, set())

                            # Boost artists user has NEVER played (true discovery)
                            if t_artist not in profile['top_artist_names']:
                                score += 0.5
                            # Boost genres user likes but hasn't explored
                            if t_genres & profile['top_genres']:
                                score += 0.3
                            # Penalize artists user already plays heavily
                            if profile['artist_play_counts'].get(t_artist, 0) > 10:
                                score -= 0.4

                            t._serendipity = score + random.random() * 0.2  # Small random factor

                        tracks_list.sort(key=lambda t: getattr(t, '_serendipity', 1.0), reverse=True)
                        return tracks_list

                    _serendipity_sort(popular_picks)
                    _serendipity_sort(balanced_mix)
                    _serendipity_sort(deep_cuts)

                    selected_tracks = []
                    selected_tracks.extend(popular_picks[:20])
                    selected_tracks.extend(balanced_mix[:20])
                    selected_tracks.extend(deep_cuts[:10])
                    random.shuffle(selected_tracks)

                    # Extract appropriate track IDs based on source
                    for track in selected_tracks:
                        if source == 'spotify' and track.spotify_track_id:
                            discovery_weekly_tracks.append(track.spotify_track_id)
                        elif source == 'itunes' and track.itunes_track_id:
                            discovery_weekly_tracks.append(track.itunes_track_id)
                        elif source == 'deezer' and track.deezer_track_id:
                            discovery_weekly_tracks.append(track.deezer_track_id)

                playlist_key = f'discovery_weekly_{source}'
                self.database.save_curated_playlist(playlist_key, discovery_weekly_tracks, profile_id=profile_id)
                logger.info(f"Discovery Weekly ({source}) curated: {len(discovery_weekly_tracks)} tracks")

            # 3. "Because You Listen To" — personalized sections based on top played artists
            if profile['has_data']:
                logger.info("Building 'Because You Listen To' playlists...")
                top_played = self.database.get_top_artists('30d', 3)
                active_source_for_bylt = 'spotify' if spotify_available else fallback_source
                all_pool_tracks = self.database.get_discovery_pool_tracks(
                    limit=2000, new_releases_only=False,
                    source=active_source_for_bylt, profile_id=profile_id
                )

                # Build source_artist_id → artist_name mapping from watchlist
                _wa_id_to_name = {}
                try:
                    _wa_list = self.database.get_watchlist_artists(profile_id=profile_id)
                    for _wa in _wa_list:
                        _wa_id_to_name[str(_wa.id)] = (_wa.artist_name or '').lower()
                except Exception:
                    pass

                all_similar = self.database.get_top_similar_artists(limit=200, profile_id=profile_id)

                for i, played_artist in enumerate(top_played):
                    try:
                        artist_name = played_artist['name']
                        artist_lower = artist_name.lower()

                        # Find similar artists to this played artist via the similar_artists table
                        similar_names = set()
                        for s in all_similar:
                            # Check if this similar artist's source matches our played artist
                            src_id = str(getattr(s, 'source_artist_id', ''))
                            src_name = _wa_id_to_name.get(src_id, '')
                            sim_name = getattr(s, 'similar_artist_name', '') or ''
                            if src_name == artist_lower and sim_name:
                                similar_names.add(sim_name.lower())

                        if not similar_names:
                            # Fallback: find pool tracks from same genre
                            played_genres = _artist_genre_cache.get(artist_lower, set())
                            if played_genres:
                                for t in all_pool_tracks:
                                    t_artist_lower = (t.artist_name or '').lower()
                                    if t_artist_lower != artist_lower and _artist_genre_cache.get(t_artist_lower, set()) & played_genres:
                                        similar_names.add(t_artist_lower)
                                    if len(similar_names) >= 20:
                                        break

                        if not similar_names:
                            continue

                        # Pick tracks from those similar artists in the pool
                        matching_tracks = []
                        for t in all_pool_tracks:
                            if (t.artist_name or '').lower() in similar_names:
                                if active_source_for_bylt == 'spotify' and t.spotify_track_id:
                                    matching_tracks.append(t.spotify_track_id)
                                elif active_source_for_bylt == 'itunes' and t.itunes_track_id:
                                    matching_tracks.append(t.itunes_track_id)
                                elif active_source_for_bylt == 'deezer' and t.deezer_track_id:
                                    matching_tracks.append(t.deezer_track_id)

                            if len(matching_tracks) >= 15:
                                break

                        if matching_tracks:
                            import random as _rnd
                            _rnd.shuffle(matching_tracks)
                            playlist_key = f'because_you_listen_to_{i}'
                            self.database.save_curated_playlist(playlist_key, matching_tracks[:10], profile_id=profile_id)
                            # Store the source artist name in metadata
                            self.database.set_metadata(f'bylt_artist_{i}', artist_name)
                            logger.info(f"'Because You Listen To {artist_name}': {len(matching_tracks[:10])} tracks")
                    except Exception as e:
                        logger.debug(f"Error building BYLT for {played_artist.get('name', '?')}: {e}")

            # Also save without suffix for backward compatibility (use active source)
            active_source = 'spotify' if spotify_available else fallback_source
            release_radar_key = f'release_radar_{active_source}'
            discovery_weekly_key = f'discovery_weekly_{active_source}'

            # Copy active source playlists to non-suffixed keys
            release_radar_ids = self.database.get_curated_playlist(release_radar_key, profile_id=profile_id) or []
            discovery_weekly_ids = self.database.get_curated_playlist(discovery_weekly_key, profile_id=profile_id) or []
            self.database.save_curated_playlist('release_radar', release_radar_ids, profile_id=profile_id)
            self.database.save_curated_playlist('discovery_weekly', discovery_weekly_ids, profile_id=profile_id)

            logger.info("Playlist curation complete")

        except Exception as e:
            logger.error(f"Error curating discovery playlists: {e}")
            import traceback
            traceback.print_exc()

    def sync_spotify_library_cache(self, profile_id=1):
        """Sync user's saved Spotify albums into the local cache.

        Runs after the main watchlist scan. First sync fetches all saved albums;
        subsequent syncs are incremental (only fetch newly saved albums).
        Every 7 days, does a full re-sync to detect un-saved albums.
        """
        if not self._spotify_available_for_run():
            logger.debug("Spotify not authenticated, skipping library cache sync")
            return

        logger.info("Syncing Spotify library cache...")

        try:
            last_sync = self.database.get_metadata('spotify_library_last_sync')
            last_full_sync = self.database.get_metadata('spotify_library_last_full_sync')

            # Determine if we need a full sync (first time or every 7 days)
            do_full_sync = False
            if not last_sync:
                do_full_sync = True
                logger.info("First-time Spotify library sync — fetching all saved albums")
            elif not last_full_sync:
                # last_sync exists but last_full_sync doesn't — first run with this code
                do_full_sync = True
                logger.info("Full re-sync triggered (no full sync recorded)")
            else:
                try:
                    last_full_dt = datetime.fromisoformat(last_full_sync)
                    if datetime.now() - last_full_dt > timedelta(days=7):
                        do_full_sync = True
                        logger.info("Full re-sync triggered (>7 days since last full sync)")
                except (ValueError, TypeError):
                    do_full_sync = True

            # Fetch albums from Spotify
            since_timestamp = None if do_full_sync else last_sync
            albums = self.spotify_client.get_saved_albums(since_timestamp=since_timestamp)

            if not albums and not do_full_sync:
                logger.info("No new saved albums since last sync")
                return

            if albums:
                self.database.upsert_spotify_library_albums(albums, profile_id=profile_id)

            # On full sync, remove albums that are no longer saved
            if do_full_sync and albums:
                fetched_ids = {a['spotify_album_id'] for a in albums}
                self.database.remove_spotify_library_albums_not_in(fetched_ids, profile_id=profile_id)
                self.database.set_metadata('spotify_library_last_full_sync', datetime.now().isoformat())

            # Update last sync timestamp
            self.database.set_metadata('spotify_library_last_sync', datetime.now().isoformat())

            logger.info(f"Spotify library cache sync complete — {len(albums)} albums processed")

        except Exception as e:
            logger.error(f"Error syncing Spotify library cache: {e}")

    def _populate_seasonal_content(self):
        """
        Populate seasonal content as part of watchlist scan.

        IMPROVED: Integrated with discovery system
        - Checks if seasonal content needs update (7-day threshold)
        - Populates content for all seasons
        - Curates seasonal playlists
        - Runs once per week automatically
        """
        try:
            from core.seasonal_discovery import get_seasonal_discovery_service

            logger.info("Checking seasonal content update...")

            seasonal_service = get_seasonal_discovery_service(self.spotify_client, self.database)

            # Get current season to prioritize
            current_season = seasonal_service.get_current_season()

            if current_season:
                # Always update current season if needed
                if seasonal_service.should_populate_seasonal_content(current_season, days_threshold=7):
                    logger.info(f"Populating current season: {current_season}")
                    seasonal_service.populate_seasonal_content(current_season)
                    seasonal_service.curate_seasonal_playlist(current_season)
                else:
                    logger.info(f"Current season '{current_season}' is up to date")

            # Update other seasons in background (less frequently - 14 day threshold)
            from core.seasonal_discovery import SEASONAL_CONFIG
            for season_key in SEASONAL_CONFIG.keys():
                if season_key == current_season:
                    continue  # Already handled above

                if seasonal_service.should_populate_seasonal_content(season_key, days_threshold=14):
                    logger.info(f"Populating season: {season_key}")
                    seasonal_service.populate_seasonal_content(season_key)
                    seasonal_service.curate_seasonal_playlist(season_key)

            logger.info("Seasonal content update complete")

        except Exception as e:
            logger.error(f"Error populating seasonal content: {e}")
            import traceback
            traceback.print_exc()

    def _generate_lastfm_radio_playlists(self):
        """Generate Last.fm Radio playlists from the user's top 3 most-played tracks.

        Runs at most once per week (throttled via config key 'lastfm_radio.last_generated').
        Requires a Last.fm API key to be configured.
        Stores playlists in DB under playlist_type='lastfm_radio' via ListenBrainzManager.
        """
        try:
            from datetime import datetime, timedelta
            from config.settings import config_manager
            from database.music_database import get_database

            # Weekly throttle
            last_generated_str = config_manager.get('lastfm_radio.last_generated', '')
            if last_generated_str:
                try:
                    last_generated = datetime.fromisoformat(last_generated_str)
                    if datetime.now() - last_generated < timedelta(days=7):
                        logger.info("Last.fm radio: skipping — generated within the last 7 days")
                        return
                except ValueError:
                    pass  # Malformed timestamp — proceed

            # Require Last.fm API key
            api_key = config_manager.get('lastfm.api_key', '')
            if not api_key:
                logger.info("Last.fm radio: skipping — no API key configured")
                return

            # Get top 3 most-played tracks over the last 30 days
            db = get_database()
            top_tracks = db.get_top_tracks(time_range='30d', limit=3)
            if not top_tracks:
                logger.info("Last.fm radio: skipping — no listening history found")
                return

            logger.info(f"Last.fm radio: generating playlists for {len(top_tracks)} top tracks")

            from core.lastfm_client import LastFMClient
            from core.listenbrainz_manager import ListenBrainzManager

            client = LastFMClient(api_key=api_key)
            # Use profile_id=1 as a sensible default; the scanner runs globally
            lb_manager = ListenBrainzManager(str(db.database_path), profile_id=1)

            generated = 0
            for track in top_tracks:
                track_name = track.get('name', '')
                artist_name = track.get('artist', '')
                if not track_name or not artist_name:
                    continue

                try:
                    similar = client.get_similar_tracks(artist_name, track_name, limit=25)
                    if not similar:
                        logger.info(f"Last.fm radio: no similar tracks for '{artist_name} - {track_name}'")
                        continue

                    playlist_mbid = lb_manager.save_lastfm_radio_playlist(track_name, artist_name, similar)
                    logger.info(
                        f"Last.fm radio: saved '{track_name}' by '{artist_name}' "
                        f"→ {playlist_mbid} ({len(similar)} tracks)"
                    )
                    generated += 1
                except Exception as track_err:
                    logger.warning(f"Last.fm radio: error processing '{track_name}': {track_err}")

            if generated > 0:
                config_manager.set('lastfm_radio.last_generated', datetime.now().isoformat())
                logger.info(f"Last.fm radio: generated {generated} playlists, throttle updated")

        except Exception as e:
            logger.error(f"Error in _generate_lastfm_radio_playlists: {e}")
            import traceback
            traceback.print_exc()

# Singleton instance
_watchlist_scanner_instance = None

def get_watchlist_scanner(spotify_client: SpotifyClient) -> WatchlistScanner:
    """Get the global watchlist scanner instance"""
    global _watchlist_scanner_instance
    if _watchlist_scanner_instance is None:
        _watchlist_scanner_instance = WatchlistScanner(spotify_client)
    return _watchlist_scanner_instance
