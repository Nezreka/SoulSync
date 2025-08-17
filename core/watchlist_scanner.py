#!/usr/bin/env python3

"""
Watchlist Scanner Service - Monitors watched artists for new releases
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass
import re
import time
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
    
    def __init__(self, spotify_client: SpotifyClient, database_path: str = "database/music_library.db"):
        self.spotify_client = spotify_client
        self.database_path = database_path
        self._database = None
        self._wishlist_service = None
        self._matching_engine = None
    
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
    
    def scan_all_watchlist_artists(self) -> List[ScanResult]:
        """
        Scan all artists in the watchlist for new releases.
        Only checks releases after their last scan timestamp.
        """
        logger.info("Starting watchlist scan for all artists")
        
        try:
            # Get all watchlist artists
            watchlist_artists = self.database.get_watchlist_artists()
            if not watchlist_artists:
                logger.info("No artists in watchlist to scan")
                return []
            
            logger.info(f"Found {len(watchlist_artists)} artists in watchlist")
            
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
            
            return scan_results
            
        except Exception as e:
            logger.error(f"Error during watchlist scan: {e}")
            return []
    
    def scan_artist(self, watchlist_artist: WatchlistArtist) -> ScanResult:
        """
        Scan a single artist for new releases.
        Only checks releases after the last scan timestamp.
        """
        try:
            logger.info(f"Scanning artist: {watchlist_artist.artist_name}")
            
            # Get artist discography from Spotify
            albums = self.get_artist_discography(watchlist_artist.spotify_artist_id, watchlist_artist.last_scan_timestamp)
            
            if albums is None:
                return ScanResult(
                    artist_name=watchlist_artist.artist_name,
                    spotify_artist_id=watchlist_artist.spotify_artist_id,
                    albums_checked=0,
                    new_tracks_found=0,
                    tracks_added_to_wishlist=0,
                    success=False,
                    error_message="Failed to get artist discography from Spotify"
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
                    # Get full album data with tracks
                    logger.info(f"Checking album {album_index + 1}/{len(albums)}: {album.name}")
                    album_data = self.spotify_client.get_album(album.id)
                    if not album_data or 'tracks' not in album_data or not album_data['tracks'].get('items'):
                        continue
                    
                    tracks = album_data['tracks']['items']
                    logger.debug(f"Checking album: {album_data.get('name', 'Unknown')} ({len(tracks)} tracks)")
                    
                    # Check each track
                    for track in tracks:
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
            
            # Update last scan timestamp for this artist
            self.update_artist_scan_timestamp(watchlist_artist.spotify_artist_id)
            
            return ScanResult(
                artist_name=watchlist_artist.artist_name,
                spotify_artist_id=watchlist_artist.spotify_artist_id,
                albums_checked=len(albums),
                new_tracks_found=new_tracks_found,
                tracks_added_to_wishlist=tracks_added_to_wishlist,
                success=True
            )
            
        except Exception as e:
            logger.error(f"Error scanning artist {watchlist_artist.artist_name}: {e}")
            return ScanResult(
                artist_name=watchlist_artist.artist_name,
                spotify_artist_id=watchlist_artist.spotify_artist_id,
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
            
            # Filter by release date if we have a last scan timestamp
            if last_scan_timestamp:
                filtered_albums = []
                for album in albums:
                    if self.is_album_after_timestamp(album, last_scan_timestamp):
                        filtered_albums.append(album)
                
                logger.info(f"Filtered {len(albums)} albums to {len(filtered_albums)} released after {last_scan_timestamp}")
                return filtered_albums
            
            return albums
            
        except Exception as e:
            logger.error(f"Error getting discography for artist {spotify_artist_id}: {e}")
            return None
    
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
                    # Use same database check as modals
                    db_track, confidence = self.database.check_track_exists(query_title, artist_name, confidence_threshold=0.7)
                    
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
            else:
                album_name = album.name
                album_id = album.id
                album_release_date = album.release_date
                album_images = album.images if hasattr(album, 'images') else []
            
            # Create Spotify track data structure
            spotify_track_data = {
                'id': track_id,
                'name': track_name,
                'artists': track_artists,
                'album': {
                    'name': album_name,
                    'id': album_id,
                    'release_date': album_release_date,
                    'images': album_images
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

# Singleton instance
_watchlist_scanner_instance = None

def get_watchlist_scanner(spotify_client: SpotifyClient) -> WatchlistScanner:
    """Get the global watchlist scanner instance"""
    global _watchlist_scanner_instance
    if _watchlist_scanner_instance is None:
        _watchlist_scanner_instance = WatchlistScanner(spotify_client)
    return _watchlist_scanner_instance