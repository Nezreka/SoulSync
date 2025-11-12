#!/usr/bin/env python3

"""
Watchlist Scanner Service - Monitors watched artists for new releases
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
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

            # Populate discovery pool with tracks from similar artists
            logger.info("Starting discovery pool population...")
            self.populate_discovery_pool()

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

            # Fetch and store similar artists for discovery feature (with caching to avoid over-polling)
            try:
                # Check if we have fresh similar artists cached (< 30 days old)
                if self.database.has_fresh_similar_artists(watchlist_artist.spotify_artist_id, days_threshold=30):
                    logger.info(f"Similar artists for {watchlist_artist.artist_name} are cached and fresh, skipping fetch")
                else:
                    logger.info(f"Fetching similar artists for {watchlist_artist.artist_name}...")
                    self.update_similar_artists(watchlist_artist)
                    logger.info(f"Similar artists updated for {watchlist_artist.artist_name}")
            except Exception as similar_error:
                logger.warning(f"Failed to update similar artists for {watchlist_artist.artist_name}: {similar_error}")

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

    def _fetch_similar_artists_from_musicmap(self, artist_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Fetch similar artists from MusicMap and match them to Spotify.

        Args:
            artist_name: The artist name to find similar artists for
            limit: Maximum number of similar artists to return (default: 20)

        Returns:
            List of matched artist dictionaries with Spotify data
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

            # Get the searched artist's Spotify ID to exclude them
            searched_artist_id = None
            try:
                searched_results = self.spotify_client.search_artists(artist_name, limit=1)
                if searched_results and len(searched_results) > 0:
                    searched_artist_id = searched_results[0].id
            except Exception as e:
                logger.warning(f"Could not get searched artist ID: {e}")

            # Match each artist to Spotify
            matched_artists = []
            seen_artist_ids = set()  # Track seen artist IDs to prevent duplicates

            for artist_name_to_match in similar_artist_names[:limit]:
                try:
                    # Search Spotify for the artist
                    results = self.spotify_client.search_artists(artist_name_to_match, limit=1)

                    if results and len(results) > 0:
                        spotify_artist = results[0]

                        # Skip if this is the searched artist
                        if spotify_artist.id == searched_artist_id:
                            continue

                        # Skip if we've already seen this artist ID (deduplication)
                        if spotify_artist.id in seen_artist_ids:
                            continue

                        seen_artist_ids.add(spotify_artist.id)

                        matched_artists.append({
                            'id': spotify_artist.id,
                            'name': spotify_artist.name,
                            'image_url': spotify_artist.image_url if hasattr(spotify_artist, 'image_url') else None,
                            'genres': spotify_artist.genres if hasattr(spotify_artist, 'genres') else [],
                            'popularity': spotify_artist.popularity if hasattr(spotify_artist, 'popularity') else 0
                        })

                        logger.debug(f"  Matched: {spotify_artist.name}")

                except Exception as match_error:
                    logger.debug(f"Error matching {artist_name_to_match}: {match_error}")
                    continue

            logger.info(f"Matched {len(matched_artists)} similar artists to Spotify")
            return matched_artists

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching from MusicMap: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching similar artists from MusicMap: {e}")
            return []

    def update_similar_artists(self, watchlist_artist: WatchlistArtist, limit: int = 10) -> bool:
        """
        Fetch and store similar artists for a watchlist artist.
        Called after each artist scan to build discovery pool.
        Uses MusicMap to find similar artists and matches them to Spotify.
        """
        try:
            logger.info(f"Fetching similar artists for {watchlist_artist.artist_name}")

            # Get similar artists from MusicMap (returns list of artist dicts)
            similar_artists = self._fetch_similar_artists_from_musicmap(watchlist_artist.artist_name, limit=limit)

            if not similar_artists:
                logger.debug(f"No similar artists found for {watchlist_artist.artist_name}")
                return True  # Not an error, just no recommendations

            logger.info(f"Found {len(similar_artists)} similar artists for {watchlist_artist.artist_name}")

            # Store each similar artist in database
            stored_count = 0
            for rank, similar_artist in enumerate(similar_artists, 1):
                try:
                    # similar_artist is a dict with 'id' and 'name' keys
                    success = self.database.add_or_update_similar_artist(
                        source_artist_id=watchlist_artist.spotify_artist_id,
                        similar_artist_spotify_id=similar_artist['id'],
                        similar_artist_name=similar_artist['name'],
                        similarity_rank=rank
                    )

                    if success:
                        stored_count += 1
                        logger.debug(f"  #{rank}: {similar_artist['name']} (Spotify ID: {similar_artist['id']})")

                except Exception as e:
                    logger.warning(f"Error storing similar artist {similar_artist.get('name', 'Unknown')}: {e}")
                    continue

            logger.info(f"Stored {stored_count}/{len(similar_artists)} similar artists for {watchlist_artist.artist_name}")
            return True

        except Exception as e:
            logger.error(f"Error fetching similar artists for {watchlist_artist.artist_name}: {e}")
            return False

    def populate_discovery_pool(self, top_artists_limit: int = 20, albums_per_artist: int = 5):
        """
        Populate discovery pool with tracks from top similar artists.
        Called after watchlist scan completes.

        This method now:
        - Checks if pool was updated in last 24 hours (prevents over-polling Spotify)
        - Appends to existing pool instead of replacing it
        - Cleans up tracks older than 365 days (maintains 1 year rolling window)
        """
        try:
            from datetime import datetime, timedelta
            import random

            # Check if we should run (prevents over-polling Spotify)
            if not self.database.should_populate_discovery_pool(hours_threshold=24):
                logger.info("Discovery pool was populated recently (< 24 hours ago). Skipping to avoid over-polling Spotify.")
                return

            logger.info("Populating discovery pool from similar artists...")

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

                    # Get artist's albums from Spotify
                    all_albums = self.spotify_client.get_artist_albums(
                        similar_artist.similar_artist_spotify_id,
                        album_type='album',  # Only full albums, not singles
                        limit=50
                    )

                    if not all_albums:
                        logger.debug(f"No albums found for {similar_artist.similar_artist_name}")
                        continue

                    # Filter to only studio albums (exclude compilations, live albums)
                    studio_albums = [a for a in all_albums if 'album_type' not in dir(a) or a.album_type == 'album']

                    if len(studio_albums) == 0:
                        studio_albums = all_albums  # Fallback to all if no studio albums

                    # Select albums: latest + random selection
                    selected_albums = []

                    # Always include latest album
                    if studio_albums:
                        selected_albums.append(studio_albums[0])  # Latest is first

                    # Add random albums if we have more
                    if len(studio_albums) > 1:
                        remaining_slots = min(albums_per_artist - 1, len(studio_albums) - 1)
                        random_albums = random.sample(studio_albums[1:], remaining_slots)
                        selected_albums.extend(random_albums)

                    logger.info(f"  Selected {len(selected_albums)} albums from {len(studio_albums)} available")

                    # Process each selected album
                    for album_idx, album in enumerate(selected_albums, 1):
                        try:
                            # Get full album data with tracks
                            album_data = self.spotify_client.get_album(album.id)

                            if not album_data or 'tracks' not in album_data:
                                continue

                            tracks = album_data['tracks'].get('items', [])
                            logger.debug(f"    Album {album_idx}: {album_data.get('name', 'Unknown')} ({len(tracks)} tracks)")

                            # Determine if this is a new release (within last 30 days)
                            is_new = False
                            try:
                                release_date_str = album_data.get('release_date', '')
                                if release_date_str:
                                    if len(release_date_str) == 10:  # Full date
                                        release_date = datetime.strptime(release_date_str, "%Y-%m-%d")
                                        days_old = (datetime.now() - release_date).days
                                        is_new = days_old <= 30
                            except:
                                pass

                            # Add each track to discovery pool
                            for track in tracks:
                                try:
                                    # Build track data for discovery pool
                                    track_data = {
                                        'spotify_track_id': track['id'],
                                        'spotify_album_id': album_data['id'],
                                        'spotify_artist_id': similar_artist.similar_artist_spotify_id,
                                        'track_name': track['name'],
                                        'artist_name': similar_artist.similar_artist_name,
                                        'album_name': album_data.get('name', 'Unknown Album'),
                                        'album_cover_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                                        'duration_ms': track.get('duration_ms', 0),
                                        'popularity': album_data.get('popularity', 0),
                                        'release_date': album_data.get('release_date', ''),
                                        'is_new_release': is_new,
                                        'track_data_json': track  # Store full Spotify track object
                                    }

                                    # Add to discovery pool
                                    if self.database.add_to_discovery_pool(track_data):
                                        total_tracks_added += 1

                                except Exception as track_error:
                                    logger.debug(f"Error adding track to discovery pool: {track_error}")
                                    continue

                            # Small delay between albums
                            time.sleep(DELAY_BETWEEN_ALBUMS)

                        except Exception as album_error:
                            logger.warning(f"Error processing album: {album_error}")
                            continue

                    # Delay between artists
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
                        FROM albums_new a
                        JOIN artists_new ar ON a.artist_id = ar.id
                        ORDER BY RANDOM()
                        LIMIT 5
                    """)
                    db_albums = cursor.fetchall()

                    logger.info(f"Processing {len(db_albums)} database albums for discovery pool")

                    for db_idx, album_row in enumerate(db_albums, 1):
                        try:
                            # Search for album on Spotify
                            query = f"album:{album_row['title']} artist:{album_row['artist_name']}"
                            search_results = self.spotify_client.search_albums(query, limit=1)

                            if search_results and len(search_results) > 0:
                                spotify_album = search_results[0]
                                album_data = self.spotify_client.get_album(spotify_album.id)

                                if album_data and 'tracks' in album_data:
                                    tracks = album_data['tracks'].get('items', [])

                                    # Check if new release
                                    is_new = False
                                    try:
                                        release_date_str = album_data.get('release_date', '')
                                        if release_date_str and len(release_date_str) == 10:
                                            release_date = datetime.strptime(release_date_str, "%Y-%m-%d")
                                            days_old = (datetime.now() - release_date).days
                                            is_new = days_old <= 30
                                    except:
                                        pass

                                    for track in tracks:
                                        try:
                                            track_data = {
                                                'spotify_track_id': track['id'],
                                                'spotify_album_id': album_data['id'],
                                                'spotify_artist_id': album_data['artists'][0]['id'] if album_data.get('artists') else '',
                                                'track_name': track['name'],
                                                'artist_name': album_row['artist_name'],
                                                'album_name': album_row['title'],
                                                'album_cover_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                                                'duration_ms': track.get('duration_ms', 0),
                                                'popularity': album_data.get('popularity', 0),
                                                'release_date': album_data.get('release_date', ''),
                                                'is_new_release': is_new,
                                                'track_data_json': track
                                            }

                                            if self.database.add_to_discovery_pool(track_data):
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

    def cache_discovery_recent_albums(self):
        """Cache recent albums from watchlist and similar artists for discover page"""
        try:
            from datetime import datetime, timedelta
            import random

            logger.info("Caching recent albums for discover page...")

            # Clear existing cache
            self.database.clear_discovery_recent_albums()

            cutoff_date = datetime.now() - timedelta(days=90)  # 3 months
            cached_count = 0
            albums_checked = 0

            # Get watchlist artists (10 random for more variety)
            watchlist_artists = self.database.get_watchlist_artists()
            watchlist_sample = random.sample(watchlist_artists, min(10, len(watchlist_artists))) if watchlist_artists else []

            # Get similar artists (10 random from top 30 for more variety)
            similar_artists = self.database.get_top_similar_artists(limit=30)
            similar_sample = random.sample(similar_artists, min(10, len(similar_artists))) if similar_artists else []

            logger.info(f"Checking albums from {len(watchlist_sample)} watchlist + {len(similar_sample)} similar artists for recent releases")

            # Process watchlist artists
            for artist in watchlist_sample:
                try:
                    albums = self.spotify_client.get_artist_albums(
                        artist.spotify_artist_id,
                        album_type='album,single',
                        limit=20
                    )

                    for album in albums:
                        try:
                            albums_checked += 1
                            if hasattr(album, 'release_date') and album.release_date:
                                release_str = album.release_date
                                if len(release_str) >= 10:
                                    release_date = datetime.strptime(release_str[:10], "%Y-%m-%d")
                                    if release_date >= cutoff_date:
                                        album_data = {
                                            'album_spotify_id': album.id,
                                            'album_name': album.name,
                                            'artist_name': artist.artist_name,
                                            'artist_spotify_id': artist.spotify_artist_id,
                                            'album_cover_url': album.image_url if hasattr(album, 'image_url') else None,
                                            'release_date': release_str,
                                            'album_type': album.album_type if hasattr(album, 'album_type') else 'album'
                                        }
                                        if self.database.cache_discovery_recent_album(album_data):
                                            cached_count += 1
                                            logger.debug(f"Cached recent album: {album.name} by {artist.artist_name} ({release_str})")
                        except Exception as e:
                            logger.warning(f"Error checking album for recent releases: {e}")
                            continue

                except Exception as e:
                    logger.debug(f"Error fetching albums for watchlist artist {artist.artist_name}: {e}")
                    continue

                # Rate limiting between artists
                time.sleep(DELAY_BETWEEN_ARTISTS)

            # Process similar artists
            for artist in similar_sample:
                try:
                    albums = self.spotify_client.get_artist_albums(
                        artist.similar_artist_spotify_id,
                        album_type='album,single',
                        limit=20
                    )

                    for album in albums:
                        try:
                            albums_checked += 1
                            if hasattr(album, 'release_date') and album.release_date:
                                release_str = album.release_date
                                if len(release_str) >= 10:
                                    release_date = datetime.strptime(release_str[:10], "%Y-%m-%d")
                                    if release_date >= cutoff_date:
                                        album_data = {
                                            'album_spotify_id': album.id,
                                            'album_name': album.name,
                                            'artist_name': artist.similar_artist_name,
                                            'artist_spotify_id': artist.similar_artist_spotify_id,
                                            'album_cover_url': album.image_url if hasattr(album, 'image_url') else None,
                                            'release_date': release_str,
                                            'album_type': album.album_type if hasattr(album, 'album_type') else 'album'
                                        }
                                        if self.database.cache_discovery_recent_album(album_data):
                                            cached_count += 1
                                            logger.debug(f"Cached recent album: {album.name} by {artist.similar_artist_name} ({release_str})")
                        except Exception as e:
                            logger.warning(f"Error checking album for recent releases: {e}")
                            continue

                except Exception as e:
                    logger.debug(f"Error fetching albums for similar artist {artist.similar_artist_name}: {e}")
                    continue

                # Rate limiting between artists
                time.sleep(DELAY_BETWEEN_ARTISTS)

            logger.info(f"Cached {cached_count} recent albums from {albums_checked} albums checked (cutoff: {cutoff_date.strftime('%Y-%m-%d')})")

        except Exception as e:
            logger.error(f"Error caching discovery recent albums: {e}")
            import traceback
            traceback.print_exc()

    def curate_discovery_playlists(self):
        """Curate consistent playlist selections that stay the same until next discovery pool update"""
        try:
            import random

            logger.info("Curating Release Radar playlist...")

            # 1. Curate Release Radar - 50 tracks from recent albums
            recent_albums = self.database.get_discovery_recent_albums(limit=20)
            release_radar_tracks = []

            if recent_albums:
                # Group albums by artist for variety
                albums_by_artist = {}
                for album in recent_albums:
                    artist = album['artist_name']
                    if artist not in albums_by_artist:
                        albums_by_artist[artist] = []
                    albums_by_artist[artist].append(album)

                # Get tracks from each album, grouped by artist
                # Also add these tracks to discovery pool for fast lookup
                artist_tracks = {}
                artist_track_data = {}  # Store full track data for discovery pool

                for artist, albums in albums_by_artist.items():
                    artist_tracks[artist] = []
                    artist_track_data[artist] = []

                    for album in albums:
                        try:
                            album_data = self.spotify_client.get_album(album['album_spotify_id'])
                            if album_data and 'tracks' in album_data:
                                for track in album_data['tracks']['items']:
                                    track_id = track['id']
                                    artist_tracks[artist].append(track_id)

                                    # Store full track data for discovery pool
                                    full_track = {
                                        'id': track_id,
                                        'name': track['name'],
                                        'artists': track.get('artists', []),
                                        'album': album_data,
                                        'duration_ms': track.get('duration_ms', 0)
                                    }
                                    artist_track_data[artist].append(full_track)

                        except Exception as e:
                            continue

                # Balance by artist - max 6 tracks per artist
                balanced_tracks = []
                balanced_track_data = []

                for artist, tracks in artist_tracks.items():
                    # Shuffle and get indices
                    indices = list(range(len(tracks)))
                    random.shuffle(indices)
                    selected_indices = indices[:6]

                    # Add selected tracks
                    for idx in selected_indices:
                        balanced_tracks.append(tracks[idx])
                        balanced_track_data.append(artist_track_data[artist][idx])

                # Shuffle and limit to 50
                combined = list(zip(balanced_tracks, balanced_track_data))
                random.shuffle(combined)
                combined = combined[:50]

                release_radar_tracks = [track_id for track_id, _ in combined]
                release_radar_track_data = [track_data for _, track_data in combined]

                # Add Release Radar tracks to discovery pool so they're available for fast lookup
                logger.info(f"Adding {len(release_radar_track_data)} Release Radar tracks to discovery pool...")
                for track_data in release_radar_track_data:
                    try:
                        # Format track data for discovery pool (expects specific structure)
                        formatted_track = {
                            'spotify_track_id': track_data['id'],
                            'spotify_album_id': track_data['album'].get('id', ''),
                            'spotify_artist_id': track_data['artists'][0]['id'] if track_data['artists'] else '',
                            'track_name': track_data['name'],
                            'artist_name': track_data['artists'][0]['name'] if track_data['artists'] else 'Unknown',
                            'album_name': track_data['album'].get('name', 'Unknown'),
                            'album_cover_url': track_data['album']['images'][0]['url'] if track_data['album'].get('images') else None,
                            'duration_ms': track_data.get('duration_ms', 0),
                            'popularity': track_data.get('popularity', 0),
                            'release_date': track_data['album'].get('release_date', ''),
                            'is_new_release': True,
                            'track_data_json': track_data
                        }
                        self.database.add_to_discovery_pool(formatted_track)
                    except Exception as e:
                        logger.warning(f"Failed to add track {track_data['name']} to discovery pool: {e}")
                        continue

            self.database.save_curated_playlist('release_radar', release_radar_tracks)
            logger.info(f"Release Radar curated: {len(release_radar_tracks)} tracks")

            # 2. Curate Discovery Weekly - 50 tracks from full discovery pool
            logger.info("Curating Discovery Weekly playlist...")
            discovery_tracks = self.database.get_discovery_pool_tracks(limit=1000, new_releases_only=False)

            discovery_weekly_tracks = []
            if discovery_tracks:
                all_track_ids = [track.spotify_track_id for track in discovery_tracks]
                random.shuffle(all_track_ids)
                discovery_weekly_tracks = all_track_ids[:50]

            self.database.save_curated_playlist('discovery_weekly', discovery_weekly_tracks)
            logger.info(f"Discovery Weekly curated: {len(discovery_weekly_tracks)} tracks")

            logger.info("Playlist curation complete")

        except Exception as e:
            logger.error(f"Error curating discovery playlists: {e}")
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