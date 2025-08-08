#!/usr/bin/env python3

from PyQt6.QtCore import QThread, pyqtSignal
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List
from datetime import datetime
import time

from database import get_database, MusicDatabase
from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("database_update_worker")

class DatabaseUpdateWorker(QThread):
    """Worker thread for updating SoulSync database with Plex library data"""
    
    # Signals for progress reporting
    progress_updated = pyqtSignal(str, int, int, float)  # current_item, processed, total, percentage
    artist_processed = pyqtSignal(str, bool, str, int, int)  # artist_name, success, details, albums_count, tracks_count
    finished = pyqtSignal(int, int, int, int, int)  # total_artists, total_albums, total_tracks, successful, failed
    error = pyqtSignal(str)  # error_message
    phase_changed = pyqtSignal(str)  # current_phase (artists, albums, tracks)
    
    def __init__(self, plex_client, database_path: str = "database/music_library.db", full_refresh: bool = False):
        super().__init__()
        self.plex_client = plex_client
        self.database_path = database_path
        self.full_refresh = full_refresh
        self.should_stop = False
        
        # Statistics tracking
        self.processed_artists = 0
        self.processed_albums = 0
        self.processed_tracks = 0
        self.successful_operations = 0
        self.failed_operations = 0
        
        # Threading control - get from config or default to 5
        database_config = config_manager.get('database', {})
        self.max_workers = database_config.get('max_workers', 5)
        logger.info(f"Using {self.max_workers} worker threads for database update")
        self.thread_lock = threading.Lock()
        
        # Database instance
        self.database: Optional[MusicDatabase] = None
    
    def stop(self):
        """Stop the database update process"""
        self.should_stop = True
    
    def run(self):
        """Main worker thread execution"""
        try:
            # Initialize database
            self.database = get_database(self.database_path)
            
            if self.full_refresh:
                logger.info("Performing full database refresh - clearing existing data")
                self.database.clear_all_data()
                # For full refresh, use the old method (all artists)
                artists_to_process = self._get_all_artists()
                if not artists_to_process:
                    self.error.emit("No artists found in Plex library or connection failed")
                    return
                logger.info(f"Full refresh: Found {len(artists_to_process)} artists in Plex library")
            else:
                logger.info("Performing smart incremental update - checking recently added content")
                # For incremental, use smart recent-first approach
                self.phase_changed.emit("Finding recently added content...")
                artists_to_process = self._get_artists_for_incremental_update()
                if not artists_to_process:
                    logger.info("No new content found - database is up to date")
                    self.finished.emit(0, 0, 0, 0, 0)
                    return
                logger.info(f"Incremental update: Found {len(artists_to_process)} artists to process")
            
            # Phase 2: Process artists and their albums/tracks
            self.phase_changed.emit("Processing artists, albums, and tracks...")
            self._process_all_artists(artists_to_process)
            
            # Record full refresh completion for tracking purposes
            if self.full_refresh and self.database:
                try:
                    self.database.record_full_refresh_completion()
                    logger.info("Full refresh completion recorded in database")
                except Exception as e:
                    logger.warning(f"Could not record full refresh completion: {e}")
            
            # Emit final results
            self.finished.emit(
                self.processed_artists,
                self.processed_albums, 
                self.processed_tracks,
                self.successful_operations,
                self.failed_operations
            )
            
            update_type = "Full refresh" if self.full_refresh else "Incremental update"
            logger.info(f"{update_type} completed: {self.processed_artists} artists, "
                       f"{self.processed_albums} albums, {self.processed_tracks} tracks processed")
            
        except Exception as e:
            logger.error(f"Database update failed: {str(e)}")
            self.error.emit(f"Database update failed: {str(e)}")
    
    def _get_all_artists(self) -> List:
        """Get all artists from Plex library"""
        try:
            if not self.plex_client.ensure_connection():
                logger.error("Could not connect to Plex server")
                return []
            
            artists = self.plex_client.get_all_artists()
            return artists
            
        except Exception as e:
            logger.error(f"Error getting artists from Plex: {e}")
            return []
    
    def _get_artists_for_incremental_update(self) -> List:
        """Get artists that need processing for incremental update using smart early-stopping logic"""
        try:
            if not self.plex_client.ensure_connection():
                logger.error("Could not connect to Plex server")
                return []
            
            if not self.plex_client.music_library:
                logger.error("No music library found in Plex")
                return []
            
            # Check if database has enough content for incremental updates
            try:
                stats = self.database.get_database_info()
                track_count = stats.get('tracks', 0)
                
                if track_count < 100:  # Minimum threshold for meaningful incremental updates
                    logger.warning(f"Database has only {track_count} tracks - insufficient for incremental updates")
                    logger.info("Switching to full refresh mode (incremental updates require established database)")
                    # Switch to full refresh automatically
                    self.full_refresh = True
                    return self._get_all_artists()
                    
                logger.info(f"Database has {track_count} tracks - proceeding with incremental update")
                
            except Exception as e:
                logger.warning(f"Could not check database state: {e} - defaulting to full refresh")
                self.full_refresh = True
                return self._get_all_artists()
            
            # Strategy: Get recently added albums and extract artists from them
            # Process artists in reverse chronological order until we hit one that's already current
            
            logger.info("Getting recently added albums to find new artists...")
            
            # Get recently added albums (up to 500 to cast a wide net)  
            try:
                # Try to get specifically albums first
                try:
                    recent_content = self.plex_client.music_library.recentlyAdded(libtype='album', maxresults=500)
                    logger.info(f"Found {len(recent_content)} recently added albums (album-specific)")
                except:
                    # Fallback to general recently added
                    recent_content = self.plex_client.music_library.recentlyAdded(maxresults=500)
                    logger.info(f"Found {len(recent_content)} recently added items (mixed types)")
                
                # Filter to only get Album objects and convert Artist objects to their albums
                recent_albums = []
                artist_count = 0
                album_count = 0
                
                for item in recent_content:
                    try:
                        item_type = type(item).__name__
                        logger.info(f"Processing recently added item: {item_type} - '{getattr(item, 'title', 'Unknown')}'")
                        
                        if hasattr(item, 'tracks') and hasattr(item, 'artist'):
                            # This is an Album - add directly
                            recent_albums.append(item)
                            album_count += 1
                            logger.info(f"✅ Added album directly: '{item.title}'")
                        elif hasattr(item, 'albums'):
                            # This is an Artist - get their albums
                            try:
                                artist_albums = list(item.albums())
                                if artist_albums:
                                    recent_albums.extend(artist_albums)
                                    artist_count += 1
                                    logger.info(f"✅ Added {len(artist_albums)} albums from artist '{item.title}'")
                                else:
                                    logger.info(f"⚠️ Artist '{item.title}' has no albums")
                            except Exception as albums_error:
                                logger.warning(f"Error getting albums from artist '{getattr(item, 'title', 'Unknown')}': {albums_error}")
                        else:
                            # Unknown type - skip
                            logger.info(f"❌ Skipping unsupported type: {item_type} (has tracks: {hasattr(item, 'tracks')}, has albums: {hasattr(item, 'albums')}, has artist: {hasattr(item, 'artist')})")
                    except Exception as e:
                        logger.warning(f"Error processing recently added item: {e}")
                        continue
                
                logger.info(f"Processed recently added content: {artist_count} artists → albums, {album_count} direct albums")
                
                logger.info(f"Extracted {len(recent_albums)} albums from recently added content")
            except Exception as e:
                logger.warning(f"Could not get recently added albums: {e}")
                # Fallback: get recently added tracks instead
                try:
                    recent_tracks = self.plex_client.music_library.recentlyAdded(libtype='track', maxresults=1000)
                    logger.info(f"Fallback: Found {len(recent_tracks)} recently added tracks")
                    # Extract albums from tracks
                    recent_albums = []
                    seen_albums = set()
                    for track in recent_tracks:
                        try:
                            album = track.album()
                            if album and album.ratingKey not in seen_albums:
                                recent_albums.append(album)
                                seen_albums.add(album.ratingKey)
                        except:
                            continue
                    logger.info(f"Extracted {len(recent_albums)} unique albums from tracks")
                except Exception as e2:
                    logger.error(f"Could not get recently added content: {e2}")
                    return []
            
            if not recent_albums:
                logger.info("No recently added albums found")
                return []
            
            # Sort albums by added date (newest first)
            try:
                recent_albums.sort(key=lambda x: getattr(x, 'addedAt', 0), reverse=True)
                logger.info("Sorted albums by recently added date (newest first)")
            except Exception as e:
                logger.warning(f"Could not sort albums by date: {e}")
            
            # Extract artists from recent albums with early stopping logic
            artists_to_process = []
            processed_artist_ids = set()
            stopped_early = False
            
            logger.info("Checking artists from recent albums (with early stopping)...")
            
            # Debug: log the types of objects we're processing
            object_types = {}
            for item in recent_albums[:10]:  # Check first 10 items
                item_type = type(item).__name__
                object_types[item_type] = object_types.get(item_type, 0) + 1
            logger.info(f"Recent albums object types (first 10): {object_types}")
            
            if not recent_albums:
                logger.warning("No albums found to process - incremental update cannot proceed")
                return []
            
            # New approach: Track-level incremental update with 3-consecutive-tracks stopping
            consecutive_existing_tracks = 0
            processed_artist_ids = set()
            total_tracks_checked = 0
            
            for i, album in enumerate(recent_albums):
                if self.should_stop:
                    break
                
                try:
                    # Defensive check: ensure this is actually an album object
                    if not hasattr(album, 'tracks') or not hasattr(album, 'artist'):
                        logger.warning(f"Skipping invalid album object at index {i}: {type(album).__name__}")
                        continue
                    
                    album_title = getattr(album, 'title', f'Album_{i}')
                    album_has_new_tracks = False
                    
                    # Check each individual track in this album
                    try:
                        tracks = list(album.tracks())
                        logger.debug(f"Checking {len(tracks)} tracks in album '{album_title}'")
                        
                        for track in tracks:
                            total_tracks_checked += 1
                            try:
                                track_id = int(track.ratingKey)
                                track_title = getattr(track, 'title', 'Unknown Track')
                                
                                if self.database.track_exists(track_id):
                                    consecutive_existing_tracks += 1
                                    logger.debug(f"Track '{track_title}' already exists (consecutive: {consecutive_existing_tracks})")
                                    
                                    # Stop after 3 consecutive existing tracks
                                    if consecutive_existing_tracks >= 3:
                                        logger.info(f"🛑 Found 3 consecutive existing tracks - stopping incremental scan after checking {total_tracks_checked} tracks")
                                        stopped_early = True
                                        break
                                else:
                                    # Found missing track - reset counter
                                    if consecutive_existing_tracks > 0:
                                        logger.debug(f"Track '{track_title}' missing - resetting consecutive count (was {consecutive_existing_tracks})")
                                    consecutive_existing_tracks = 0
                                    album_has_new_tracks = True
                                    logger.debug(f"📀 Track '{track_title}' is new - will process album's artist")
                                    
                            except Exception as track_error:
                                logger.debug(f"Error checking individual track: {track_error}")
                                # Reset counter on error to be safe
                                consecutive_existing_tracks = 0
                                album_has_new_tracks = True  # Assume needs processing if can't check
                                continue
                                
                        # If we hit the stop condition, break out of album loop too
                        if stopped_early:
                            break
                            
                    except Exception as tracks_error:
                        logger.warning(f"Error getting tracks for album '{album_title}': {tracks_error}")
                        # Assume album needs processing if we can't check tracks
                        album_has_new_tracks = True
                        consecutive_existing_tracks = 0
                    
                    # If album has new tracks, queue its artist for processing
                    if album_has_new_tracks:
                        try:
                            album_artist = album.artist()
                            if album_artist:
                                artist_id = int(album_artist.ratingKey)
                                
                                # Skip if we've already queued this artist
                                if artist_id not in processed_artist_ids:
                                    processed_artist_ids.add(artist_id)
                                    artists_to_process.append(album_artist)
                                    logger.info(f"✅ Added artist '{album_artist.title}' for processing (from album '{album_title}' with new tracks)")
                        except Exception as artist_error:
                            logger.warning(f"Error getting artist for album '{album_title}': {artist_error}")
                
                except Exception as e:
                    logger.warning(f"Error processing album at index {i} (type: {type(album).__name__}): {e}")
                    # Reset consecutive count on error to be safe
                    consecutive_existing_tracks = 0
                    continue
            
            result_msg = f"Smart incremental scan result: {len(artists_to_process)} artists to process"
            if stopped_early:
                result_msg += f" (stopped early after finding 3 consecutive existing tracks)"
            else:
                result_msg += f" (checked {total_tracks_checked} tracks from {len(recent_albums)} albums)"
            
            logger.info(result_msg)
            return artists_to_process
            
        except Exception as e:
            logger.error(f"Error in smart incremental update: {e}")
            # Fallback to empty list - user can try full refresh
            return []
    
    def _process_all_artists(self, artists: List):
        """Process all artists and their albums/tracks using thread pool"""
        total_artists = len(artists)
        
        def process_single_artist(artist):
            """Process a single artist and return results"""
            if self.should_stop:
                return None
            
            try:
                artist_name = getattr(artist, 'title', 'Unknown Artist')
                
                # Update progress
                with self.thread_lock:
                    self.processed_artists += 1
                    progress_percent = (self.processed_artists / total_artists) * 100
                
                self.progress_updated.emit(
                    f"Processing {artist_name}",
                    self.processed_artists,
                    total_artists,
                    progress_percent
                )
                
                # Process the artist
                success, details, album_count, track_count = self._process_artist_with_content(artist)
                
                # Track statistics
                with self.thread_lock:
                    if success:
                        self.successful_operations += 1
                    else:
                        self.failed_operations += 1
                    
                    self.processed_albums += album_count
                    self.processed_tracks += track_count
                
                return (artist_name, success, details, album_count, track_count)
                
            except Exception as e:
                logger.error(f"Error processing artist {getattr(artist, 'title', 'Unknown')}: {e}")
                return (getattr(artist, 'title', 'Unknown'), False, f"Error: {str(e)}", 0, 0)
        
        # Process artists in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_artist = {executor.submit(process_single_artist, artist): artist 
                              for artist in artists}
            
            # Process completed tasks as they finish
            for future in as_completed(future_to_artist):
                if self.should_stop:
                    break
                
                result = future.result()
                if result is None:  # Task was cancelled
                    continue
                
                artist_name, success, details, album_count, track_count = result
                
                # Emit progress signal
                self.artist_processed.emit(artist_name, success, details, album_count, track_count)
    
    def _process_artist_with_content(self, plex_artist) -> tuple[bool, str, int, int]:
        """Process an artist and all their albums and tracks"""
        try:
            artist_name = getattr(plex_artist, 'title', 'Unknown Artist')
            
            # 1. Insert/update the artist
            artist_success = self.database.insert_or_update_artist(plex_artist)
            if not artist_success:
                return False, "Failed to update artist data", 0, 0
            
            artist_id = int(plex_artist.ratingKey)
            
            # 2. Get all albums for this artist
            try:
                albums = list(plex_artist.albums())
            except Exception as e:
                logger.warning(f"Could not get albums for artist '{artist_name}': {e}")
                return True, "Artist updated (no albums accessible)", 0, 0
            
            album_count = 0
            track_count = 0
            
            # 3. Process each album
            for album in albums:
                if self.should_stop:
                    break
                
                try:
                    # Insert/update album
                    album_success = self.database.insert_or_update_album(album, artist_id)
                    if album_success:
                        album_count += 1
                        album_id = int(album.ratingKey)
                        
                        # 4. Process tracks in this album
                        try:
                            tracks = list(album.tracks())
                            
                            for track in tracks:
                                if self.should_stop:
                                    break
                                
                                try:
                                    track_success = self.database.insert_or_update_track(track, album_id, artist_id)
                                    if track_success:
                                        track_count += 1
                                except Exception as e:
                                    logger.warning(f"Failed to process track '{getattr(track, 'title', 'Unknown')}': {e}")
                                    
                        except Exception as e:
                            logger.warning(f"Could not get tracks for album '{getattr(album, 'title', 'Unknown')}': {e}")
                    
                except Exception as e:
                    logger.warning(f"Failed to process album '{getattr(album, 'title', 'Unknown')}': {e}")
            
            details = f"Updated with {album_count} albums, {track_count} tracks"
            return True, details, album_count, track_count
            
        except Exception as e:
            logger.error(f"Error processing artist '{getattr(plex_artist, 'title', 'Unknown')}': {e}")
            return False, f"Processing error: {str(e)}", 0, 0

class DatabaseStatsWorker(QThread):
    """Simple worker for getting database statistics without blocking UI"""
    
    stats_updated = pyqtSignal(dict)  # Database statistics
    
    def __init__(self, database_path: str = "database/music_library.db"):
        super().__init__()
        self.database_path = database_path
        self.should_stop = False
    
    def stop(self):
        """Stop the worker"""
        self.should_stop = True
    
    def run(self):
        """Get database statistics and full info including last refresh"""
        try:
            if self.should_stop:
                return
                
            database = get_database(self.database_path)
            if self.should_stop:
                return
                
            # Get full database info (includes last_full_refresh)
            info = database.get_database_info()
            if not self.should_stop:
                self.stats_updated.emit(info)
        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            if not self.should_stop:
                self.stats_updated.emit({
                    'artists': 0,
                    'albums': 0, 
                    'tracks': 0,
                    'database_size_mb': 0.0,
                    'last_update': None,
                    'last_full_refresh': None
                })