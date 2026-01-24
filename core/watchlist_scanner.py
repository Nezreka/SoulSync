#!/usr/bin/env python3

"""
Watchlist Scanner Service - Monitors watched artists for new releases
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
import re
import time
import requests
from bs4 import BeautifulSoup
from database.music_database import get_database, WatchlistArtist
from core.spotify_client import SpotifyClient
from core.wishlist_service import get_wishlist_service
from core.matching_engine import MusicMatchingEngine
from utils.logging_config import get_logger

logger = get_logger("watchlist_scanner")

# Rate limiting constants for watchlist operations
DELAY_BETWEEN_ARTISTS = 2.0      # 2 seconds between different artists
DELAY_BETWEEN_ALBUMS = 0.5       # 500ms between albums for same artist  
DELAY_BETWEEN_API_BATCHES = 1.0  # 1 second between API batch operations

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
        logger.debug(f"🧹 Intelligent track cleaning: '{track_name}' -> '{cleaned_name}'")
    
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

class WatchlistScanner:
    """Service for scanning watched artists for new releases"""
    
    def __init__(self, spotify_client: SpotifyClient = None, metadata_service=None, database_path: str = "database/music_library.db"):
        # Support both old (spotify_client) and new (metadata_service) initialization
        self.database_path = database_path
        self._database = None
        self._wishlist_service = None
        self._matching_engine = None
        
        if metadata_service:
            self._metadata_service = metadata_service
            self.spotify_client = metadata_service.spotify  # For backward compatibility
        elif spotify_client:
            self.spotify_client = spotify_client
            self._metadata_service = None  # Lazy load if needed
        else:
            raise ValueError("Must provide either spotify_client or metadata_service")
    
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

    def _get_active_client_and_artist_id(self, watchlist_artist: WatchlistArtist):
        """
        Get the appropriate client and artist ID based on active provider.
        If iTunes ID is missing, searches by artist name to find and cache it.

        Returns:
            Tuple of (client, artist_id, provider_name) or (None, None, None) if no valid ID
        """
        provider = self.metadata_service.get_active_provider()

        if provider == 'spotify':
            if watchlist_artist.spotify_artist_id:
                return (self.metadata_service.spotify, watchlist_artist.spotify_artist_id, 'spotify')
            else:
                logger.warning(f"No Spotify ID for {watchlist_artist.artist_name}, cannot scan with Spotify")
                return (None, None, None)
        else:  # itunes
            if watchlist_artist.itunes_artist_id:
                return (self.metadata_service.itunes, watchlist_artist.itunes_artist_id, 'itunes')
            else:
                # No iTunes ID stored - search by name and cache it
                logger.info(f"No iTunes ID for {watchlist_artist.artist_name}, searching by name...")
                try:
                    itunes_client = self.metadata_service.itunes
                    search_results = itunes_client.search_artists(watchlist_artist.artist_name, limit=1)
                    if search_results and len(search_results) > 0:
                        itunes_id = search_results[0].id
                        logger.info(f"Found iTunes ID {itunes_id} for {watchlist_artist.artist_name}")
                        # Cache the iTunes ID in the database for future use
                        self.database.update_watchlist_artist_itunes_id(
                            watchlist_artist.spotify_artist_id or str(watchlist_artist.id),
                            itunes_id
                        )
                        return (itunes_client, itunes_id, 'itunes')
                    else:
                        logger.warning(f"Could not find {watchlist_artist.artist_name} on iTunes")
                        return (None, None, None)
                except Exception as e:
                    logger.error(f"Error searching iTunes for {watchlist_artist.artist_name}: {e}")
                    return (None, None, None)

    def get_active_client_and_artist_id(self, watchlist_artist: WatchlistArtist):
        """
        Public wrapper for _get_active_client_and_artist_id.
        Gets the appropriate client and artist ID based on active provider.

        Returns:
            Tuple of (client, artist_id, provider_name) or (None, None, None) if no valid ID
        """
        return self._get_active_client_and_artist_id(watchlist_artist)

    def get_artist_image_url(self, watchlist_artist: WatchlistArtist) -> Optional[str]:
        """
        Get artist image URL using the active provider.

        Returns:
            Image URL string or None if not available
        """
        client, artist_id, provider = self._get_active_client_and_artist_id(watchlist_artist)
        if not client or not artist_id:
            return None

        try:
            artist_data = client.get_artist(artist_id)
            if artist_data:
                # Handle both Spotify and iTunes response formats
                if 'images' in artist_data and artist_data['images']:
                    return artist_data['images'][0].get('url')
                elif 'image_url' in artist_data:
                    return artist_data['image_url']
        except Exception as e:
            logger.debug(f"Could not fetch artist image for {watchlist_artist.artist_name}: {e}")

        return None

    def get_artist_discography_for_watchlist(self, watchlist_artist: WatchlistArtist, last_scan_timestamp: Optional[datetime] = None) -> Optional[List]:
        """
        Get artist's discography using the active provider, with proper ID resolution.
        This is the provider-aware version of get_artist_discography.

        Args:
            watchlist_artist: WatchlistArtist object (has both spotify and itunes IDs)
            last_scan_timestamp: Only return releases after this date (for incremental scans)

        Returns:
            List of albums or None on error
        """
        client, artist_id, provider = self._get_active_client_and_artist_id(watchlist_artist)
        if not client or not artist_id:
            logger.warning(f"No valid client/ID for {watchlist_artist.artist_name}")
            return None

        return self._get_artist_discography_with_client(client, artist_id, last_scan_timestamp)

    def scan_all_watchlist_artists(self) -> List[ScanResult]:
        """
        Scan artists in the watchlist for new releases.

        OPTIMIZED: Scans up to 50 artists per run using smart selection:
        - Priority: Artists not scanned in 7+ days (guaranteed)
        - Remainder: Random selection from other artists

        This reduces API calls while ensuring all artists scanned at least weekly.
        Only checks releases after their last scan timestamp.
        """
        logger.info("Starting watchlist scan")

        try:
            from datetime import datetime, timedelta
            import random

            # Get all watchlist artists
            all_watchlist_artists = self.database.get_watchlist_artists()
            if not all_watchlist_artists:
                logger.info("No artists in watchlist to scan")
                return []

            logger.info(f"Found {len(all_watchlist_artists)} total artists in watchlist")

            # OPTIMIZATION: Select up to 50 artists to scan
            # 1. Must scan: Artists not scanned in 7+ days (or never scanned)
            seven_days_ago = datetime.now() - timedelta(days=7)
            must_scan = []
            can_skip = []

            for artist in all_watchlist_artists:
                if artist.last_scan_timestamp is None:
                    # Never scanned - must scan
                    must_scan.append(artist)
                elif artist.last_scan_timestamp < seven_days_ago:
                    # Not scanned in 7+ days - must scan
                    must_scan.append(artist)
                else:
                    # Scanned recently - can skip (but might randomly select)
                    can_skip.append(artist)

            logger.info(f"Artists requiring scan (not scanned in 7+ days): {len(must_scan)}")
            logger.info(f"Artists scanned recently (< 7 days): {len(can_skip)}")

            # 2. Fill remaining slots (up to 50 total) with random selection
            max_artists_per_scan = 50
            artists_to_scan = must_scan.copy()

            remaining_slots = max_artists_per_scan - len(must_scan)
            if remaining_slots > 0 and can_skip:
                # Randomly sample from recently-scanned artists
                random_sample_size = min(remaining_slots, len(can_skip))
                random_selection = random.sample(can_skip, random_sample_size)
                artists_to_scan.extend(random_selection)
                logger.info(f"Additionally scanning {len(random_selection)} randomly selected artists")

            # Shuffle to avoid always scanning same order
            random.shuffle(artists_to_scan)

            logger.info(f"Total artists to scan this run: {len(artists_to_scan)}")
            if len(all_watchlist_artists) > max_artists_per_scan:
                logger.info(f"Skipping {len(all_watchlist_artists) - len(artists_to_scan)} artists (will be scanned in future runs)")

            watchlist_artists = artists_to_scan
            
            # PROACTIVE ID BACKFILLING (cross-provider support)
            # Before scanning, ensure all artists have IDs for the current provider
            logger.info(f"DEBUG: About to check backfilling. _metadata_service = {getattr(self, '_metadata_service', 'ATTRIBUTE MISSING')}")
            if self._metadata_service is not None:
                try:
                    active_provider = self._metadata_service.get_active_provider()
                    logger.info(f"🔍 Checking for missing {active_provider} IDs in watchlist...")
                    self._backfill_missing_ids(all_watchlist_artists, active_provider)
                except Exception as backfill_error:
                    logger.warning(f"Error during ID backfilling: {backfill_error}")
                    import traceback
                    traceback.print_exc()
                    # Continue with scan even if backfilling fails
            else:
                logger.warning(f"⚠️ Backfilling SKIPPED - _metadata_service is None")
            
            scan_results = []
            for i, artist in enumerate(watchlist_artists):
                try:
                    result = self.scan_artist(artist)
                    scan_results.append(result)
                    
                    if result.success:
                        logger.info(f"✅ Scanned {artist.artist_name}: {result.new_tracks_found} new tracks found")
                    else:
                        logger.warning(f"❌ Failed to scan {artist.artist_name}: {result.error_message}")
                    
                    # Rate limiting: Add delay between artists to avoid hitting Spotify API limits
                    # This is critical to prevent getting banned for 6+ hours
                    if i < len(watchlist_artists) - 1:  # Don't delay after the last artist
                        logger.debug(f"Rate limiting: waiting {DELAY_BETWEEN_ARTISTS}s before scanning next artist")
                        time.sleep(DELAY_BETWEEN_ARTISTS)
                
                except Exception as e:
                    logger.error(f"Error scanning artist {artist.artist_name}: {e}")
                    scan_results.append(ScanResult(
                        artist_name=artist.artist_name,
                        spotify_artist_id=artist.spotify_artist_id,
                        albums_checked=0,
                        new_tracks_found=0,
                        tracks_added_to_wishlist=0,
                        success=False,
                        error_message=str(e)
                    ))
            
            # Log summary
            successful_scans = [r for r in scan_results if r.success]
            total_new_tracks = sum(r.new_tracks_found for r in successful_scans)
            total_added_to_wishlist = sum(r.tracks_added_to_wishlist for r in successful_scans)
            
            logger.info(f"Watchlist scan complete: {len(successful_scans)}/{len(scan_results)} artists scanned successfully")
            logger.info(f"Found {total_new_tracks} new tracks, added {total_added_to_wishlist} to wishlist")

            # Populate discovery pool with tracks from similar artists
            logger.info("Starting discovery pool population...")
            self.populate_discovery_pool()

            # Populate seasonal content (runs independently with its own threshold)
            logger.info("Updating seasonal content...")
            self._populate_seasonal_content()

            return scan_results
            
        except Exception as e:
            logger.error(f"Error during watchlist scan: {e}")
            return []
    
    def scan_artist(self, watchlist_artist: WatchlistArtist) -> ScanResult:
        """
        Scan a single artist for new releases.
        Only checks releases after the last scan timestamp.
        Uses the active provider (Spotify if authenticated, otherwise iTunes).
        """
        try:
            logger.info(f"Scanning artist: {watchlist_artist.artist_name}")

            # Get the active client and artist ID based on provider
            client, artist_id, provider = self._get_active_client_and_artist_id(watchlist_artist)

            if client is None or artist_id is None:
                return ScanResult(
                    artist_name=watchlist_artist.artist_name,
                    spotify_artist_id=watchlist_artist.spotify_artist_id or '',
                    albums_checked=0,
                    new_tracks_found=0,
                    tracks_added_to_wishlist=0,
                    success=False,
                    error_message=f"No {self.metadata_service.get_active_provider()} ID available for this artist"
                )

            logger.info(f"Using {provider} provider for {watchlist_artist.artist_name} (ID: {artist_id})")

            # Update artist image (cached for performance)
            try:
                artist_data = client.get_artist(artist_id)
                if artist_data and 'images' in artist_data and artist_data['images']:
                    # Get medium-sized image (usually the second one, or first if only one)
                    image_url = None
                    if len(artist_data['images']) > 1:
                        image_url = artist_data['images'][1]['url']
                    else:
                        image_url = artist_data['images'][0]['url']

                    # Update in database (use spotify_artist_id as the key for consistency)
                    if image_url:
                        db_artist_id = watchlist_artist.spotify_artist_id or artist_id
                        self.database.update_watchlist_artist_image(db_artist_id, image_url)
                        logger.info(f"Updated artist image for {watchlist_artist.artist_name}")
                    else:
                        logger.warning(f"No image URL found for {watchlist_artist.artist_name}")
                else:
                    logger.warning(f"No images in {provider} data for {watchlist_artist.artist_name}")
            except Exception as img_error:
                logger.warning(f"Could not update artist image for {watchlist_artist.artist_name}: {img_error}")

            # Get artist discography using active provider
            albums = self._get_artist_discography_with_client(client, artist_id, watchlist_artist.last_scan_timestamp)

            if albums is None:
                return ScanResult(
                    artist_name=watchlist_artist.artist_name,
                    spotify_artist_id=watchlist_artist.spotify_artist_id or '',
                    albums_checked=0,
                    new_tracks_found=0,
                    tracks_added_to_wishlist=0,
                    success=False,
                    error_message=f"Failed to get artist discography from {provider}"
                )

            logger.info(f"Found {len(albums)} albums/singles to check for {watchlist_artist.artist_name}")

            # Safety check: Limit number of albums to scan to prevent extremely long sessions
            MAX_ALBUMS_PER_ARTIST = 50  # Reasonable limit to prevent API abuse
            if len(albums) > MAX_ALBUMS_PER_ARTIST:
                logger.warning(f"Artist {watchlist_artist.artist_name} has {len(albums)} albums, limiting to {MAX_ALBUMS_PER_ARTIST} most recent")
                albums = albums[:MAX_ALBUMS_PER_ARTIST]  # Most recent albums are first

            # Check each album/single for missing tracks
            new_tracks_found = 0
            tracks_added_to_wishlist = 0

            for album_index, album in enumerate(albums):
                try:
                    # Get full album data
                    logger.info(f"Checking album {album_index + 1}/{len(albums)}: {album.name}")
                    album_data = client.get_album(album.id)
                    if not album_data:
                        continue

                    # Get album tracks (works for both Spotify and iTunes)
                    # Spotify's get_album() includes tracks, but we use get_album_tracks() for consistency
                    tracks_data = client.get_album_tracks(album.id)
                    if not tracks_data or not tracks_data.get('items'):
                        continue

                    tracks = tracks_data['items']
                    logger.debug(f"Checking album: {album_data.get('name', 'Unknown')} ({len(tracks)} tracks)")

                    # Check if user wants this type of release
                    if not self._should_include_release(len(tracks), watchlist_artist):
                        release_type = "album" if len(tracks) >= 7 else ("EP" if len(tracks) >= 4 else "single")
                        logger.debug(f"Skipping {release_type}: {album_data.get('name', 'Unknown')} - user preference")
                        continue

                    # Check each track
                    for track in tracks:
                        # Check content type filters (live, remix, acoustic, compilation)
                        if not self._should_include_track(track, album_data, watchlist_artist):
                            continue  # Skip this track based on content type preferences

                        if self.is_track_missing_from_library(track):
                            new_tracks_found += 1

                            # Add to wishlist
                            if self.add_track_to_wishlist(track, album_data, watchlist_artist):
                                tracks_added_to_wishlist += 1
                    
                    # Rate limiting: Add delay between albums to prevent API abuse
                    # This is especially important for artists with many albums
                    if album_index < len(albums) - 1:  # Don't delay after the last album
                        logger.debug(f"Rate limiting: waiting {DELAY_BETWEEN_ALBUMS}s before next album")
                        time.sleep(DELAY_BETWEEN_ALBUMS)
                            
                except Exception as e:
                    logger.warning(f"Error checking album {album.name}: {e}")
                    continue
            
            # Update last scan timestamp for this artist (use spotify_artist_id as DB key for consistency)
            db_artist_id = watchlist_artist.spotify_artist_id or artist_id
            self.update_artist_scan_timestamp(db_artist_id)

            # Fetch and store similar artists for discovery feature (with caching to avoid over-polling)
            # Similar artists are fetched from MusicMap (works with any source) and matched to both Spotify and iTunes
            source_artist_id = watchlist_artist.spotify_artist_id or watchlist_artist.itunes_artist_id or str(watchlist_artist.id)
            try:
                # Check if we have fresh similar artists cached (< 30 days old)
                if self.database.has_fresh_similar_artists(source_artist_id, days_threshold=30):
                    logger.info(f"Similar artists for {watchlist_artist.artist_name} are cached and fresh, skipping MusicMap fetch")
                    # Even if cached, backfill missing iTunes IDs (seamless dual-source support)
                    self._backfill_similar_artists_itunes_ids(source_artist_id)
                else:
                    logger.info(f"Fetching similar artists for {watchlist_artist.artist_name}...")
                    self.update_similar_artists(watchlist_artist)
                    logger.info(f"Similar artists updated for {watchlist_artist.artist_name}")
            except Exception as similar_error:
                logger.warning(f"Failed to update similar artists for {watchlist_artist.artist_name}: {similar_error}")

            return ScanResult(
                artist_name=watchlist_artist.artist_name,
                spotify_artist_id=watchlist_artist.spotify_artist_id or '',
                albums_checked=len(albums),
                new_tracks_found=new_tracks_found,
                tracks_added_to_wishlist=tracks_added_to_wishlist,
                success=True
            )
            
        except Exception as e:
            logger.error(f"Error scanning artist {watchlist_artist.artist_name}: {e}")
            return ScanResult(
                artist_name=watchlist_artist.artist_name,
                spotify_artist_id=watchlist_artist.spotify_artist_id or '',
                albums_checked=0,
                new_tracks_found=0,
                tracks_added_to_wishlist=0,
                success=False,
                error_message=str(e)
            )
    
    def get_artist_discography(self, spotify_artist_id: str, last_scan_timestamp: Optional[datetime] = None) -> Optional[List]:
        """
        Get artist's discography from Spotify, optionally filtered by release date.

        Args:
            spotify_artist_id: Spotify artist ID
            last_scan_timestamp: Only return releases after this date (for incremental scans)
                                If None, uses lookback period setting from database
        """
        try:
            # Get all artist albums (albums + singles) - this is rate limited in spotify_client
            logger.debug(f"Fetching discography for artist {spotify_artist_id}")
            albums = self.spotify_client.get_artist_albums(spotify_artist_id, album_type='album,single', limit=50)

            if not albums:
                logger.warning(f"No albums found for artist {spotify_artist_id}")
                return []

            # Add small delay after fetching artist discography to be extra safe
            time.sleep(0.3)  # 300ms breathing room

            # Determine cutoff date for filtering
            cutoff_timestamp = last_scan_timestamp

            # If no last scan timestamp, use lookback period setting
            if cutoff_timestamp is None:
                lookback_period = self._get_lookback_period_setting()
                if lookback_period != 'all':
                    # Convert period to days and create cutoff date (use UTC)
                    days = int(lookback_period)
                    cutoff_timestamp = datetime.now(timezone.utc) - timedelta(days=days)
                    logger.info(f"Using lookback period: {lookback_period} days (cutoff: {cutoff_timestamp})")

            # Filter by release date if we have a cutoff timestamp
            if cutoff_timestamp:
                filtered_albums = []
                for album in albums:
                    if self.is_album_after_timestamp(album, cutoff_timestamp):
                        filtered_albums.append(album)

                logger.info(f"Filtered {len(albums)} albums to {len(filtered_albums)} released after {cutoff_timestamp}")
                return filtered_albums

            # Return all albums if no cutoff (lookback_period = 'all')
            return albums
            
        except Exception as e:
            logger.error(f"Error getting discography for artist {spotify_artist_id}: {e}")
            return None

    def _get_artist_discography_with_client(self, client, artist_id: str, last_scan_timestamp: Optional[datetime] = None) -> Optional[List]:
        """
        Get artist's discography using the specified client, optionally filtered by release date.

        Args:
            client: The metadata client to use (spotify or itunes)
            artist_id: Artist ID for the given client
            last_scan_timestamp: Only return releases after this date (for incremental scans)
                                If None, uses lookback period setting from database
        """
        try:
            # Get all artist albums (albums + singles)
            logger.debug(f"Fetching discography for artist {artist_id}")
            albums = client.get_artist_albums(artist_id, album_type='album,single', limit=50)

            if not albums:
                logger.warning(f"No albums found for artist {artist_id}")
                return []

            # Add small delay after fetching artist discography to be extra safe
            time.sleep(0.3)  # 300ms breathing room

            # Determine cutoff date for filtering
            cutoff_timestamp = last_scan_timestamp

            # If no last scan timestamp, use lookback period setting
            if cutoff_timestamp is None:
                lookback_period = self._get_lookback_period_setting()
                if lookback_period != 'all':
                    # Convert period to days and create cutoff date (use UTC)
                    days = int(lookback_period)
                    cutoff_timestamp = datetime.now(timezone.utc) - timedelta(days=days)
                    logger.info(f"Using lookback period: {lookback_period} days (cutoff: {cutoff_timestamp})")

            # Filter by release date if we have a cutoff timestamp
            if cutoff_timestamp:
                filtered_albums = []
                for album in albums:
                    if self.is_album_after_timestamp(album, cutoff_timestamp):
                        filtered_albums.append(album)

                logger.info(f"Filtered {len(albums)} albums to {len(filtered_albums)} released after {cutoff_timestamp}")
                return filtered_albums

            # Return all albums if no cutoff (lookback_period = 'all')
            return albums

        except Exception as e:
            logger.error(f"Error getting discography for artist {artist_id}: {e}")
            return None

    def _backfill_missing_ids(self, artists: List[WatchlistArtist], provider: str):
        """
        Proactively match ALL artists missing IDs for the current provider.
        
        Example: User has 50 artists with only Spotify IDs.
        When iTunes becomes active, this matches ALL 50 to iTunes in one batch.
        """
        artists_to_match = []
        
        if provider == 'spotify':
            # Find all artists missing Spotify IDs
            artists_to_match = [a for a in artists if not a.spotify_artist_id and a.itunes_artist_id]
        elif provider == 'itunes':
            # Find all artists missing iTunes IDs
            artists_to_match = [a for a in artists if not a.itunes_artist_id and a.spotify_artist_id]
        
        if not artists_to_match:
            logger.info(f"✅ All artists already have {provider} IDs")
            return
        
        logger.info(f"🔄 Backfilling {len(artists_to_match)} artists with {provider} IDs...")
        
        matched_count = 0
        for artist in artists_to_match:
            try:
                if provider == 'spotify':
                    new_id = self._match_to_spotify(artist.artist_name)
                    if new_id:
                        self.database.update_watchlist_spotify_id(artist.id, new_id)
                        artist.spotify_artist_id = new_id  # Update in memory
                        matched_count += 1
                        logger.info(f"✅ Matched '{artist.artist_name}' to Spotify: {new_id}")
                
                elif provider == 'itunes':
                    new_id = self._match_to_itunes(artist.artist_name)
                    if new_id:
                        self.database.update_watchlist_itunes_id(artist.id, new_id)
                        artist.itunes_artist_id = new_id  # Update in memory
                        matched_count += 1
                        logger.info(f"✅ Matched '{artist.artist_name}' to iTunes: {new_id}")
                
                # Small delay to avoid API rate limits
                time.sleep(0.3)
                
            except Exception as e:
                logger.warning(f"Could not match '{artist.artist_name}' to {provider}: {e}")
                continue
        
        logger.info(f"✅ Backfilled {matched_count}/{len(artists_to_match)} artists with {provider} IDs")

    def _match_to_spotify(self, artist_name: str) -> Optional[str]:
        """Match artist name to Spotify ID"""
        try:
            # Use metadata service if available, fallback to spotify_client
            if hasattr(self, '_metadata_service') and self._metadata_service:
                results = self._metadata_service.spotify.search_artists(artist_name, limit=1)
            else:
                results = self.spotify_client.search_artists(artist_name, limit=1)
            
            if results:
                return results[0].id
        except Exception as e:
            logger.warning(f"Could not match {artist_name} to Spotify: {e}")
        return None
    
    def _match_to_itunes(self, artist_name: str) -> Optional[str]:
        """Match artist name to iTunes ID"""
        try:
            # Use metadata service's iTunes client
            if hasattr(self, '_metadata_service') and self._metadata_service:
                results = self._metadata_service.itunes.search_artists(artist_name, limit=1)
                if results:
                    return results[0].id
            else:
                # iTunes client not available without metadata service
                logger.warning(f"Cannot match to iTunes - MetadataService not available")
        except Exception as e:
            logger.warning(f"Could not match {artist_name} to iTunes: {e}")
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

            # Track passes all filters
            return True

        except Exception as e:
            logger.warning(f"Error checking track content type inclusion: {e}")
            return True  # Default to including on error

    def is_track_missing_from_library(self, track) -> bool:
        """
        Check if a track is missing from the local Plex library.
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
            
            for artist_name in artists_to_search:
                for query_title in unique_title_variations:
                    # Use same database check as modals with server awareness
                    from config.settings import config_manager
                    active_server = config_manager.get_active_media_server()
                    db_track, confidence = self.database.check_track_exists(query_title, artist_name, confidence_threshold=0.7, server_source=active_server)
                    
                    if db_track and confidence >= 0.7:
                        logger.debug(f"✔️ Track found in library: '{original_title}' by '{artist_name}' (confidence: {confidence:.2f})")
                        return False  # Track exists in library
            
            # No match found with any variation or artist
            logger.info(f"❌ Track missing from library: '{original_title}' by '{artists_to_search[0] if artists_to_search else 'Unknown'}' - adding to wishlist")
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
                track_uri = getattr(track, 'uri', '')
            
            if isinstance(album, dict):
                album_name = album.get('name', 'Unknown')
                album_id = album.get('id', '')
                album_release_date = album.get('release_date', '')
                album_images = album.get('images', [])
                album_type = album.get('album_type', 'album')  # 'album', 'single', or 'ep'
                total_tracks = album.get('total_tracks', 0)
            else:
                album_name = album.name
                album_id = album.id
                album_release_date = album.release_date
                album_images = album.images if hasattr(album, 'images') else []
                album_type = album.album_type if hasattr(album, 'album_type') else 'album'
                total_tracks = album.total_tracks if hasattr(album, 'total_tracks') else 0

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
                    'total_tracks': total_tracks  # Store track count for accurate categorization
                },
                'duration_ms': track_duration,
                'explicit': track_explicit,
                'external_urls': track_external_urls,
                'popularity': track_popularity,
                'preview_url': track_preview_url,
                'track_number': track_number,
                'uri': track_uri,
                'is_local': False
            }
            
            # Add to wishlist with watchlist context
            success = self.database.add_to_wishlist(
                spotify_track_data=spotify_track_data,
                failure_reason="Missing from library (found by watchlist scan)",
                source_type="watchlist",
                source_info={
                    'watchlist_artist_name': watchlist_artist.artist_name,
                    'watchlist_artist_id': watchlist_artist.spotify_artist_id,
                    'album_name': album_name,
                    'scan_timestamp': datetime.now().isoformat()
                }
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
    
    def update_artist_scan_timestamp(self, spotify_artist_id: str) -> bool:
        """Update the last scan timestamp for an artist"""
        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE watchlist_artists 
                    SET last_scan_timestamp = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE spotify_artist_id = ?
                """, (spotify_artist_id,))
                
                conn.commit()
                
                if cursor.rowcount > 0:
                    logger.debug(f"Updated scan timestamp for artist {spotify_artist_id}")
                    return True
                else:
                    logger.warning(f"No artist found with Spotify ID {spotify_artist_id}")
                    return False
                
        except Exception as e:
            logger.error(f"Error updating scan timestamp for artist {spotify_artist_id}: {e}")
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

            # Get iTunes client for matching
            from core.itunes_client import iTunesClient
            itunes_client = iTunesClient()

            # Get the searched artist's IDs to exclude them
            searched_spotify_id = None
            searched_itunes_id = None
            try:
                # Try Spotify search
                if self.spotify_client and self.spotify_client.is_spotify_authenticated():
                    searched_results = self.spotify_client.search_artists(artist_name, limit=1)
                    if searched_results and len(searched_results) > 0:
                        searched_spotify_id = searched_results[0].id
            except Exception as e:
                logger.debug(f"Could not get searched artist Spotify ID: {e}")

            try:
                # Try iTunes search
                itunes_results = itunes_client.search_artists(artist_name, limit=1)
                if itunes_results and len(itunes_results) > 0:
                    searched_itunes_id = itunes_results[0].id
            except Exception as e:
                logger.debug(f"Could not get searched artist iTunes ID: {e}")

            # Match each artist to both Spotify and iTunes
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
                        'image_url': None,
                        'genres': [],
                        'popularity': 0
                    }

                    # Try to match on Spotify
                    if self.spotify_client and self.spotify_client.is_spotify_authenticated():
                        try:
                            spotify_results = self.spotify_client.search_artists(artist_name_to_match, limit=1)
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

                    # Try to match on iTunes
                    try:
                        itunes_results = itunes_client.search_artists(artist_name_to_match, limit=1)
                        if itunes_results and len(itunes_results) > 0:
                            itunes_artist = itunes_results[0]
                            # Skip if this is the searched artist
                            if itunes_artist.id != searched_itunes_id:
                                artist_data['itunes_id'] = itunes_artist.id
                                # Use iTunes name if we don't have Spotify
                                if not artist_data['spotify_id']:
                                    artist_data['name'] = itunes_artist.name
                                # Use iTunes genres if we don't have Spotify genres
                                if not artist_data['genres'] and hasattr(itunes_artist, 'genres'):
                                    artist_data['genres'] = itunes_artist.genres
                    except Exception as e:
                        logger.debug(f"iTunes match failed for {artist_name_to_match}: {e}")

                    # Only add if we got at least one ID
                    if artist_data['spotify_id'] or artist_data['itunes_id']:
                        seen_names.add(name_lower)
                        matched_artists.append(artist_data)
                        logger.debug(f"  Matched: {artist_data['name']} (Spotify: {artist_data['spotify_id']}, iTunes: {artist_data['itunes_id']})")

                except Exception as match_error:
                    logger.debug(f"Error matching {artist_name_to_match}: {match_error}")
                    continue

            logger.info(f"Matched {len(matched_artists)} similar artists (Spotify + iTunes)")
            return matched_artists

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching from MusicMap: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching similar artists from MusicMap: {e}")
            return []

    def _backfill_similar_artists_itunes_ids(self, source_artist_id: str) -> int:
        """
        Backfill missing iTunes IDs for cached similar artists.
        This ensures seamless dual-source support without clearing cached data.

        Args:
            source_artist_id: The source artist ID to backfill similar artists for

        Returns:
            Number of similar artists updated with iTunes IDs
        """
        try:
            # Get similar artists that are missing iTunes IDs
            similar_artists = self.database.get_similar_artists_missing_itunes_ids(source_artist_id)

            if not similar_artists:
                return 0

            logger.info(f"Backfilling iTunes IDs for {len(similar_artists)} similar artists")

            # Get iTunes client
            from core.itunes_client import iTunesClient
            itunes_client = iTunesClient()

            updated_count = 0
            for similar_artist in similar_artists:
                try:
                    # Search iTunes by artist name
                    itunes_results = itunes_client.search_artists(similar_artist.similar_artist_name, limit=1)
                    if itunes_results and len(itunes_results) > 0:
                        itunes_id = itunes_results[0].id
                        # Update the similar artist with the iTunes ID
                        if self.database.update_similar_artist_itunes_id(similar_artist.id, itunes_id):
                            updated_count += 1
                            logger.debug(f"  Backfilled iTunes ID {itunes_id} for {similar_artist.similar_artist_name}")
                except Exception as e:
                    logger.debug(f"  Could not backfill iTunes ID for {similar_artist.similar_artist_name}: {e}")
                    continue

            if updated_count > 0:
                logger.info(f"Backfilled {updated_count} similar artists with iTunes IDs")

            return updated_count

        except Exception as e:
            logger.error(f"Error backfilling similar artists iTunes IDs: {e}")
            return 0

    def update_similar_artists(self, watchlist_artist: WatchlistArtist, limit: int = 10) -> bool:
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

            # Use consistent source artist ID (prefer Spotify, fall back to iTunes or internal ID)
            source_artist_id = watchlist_artist.spotify_artist_id or watchlist_artist.itunes_artist_id or str(watchlist_artist.id)

            # Store each similar artist in database
            stored_count = 0
            for rank, similar_artist in enumerate(similar_artists, 1):
                try:
                    # similar_artist has 'name', 'spotify_id', and 'itunes_id' keys
                    success = self.database.add_or_update_similar_artist(
                        source_artist_id=source_artist_id,
                        similar_artist_name=similar_artist['name'],
                        similar_artist_spotify_id=similar_artist.get('spotify_id'),
                        similar_artist_itunes_id=similar_artist.get('itunes_id'),
                        similarity_rank=rank
                    )

                    if success:
                        stored_count += 1
                        logger.debug(f"  #{rank}: {similar_artist['name']} (Spotify: {similar_artist.get('spotify_id')}, iTunes: {similar_artist.get('itunes_id')})")

                except Exception as e:
                    logger.warning(f"Error storing similar artist {similar_artist.get('name', 'Unknown')}: {e}")
                    continue

            logger.info(f"Stored {stored_count}/{len(similar_artists)} similar artists for {watchlist_artist.artist_name}")
            return True

        except Exception as e:
            logger.error(f"Error fetching similar artists for {watchlist_artist.artist_name}: {e}")
            return False

    def populate_discovery_pool(self, top_artists_limit: int = 50, albums_per_artist: int = 10):
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

            # Check if we should run (prevents over-polling)
            if not self.database.should_populate_discovery_pool(hours_threshold=24):
                logger.info("Discovery pool was populated recently (< 24 hours ago). Skipping.")
                return

            logger.info("Populating discovery pool from similar artists...")

            # Determine which sources are available
            spotify_available = self.spotify_client and self.spotify_client.is_spotify_authenticated()

            # Import iTunes client for fallback
            from core.itunes_client import iTunesClient
            itunes_client = iTunesClient()
            itunes_available = True  # iTunes is always available (no auth needed)

            if not spotify_available and not itunes_available:
                logger.warning("No music sources available to populate discovery pool")
                return

            logger.info(f"Sources available - Spotify: {spotify_available}, iTunes: {itunes_available}")

            # Get top similar artists across all watchlist (ordered by occurrence_count)
            similar_artists = self.database.get_top_similar_artists(limit=top_artists_limit)

            if not similar_artists:
                logger.info("No similar artists found to populate discovery pool")
                return

            logger.info(f"Processing {len(similar_artists)} top similar artists for discovery pool")

            total_tracks_added = 0

            for artist_idx, similar_artist in enumerate(similar_artists, 1):
                try:
                    logger.info(f"[{artist_idx}/{len(similar_artists)}] Processing {similar_artist.similar_artist_name} (occurrence: {similar_artist.occurrence_count})")

                    # Build list of sources to process for this artist
                    # iTunes is ALWAYS processed (baseline), Spotify is added if authenticated
                    sources_to_process = []

                    # Always add iTunes first (baseline source)
                    itunes_id = similar_artist.similar_artist_itunes_id
                    if not itunes_id:
                        # On-the-fly lookup for missing iTunes ID (seamless provider switching)
                        try:
                            itunes_results = itunes_client.search_artists(similar_artist.similar_artist_name, limit=1)
                            if itunes_results and len(itunes_results) > 0:
                                itunes_id = itunes_results[0].id
                                # Cache it for future use
                                self.database.update_similar_artist_itunes_id(similar_artist.id, itunes_id)
                                logger.debug(f"  Resolved iTunes ID {itunes_id} for {similar_artist.similar_artist_name}")
                        except Exception as e:
                            logger.debug(f"  Could not resolve iTunes ID for {similar_artist.similar_artist_name}: {e}")

                    if itunes_id:
                        sources_to_process.append(('itunes', itunes_id))

                    # Add Spotify if authenticated and we have an ID
                    if spotify_available and similar_artist.similar_artist_spotify_id:
                        sources_to_process.append(('spotify', similar_artist.similar_artist_spotify_id))

                    if not sources_to_process:
                        logger.debug(f"No valid IDs for {similar_artist.similar_artist_name}, skipping")
                        continue

                    logger.debug(f"  Processing {len(sources_to_process)} source(s): {[s[0] for s in sources_to_process]}")

                    # Process each source for this artist
                    for source, artist_id in sources_to_process:
                        try:
                            # Get artist's albums from this source
                            if source == 'spotify':
                                all_albums = self.spotify_client.get_artist_albums(
                                    artist_id,
                                    album_type='album,single,ep',
                                    limit=50
                                )
                            else:  # itunes
                                all_albums = itunes_client.get_artist_albums(
                                    artist_id,
                                    album_type='album,single',
                                    limit=50
                                )

                            if not all_albums:
                                logger.debug(f"No albums found for {similar_artist.similar_artist_name} on {source}")
                                continue

                            # Fetch artist genres for this source
                            artist_genres = []
                            try:
                                if source == 'spotify':
                                    artist_data = self.spotify_client.get_artist(artist_id)
                                    if artist_data and 'genres' in artist_data:
                                        artist_genres = artist_data['genres']
                                else:  # iTunes - genres from artist lookup
                                    artist_data = itunes_client.get_artist(artist_id)
                                    if artist_data and 'genres' in artist_data:
                                        artist_genres = artist_data['genres']
                            except Exception as e:
                                logger.debug(f"Could not fetch genres for {similar_artist.similar_artist_name} on {source}: {e}")

                            # IMPROVED: Smart selection mixing albums, singles, and EPs
                            # Prioritize recent releases and popular content

                            # Separate by type for balanced selection
                            albums = [a for a in all_albums if hasattr(a, 'album_type') and a.album_type == 'album']
                            singles_eps = [a for a in all_albums if hasattr(a, 'album_type') and a.album_type in ['single', 'ep']]
                            other = [a for a in all_albums if not hasattr(a, 'album_type')]

                            # Select albums: latest releases + popular older content
                            selected_albums = []

                            # Always include 3 most recent releases (any type) - this captures new singles/EPs
                            latest_releases = all_albums[:3]
                            selected_albums.extend(latest_releases)

                            # Add remaining slots with balanced mix
                            remaining_slots = albums_per_artist - len(selected_albums)
                            if remaining_slots > 0:
                                # Combine remaining albums and singles
                                remaining_content = all_albums[3:]

                                if len(remaining_content) > remaining_slots:
                                    # Randomly select from remaining content
                                    random_selection = random.sample(remaining_content, remaining_slots)
                                    selected_albums.extend(random_selection)
                                else:
                                    selected_albums.extend(remaining_content)

                            logger.info(f"  [{source}] Selected {len(selected_albums)} releases from {len(all_albums)} available (albums: {len(albums)}, singles/EPs: {len(singles_eps)})")

                            # Process each selected album
                            for album_idx, album in enumerate(selected_albums, 1):
                                try:
                                    # Get full album data with tracks from appropriate source
                                    if source == 'spotify':
                                        album_data = self.spotify_client.get_album(album.id)
                                        if not album_data or 'tracks' not in album_data:
                                            continue
                                        tracks = album_data['tracks'].get('items', [])
                                    else:  # itunes
                                        album_data = itunes_client.get_album(album.id)
                                        if not album_data:
                                            continue
                                        # iTunes get_album doesn't include tracks inline, need separate call
                                        tracks_data = itunes_client.get_album_tracks(album.id)
                                        tracks = tracks_data.get('items', []) if tracks_data else []

                                    logger.debug(f"    Album {album_idx}: {album_data.get('name', 'Unknown')} ({len(tracks)} tracks)")

                                    # Determine if this is a new release (within last 30 days)
                                    is_new = False
                                    try:
                                        release_date_str = album_data.get('release_date', '')
                                        if release_date_str:
                                            # Handle full date or year-only
                                            if len(release_date_str) >= 10:
                                                release_date = datetime.strptime(release_date_str[:10], "%Y-%m-%d")
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
                                                },
                                                '_source': source
                                            }

                                            # Build track data for discovery pool with source-specific IDs
                                            track_data = {
                                                'track_name': track.get('name', 'Unknown Track'),
                                                'artist_name': similar_artist.similar_artist_name,
                                                'album_name': album_data.get('name', 'Unknown Album'),
                                                'album_cover_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                                                'duration_ms': track.get('duration_ms', 0),
                                                'popularity': album_data.get('popularity', 0),
                                                'release_date': album_data.get('release_date', ''),
                                                'is_new_release': is_new,
                                                'track_data_json': enhanced_track,
                                                'artist_genres': artist_genres
                                            }

                                            # Add source-specific IDs
                                            if source == 'spotify':
                                                track_data['spotify_track_id'] = track.get('id')
                                                track_data['spotify_album_id'] = album_data.get('id')
                                                track_data['spotify_artist_id'] = similar_artist.similar_artist_spotify_id
                                            else:  # itunes
                                                track_data['itunes_track_id'] = track.get('id')
                                                track_data['itunes_album_id'] = album_data.get('id')
                                                track_data['itunes_artist_id'] = similar_artist.similar_artist_itunes_id

                                            # Add to discovery pool with source
                                            if self.database.add_to_discovery_pool(track_data, source=source):
                                                total_tracks_added += 1

                                        except Exception as track_error:
                                            logger.debug(f"Error adding track to discovery pool: {track_error}")
                                            continue

                                    # Small delay between albums
                                    time.sleep(DELAY_BETWEEN_ALBUMS)

                                except Exception as album_error:
                                    logger.warning(f"Error processing album on {source}: {album_error}")
                                    continue

                        except Exception as source_error:
                            logger.warning(f"Error processing {source} source for {similar_artist.similar_artist_name}: {source_error}")
                            continue

                    # Delay between artists (after processing all sources for this artist)
                    if artist_idx < len(similar_artists):
                        time.sleep(DELAY_BETWEEN_ARTISTS)

                except Exception as artist_error:
                    logger.warning(f"Error processing artist {similar_artist.similar_artist_name}: {artist_error}")
                    continue

            logger.info(f"Discovery pool from similar artists complete: {total_tracks_added} tracks added")

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

                            # Try Spotify first if available
                            if spotify_available:
                                try:
                                    search_results = self.spotify_client.search_albums(f"album:{album_row['title']} artist:{album_row['artist_name']}", limit=1)
                                    if search_results and len(search_results) > 0:
                                        spotify_album = search_results[0]
                                        album_data = self.spotify_client.get_album(spotify_album.id)
                                        if album_data and 'tracks' in album_data:
                                            tracks = album_data['tracks'].get('items', [])
                                            db_source = 'spotify'
                                            if album_data.get('artists'):
                                                artist_id_for_genres = album_data['artists'][0]['id']
                                except Exception as e:
                                    logger.debug(f"Spotify search failed for {album_row['title']}: {e}")

                            # Fall back to iTunes if Spotify didn't work
                            if not tracks and itunes_available:
                                try:
                                    search_results = itunes_client.search_albums(query, limit=1)
                                    if search_results and len(search_results) > 0:
                                        itunes_album = search_results[0]
                                        album_data = itunes_client.get_album(itunes_album.id)
                                        if album_data:
                                            tracks_data = itunes_client.get_album_tracks(itunes_album.id)
                                            tracks = tracks_data.get('items', []) if tracks_data else []
                                            db_source = 'itunes'
                                            # For iTunes, artist ID is in the album data
                                            if album_data.get('artists'):
                                                artist_id_for_genres = album_data['artists'][0].get('id')
                                except Exception as e:
                                    logger.debug(f"iTunes search failed for {album_row['title']}: {e}")

                            if not tracks or not album_data:
                                continue

                            # Fetch artist genres
                            artist_genres = []
                            try:
                                if artist_id_for_genres:
                                    if db_source == 'spotify':
                                        artist_data = self.spotify_client.get_artist(artist_id_for_genres)
                                    else:
                                        artist_data = itunes_client.get_artist(artist_id_for_genres)
                                    if artist_data and 'genres' in artist_data:
                                        artist_genres = artist_data['genres']
                            except Exception as e:
                                logger.debug(f"Could not fetch genres for album artist: {e}")

                            # Check if new release
                            is_new = False
                            try:
                                release_date_str = album_data.get('release_date', '')
                                if release_date_str and len(release_date_str) >= 10:
                                    release_date = datetime.strptime(release_date_str[:10], "%Y-%m-%d")
                                    days_old = (datetime.now() - release_date).days
                                    is_new = days_old <= 30
                            except:
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

                                    # Add source-specific IDs
                                    if db_source == 'spotify':
                                        track_data['spotify_track_id'] = track.get('id')
                                        track_data['spotify_album_id'] = album_data.get('id')
                                        track_data['spotify_artist_id'] = artist_id_for_genres or ''
                                    else:  # itunes
                                        track_data['itunes_track_id'] = track.get('id')
                                        track_data['itunes_album_id'] = album_data.get('id')
                                        track_data['itunes_artist_id'] = artist_id_for_genres or ''

                                    if self.database.add_to_discovery_pool(track_data, source=db_source):
                                        total_tracks_added += 1
                                except Exception as track_error:
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
            self.database.update_discovery_pool_timestamp(track_count=final_count)
            logger.info(f"Discovery pool now contains {final_count} total tracks (built over time)")

            # Cache recent albums for discovery page
            logger.info("Caching recent albums for discovery page...")
            self.cache_discovery_recent_albums()

            # Curate playlists for consistent daily experience
            logger.info("Curating discovery playlists...")
            self.curate_discovery_playlists()

        except Exception as e:
            logger.error(f"Error populating discovery pool: {e}")
            import traceback
            traceback.print_exc()

    def update_discovery_pool_incremental(self):
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
            if not self.database.should_populate_discovery_pool(hours_threshold=6):
                logger.info("Discovery pool was updated recently (< 6 hours ago). Skipping incremental update.")
                return

            logger.info("Starting incremental discovery pool update (watchlist artists only)...")

            watchlist_artists = self.database.get_watchlist_artists()
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
                        limit=5
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

                                    if self.database.add_to_discovery_pool(track_data):
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

                self.database.update_discovery_pool_timestamp(track_count=current_count)
                logger.info(f"Discovery pool now contains {current_count} total tracks")

        except Exception as e:
            logger.error(f"Error during incremental discovery pool update: {e}")
            import traceback
            traceback.print_exc()

    def cache_discovery_recent_albums(self):
        """
        Cache recent albums from watchlist and similar artists for discover page.

        Supports both Spotify and iTunes sources - iTunes is always processed (baseline),
        Spotify is added when authenticated. Same pattern as discovery pool.
        """
        try:
            from datetime import datetime, timedelta

            logger.info("Caching recent albums for discover page...")

            # Clear existing cache
            self.database.clear_discovery_recent_albums()

            # 30-day window for recent releases
            cutoff_date = datetime.now() - timedelta(days=30)
            cached_count = {'spotify': 0, 'itunes': 0}
            albums_checked = 0

            # Determine available sources
            spotify_available = self.spotify_client and self.spotify_client.is_spotify_authenticated()

            # Get iTunes client
            from core.itunes_client import iTunesClient
            itunes_client = iTunesClient()

            # Get artists to check
            watchlist_artists = self.database.get_watchlist_artists()
            similar_artists = self.database.get_top_similar_artists(limit=50)

            logger.info(f"Checking albums from {len(watchlist_artists)} watchlist + {len(similar_artists)} similar artists")
            logger.info(f"Sources: Spotify={spotify_available}, iTunes=True")

            def process_album(album, artist_name, artist_spotify_id, artist_itunes_id, source):
                """Helper to process and cache a single album"""
                nonlocal albums_checked
                try:
                    albums_checked += 1
                    release_str = album.release_date if hasattr(album, 'release_date') else None

                    if not release_str:
                        return False

                    # Handle iTunes ISO format (2017-12-08T08:00:00Z)
                    if 'T' in release_str:
                        release_str = release_str.split('T')[0]

                    if len(release_str) >= 10:
                        release_date = datetime.strptime(release_str[:10], "%Y-%m-%d")
                        if release_date >= cutoff_date:
                            album_data = {
                                'album_spotify_id': album.id if source == 'spotify' else None,
                                'album_itunes_id': album.id if source == 'itunes' else None,
                                'album_name': album.name,
                                'artist_name': artist_name,
                                'artist_spotify_id': artist_spotify_id,
                                'artist_itunes_id': artist_itunes_id,
                                'album_cover_url': album.image_url if hasattr(album, 'image_url') else None,
                                'release_date': release_str[:10],
                                'album_type': album.album_type if hasattr(album, 'album_type') else 'album'
                            }
                            if self.database.cache_discovery_recent_album(album_data, source=source):
                                cached_count[source] += 1
                                logger.debug(f"Cached [{source}] recent album: {album.name} by {artist_name} ({release_str})")
                                return True
                except Exception as e:
                    logger.debug(f"Error processing album: {e}")
                return False

            # Process watchlist artists
            for artist in watchlist_artists:
                # Always process iTunes (baseline)
                itunes_id = artist.itunes_artist_id
                if not itunes_id:
                    # Try to resolve iTunes ID on-the-fly
                    try:
                        results = itunes_client.search_artists(artist.artist_name, limit=1)
                        if results and len(results) > 0:
                            itunes_id = results[0].id
                    except:
                        pass

                if itunes_id:
                    try:
                        albums = itunes_client.get_artist_albums(itunes_id, album_type='album,single', limit=20)
                        for album in albums or []:
                            process_album(album, artist.artist_name, artist.spotify_artist_id, itunes_id, 'itunes')
                    except Exception as e:
                        logger.debug(f"Error fetching iTunes albums for {artist.artist_name}: {e}")

                # Process Spotify if authenticated
                if spotify_available and artist.spotify_artist_id:
                    try:
                        albums = self.spotify_client.get_artist_albums(
                            artist.spotify_artist_id,
                            album_type='album,single,ep',
                            limit=20
                        )
                        for album in albums or []:
                            process_album(album, artist.artist_name, artist.spotify_artist_id, itunes_id, 'spotify')
                    except Exception as e:
                        logger.debug(f"Error fetching Spotify albums for {artist.artist_name}: {e}")

                time.sleep(DELAY_BETWEEN_ARTISTS)

            # Process similar artists
            for artist in similar_artists:
                # Always process iTunes (baseline)
                itunes_id = artist.similar_artist_itunes_id
                if not itunes_id:
                    # Try to resolve iTunes ID on-the-fly
                    try:
                        results = itunes_client.search_artists(artist.similar_artist_name, limit=1)
                        if results and len(results) > 0:
                            itunes_id = results[0].id
                            # Cache for future
                            self.database.update_similar_artist_itunes_id(artist.id, itunes_id)
                    except:
                        pass

                if itunes_id:
                    try:
                        albums = itunes_client.get_artist_albums(itunes_id, album_type='album,single', limit=20)
                        for album in albums or []:
                            process_album(album, artist.similar_artist_name, artist.similar_artist_spotify_id, itunes_id, 'itunes')
                    except Exception as e:
                        logger.debug(f"Error fetching iTunes albums for {artist.similar_artist_name}: {e}")

                # Process Spotify if authenticated
                if spotify_available and artist.similar_artist_spotify_id:
                    try:
                        albums = self.spotify_client.get_artist_albums(
                            artist.similar_artist_spotify_id,
                            album_type='album,single,ep',
                            limit=20
                        )
                        for album in albums or []:
                            process_album(album, artist.similar_artist_name, artist.similar_artist_spotify_id, itunes_id, 'spotify')
                    except Exception as e:
                        logger.debug(f"Error fetching Spotify albums for {artist.similar_artist_name}: {e}")

                time.sleep(DELAY_BETWEEN_ARTISTS)

            total_cached = cached_count['spotify'] + cached_count['itunes']
            logger.info(f"Cached {total_cached} recent albums (Spotify: {cached_count['spotify']}, iTunes: {cached_count['itunes']}) from {albums_checked} albums checked")

        except Exception as e:
            logger.error(f"Error caching discovery recent albums: {e}")
            import traceback
            traceback.print_exc()

    def curate_discovery_playlists(self):
        """
        Curate consistent playlist selections that stay the same until next discovery pool update.

        Supports both Spotify and iTunes sources - creates separate curated playlists for each.
        - Release Radar: Prioritizes freshness + popularity from recent releases
        - Discovery Weekly: Balanced mix of popular picks, deep cuts, and mid-tier tracks
        """
        try:
            import random
            from datetime import datetime
            from core.itunes_client import iTunesClient

            logger.info("Curating discovery playlists...")

            # Determine available sources
            spotify_available = self.spotify_client and self.spotify_client.is_spotify_authenticated()
            itunes_client = iTunesClient()

            # Process each available source
            sources_to_process = ['itunes']  # iTunes always available
            if spotify_available:
                sources_to_process.append('spotify')

            logger.info(f"Curating playlists for sources: {sources_to_process}")

            for source in sources_to_process:
                logger.info(f"Curating Release Radar for {source}...")

                # 1. Curate Release Radar - 50 tracks from recent albums
                recent_albums = self.database.get_discovery_recent_albums(limit=50, source=source)
                release_radar_tracks = []

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
                                album_id = album.get('album_spotify_id') if source == 'spotify' else album.get('album_itunes_id')
                                if not album_id:
                                    continue

                                if source == 'spotify':
                                    album_data = self.spotify_client.get_album(album_id)
                                else:
                                    album_data = itunes_client.get_album(album_id)

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
                                    popularity_score = track.get('popularity', album_data.get('popularity', 50))
                                    is_single = album.get('album_type', 'album') == 'single'
                                    single_bonus = 20 if is_single else 0
                                    total_score = (recency_score * 0.5) + (popularity_score * 0.3) + single_bonus

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
                            else:
                                formatted_track['itunes_track_id'] = track_data['id']
                                formatted_track['itunes_album_id'] = track_data['album'].get('id', '')

                            self.database.add_to_discovery_pool(formatted_track, source=source)
                        except Exception as e:
                            continue

                # Save with source suffix for multi-source support
                playlist_key = f'release_radar_{source}'
                self.database.save_curated_playlist(playlist_key, release_radar_tracks)
                logger.info(f"Release Radar ({source}) curated: {len(release_radar_tracks)} tracks")

                # 2. Curate Discovery Weekly - 50 tracks from discovery pool
                logger.info(f"Curating Discovery Weekly for {source}...")
                discovery_tracks = self.database.get_discovery_pool_tracks(limit=2000, new_releases_only=False, source=source)

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

                    # Balanced selection
                    random.shuffle(popular_picks)
                    random.shuffle(balanced_mix)
                    random.shuffle(deep_cuts)

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

                playlist_key = f'discovery_weekly_{source}'
                self.database.save_curated_playlist(playlist_key, discovery_weekly_tracks)
                logger.info(f"Discovery Weekly ({source}) curated: {len(discovery_weekly_tracks)} tracks")

            # Also save without suffix for backward compatibility (use active source)
            active_source = 'spotify' if spotify_available else 'itunes'
            release_radar_key = f'release_radar_{active_source}'
            discovery_weekly_key = f'discovery_weekly_{active_source}'

            # Copy active source playlists to non-suffixed keys
            release_radar_ids = self.database.get_curated_playlist(release_radar_key) or []
            discovery_weekly_ids = self.database.get_curated_playlist(discovery_weekly_key) or []
            self.database.save_curated_playlist('release_radar', release_radar_ids)
            self.database.save_curated_playlist('discovery_weekly', discovery_weekly_ids)

            logger.info("Playlist curation complete")

        except Exception as e:
            logger.error(f"Error curating discovery playlists: {e}")
            import traceback
            traceback.print_exc()

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

# Singleton instance
_watchlist_scanner_instance = None

def get_watchlist_scanner(spotify_client: SpotifyClient) -> WatchlistScanner:
    """Get the global watchlist scanner instance"""
    global _watchlist_scanner_instance
    if _watchlist_scanner_instance is None:
        _watchlist_scanner_instance = WatchlistScanner(spotify_client)
    return _watchlist_scanner_instance