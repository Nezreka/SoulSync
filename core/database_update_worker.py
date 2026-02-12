#!/usr/bin/env python3

# Conditional PyQt6 import for backward compatibility with GUI version
try:
    from PyQt6.QtCore import QThread, pyqtSignal
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False
    # Define dummy classes for headless operation
    class QThread:
        def __init__(self):
            self.callbacks = {}
        def start(self):
            import threading
            self.thread = threading.Thread(target=self.run)
            self.thread.daemon = True
            self.thread.start()
        def wait(self):
            if hasattr(self, 'thread'):
                self.thread.join()
        def emit_signal(self, signal_name, *args):
            if signal_name in self.callbacks:
                for callback in self.callbacks[signal_name]:
                    try:
                        callback(*args)
                    except Exception as e:
                        logger.error(f"Error in callback for {signal_name}: {e}")
        def connect_signal(self, signal_name, callback):
            if signal_name not in self.callbacks:
                self.callbacks[signal_name] = []
            self.callbacks[signal_name].append(callback)

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Callable
from datetime import datetime
import time

from database import get_database, MusicDatabase
from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("database_update_worker")

class DatabaseUpdateWorker(QThread):
    """Worker thread for updating SoulSync database with media server library data (Plex or Jellyfin)"""
    
    # Qt signals (only available when PyQt6 is installed)
    if QT_AVAILABLE:
        progress_updated = pyqtSignal(str, int, int, float)  # current_item, processed, total, percentage
        artist_processed = pyqtSignal(str, bool, str, int, int)  # artist_name, success, details, albums_count, tracks_count
        finished = pyqtSignal(int, int, int, int, int)  # total_artists, total_albums, total_tracks, successful, failed
        error = pyqtSignal(str)  # error_message
        phase_changed = pyqtSignal(str)  # current_phase (artists, albums, tracks)
    
    def __init__(self, media_client, database_path: str = "database/music_library.db", full_refresh: bool = False, server_type: str = "plex", force_sequential: bool = False):
        super().__init__()

        # Force sequential processing for web server mode to avoid threading issues
        self.force_sequential = force_sequential

        # Initialize signal callbacks for headless mode
        if not QT_AVAILABLE:
            self.callbacks = {
                'progress_updated': [],
                'artist_processed': [],
                'finished': [],
                'error': [],
                'phase_changed': []
            }
        
        # Support both old plex_client parameter and new media_client parameter for backward compatibility
        if hasattr(media_client, '__class__') and 'plex' in media_client.__class__.__name__.lower():
            self.media_client = media_client
            self.server_type = "plex"
            # Keep old attribute for backward compatibility
            self.plex_client = media_client
        else:
            self.media_client = media_client
            self.server_type = server_type
            # Keep old attribute for backward compatibility with existing code that expects it
            self.plex_client = media_client if server_type == "plex" else None
        
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
        base_max_workers = database_config.get('max_workers', 5)
        
        # Optimize worker count - reduce for database concurrency safety
        if self.server_type == "jellyfin":
            # Reduce workers to prevent database lock issues with bulk inserts
            self.max_workers = min(base_max_workers, 3)  # Max 3 workers for database safety
            if base_max_workers > 3:
                logger.info(f"Reducing worker count from {base_max_workers} to {self.max_workers} for Jellyfin database safety")
        elif self.server_type == "navidrome":
            # Navidrome uses standard worker count like Plex
            self.max_workers = base_max_workers
        else:
            # Plex uses standard worker count
            self.max_workers = base_max_workers
            
        logger.info(f"Using {self.max_workers} worker threads for {self.server_type} database update")
        self.thread_lock = threading.Lock()
        
        # Database instance
        self.database: Optional[MusicDatabase] = None
    
    def _emit_signal(self, signal_name: str, *args):
        """Emit a signal in both Qt and headless modes"""
        if QT_AVAILABLE and hasattr(self, signal_name):
            # Qt mode - use actual signal
            getattr(self, signal_name).emit(*args)
        elif not QT_AVAILABLE:
            # Headless mode - use callback system
            self.emit_signal(signal_name, *args)
    
    def connect_callback(self, signal_name: str, callback: Callable):
        """Connect a callback for headless mode"""
        if not QT_AVAILABLE:
            self.connect_signal(signal_name, callback)
        # In Qt mode, use the normal signal.connect() method
    
    def stop(self):
        """Stop the database update process"""
        self.should_stop = True
        
        # Clear media client cache when user stops scan to free memory
        if self.server_type in ["jellyfin", "navidrome"] and hasattr(self, 'media_client'):
            try:
                if hasattr(self.media_client, 'get_cache_stats'):
                    cache_stats = self.media_client.get_cache_stats()
                    freed_items = cache_stats.get('bulk_albums_cached', 0) + cache_stats.get('bulk_tracks_cached', 0)
                else:
                    freed_items = "unknown"
                self.media_client.clear_cache()
                logger.info(f"🧹 Cleared {self.server_type} cache after user stop - freed ~{freed_items} items from memory")
            except Exception as e:
                logger.warning(f"Could not clear {self.server_type} cache on stop: {e}")
    
    def run(self):
        """Main worker thread execution"""
        try:
            # Initialize database
            self.database = get_database(self.database_path)

            if self.full_refresh:
                logger.info(f"Performing full database refresh for {self.server_type} - clearing existing {self.server_type} data")
                self.database.clear_server_data(self.server_type)

                # Show cache preparation phase for Jellyfin and set up progress callback
                if self.server_type == "jellyfin":
                    self._emit_signal('phase_changed', "Preparing Jellyfin cache for fast processing...")
                    # Connect Jellyfin client progress to UI
                    if hasattr(self.media_client, 'set_progress_callback'):
                        self.media_client.set_progress_callback(lambda msg: self._emit_signal('phase_changed', msg))
                elif self.server_type == "navidrome":
                    self._emit_signal('phase_changed', "Connecting to Navidrome server...")
                    # Connect Navidrome client progress to UI
                    if hasattr(self.media_client, 'set_progress_callback'):
                        self.media_client.set_progress_callback(lambda msg: self._emit_signal('phase_changed', msg))
                        logger.info("✅ Connected Navidrome progress callback")

                # For full refresh, get all artists
                artists_to_process = self._get_all_artists()
                if not artists_to_process:
                    self._emit_signal('error', f"No artists found in {self.server_type} library or connection failed")
                    return
                logger.info(f"Full refresh: Found {len(artists_to_process)} artists in {self.server_type} library")
            else:
                logger.info("Performing smart incremental update - checking recently added content")
                # For incremental, use smart recent-first approach
                self._emit_signal('phase_changed', "Finding recently added content...")
                artists_to_process = self._get_artists_for_incremental_update()
                if not artists_to_process:
                    logger.info("No new content found - database is up to date")
                    self._emit_signal('finished', 0, 0, 0, 0, 0)
                    return
                logger.info(f"Incremental update: Found {len(artists_to_process)} artists to process")
            
            # Phase 2: Process artists and their albums/tracks
            self._emit_signal('phase_changed', "Processing artists, albums, and tracks...")

            # FAST PATH: For Jellyfin track-based incremental, process new tracks directly
            if self.server_type == "jellyfin" and hasattr(self, '_jellyfin_new_tracks'):
                self._process_jellyfin_new_tracks_directly(artists_to_process)
            else:
                # Standard artist processing for Plex or full refresh
                logger.info(f"🎯 About to process {len(artists_to_process) if artists_to_process else 0} artists for {self.server_type}")
                self._process_all_artists(artists_to_process)
            
            # Record full refresh completion for tracking purposes
            if self.full_refresh and self.database:
                try:
                    self.database.record_full_refresh_completion()
                    logger.info("Full refresh completion recorded in database")
                except Exception as e:
                    logger.warning(f"Could not record full refresh completion: {e}")
            
            # Clear cache after full refresh to free memory
            if self.full_refresh and self.server_type in ["jellyfin", "navidrome"]:
                try:
                    if hasattr(self.media_client, 'get_cache_stats'):
                        cache_stats = self.media_client.get_cache_stats()
                        freed_items = cache_stats.get('bulk_albums_cached', 0) + cache_stats.get('bulk_tracks_cached', 0)
                    else:
                        freed_items = "cache data"
                    self.media_client.clear_cache()
                    logger.info(f"🧹 Cleared {self.server_type} cache after full refresh - freed ~{freed_items} items from memory")
                except Exception as e:
                    logger.warning(f"Could not clear {self.server_type} cache: {e}")
            
            # Cleanup orphaned records after incremental updates (catches fixed matches)
            if not self.full_refresh and self.database:
                try:
                    cleanup_results = self.database.cleanup_orphaned_records()
                    orphaned_artists = cleanup_results.get('orphaned_artists_removed', 0)
                    orphaned_albums = cleanup_results.get('orphaned_albums_removed', 0)
                    
                    if orphaned_artists > 0 or orphaned_albums > 0:
                        logger.info(f"🧹 Cleanup complete: {orphaned_artists} orphaned artists, {orphaned_albums} orphaned albums removed")
                    else:
                        logger.debug("🧹 Cleanup complete: No orphaned records found")
                        
                except Exception as e:
                    logger.warning(f"Could not cleanup orphaned records: {e}")
            
            # Emit final results
            self._emit_signal('finished',
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
            self._emit_signal('error', f"Database update failed: {str(e)}")
    
    def _get_all_artists(self) -> List:
        """Get all artists from media server library"""
        try:
            if not self.media_client.ensure_connection():
                logger.error(f"Could not connect to {self.server_type} server")
                return []

            logger.info(f"🎯 _get_all_artists: Calling media_client.get_all_artists() for {self.server_type}")
            artists = self.media_client.get_all_artists()
            logger.info(f"🎯 _get_all_artists: Received {len(artists) if artists else 0} artists from {self.server_type}")
            return artists

        except Exception as e:
            logger.error(f"Error getting artists from {self.server_type}: {e}")
            return []
    
    def _get_artists_for_incremental_update(self) -> List:
        """Get artists that need processing for incremental update using smart early-stopping logic"""
        try:
            if not self.media_client.ensure_connection():
                logger.error(f"Could not connect to {self.server_type} server")
                return []
            
            # Check for music library (Plex-specific check)
            if self.server_type == "plex" and not self.media_client.music_library:
                logger.error("No music library found in Plex")
                return []
            
            # Check if database has enough content for incremental updates (server-specific)
            try:
                # Get stats for the specific server we're updating
                if hasattr(self.database, 'get_database_info_for_server'):
                    stats = self.database.get_database_info_for_server(self.server_type)
                else:
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
            
            # Enhanced Strategy: Get both recently added AND recently updated content
            # This catches both new content and metadata corrections done on the server
            
            logger.info(f"Getting recently added and recently updated content from {self.server_type}...")
            
            # For Jellyfin, we need to set up progress callback for potential cache population during incremental
            if self.server_type == "jellyfin":
                if hasattr(self.media_client, 'set_progress_callback'):
                    self.media_client.set_progress_callback(lambda msg: self._emit_signal('phase_changed', f"Incremental: {msg}"))
            elif self.server_type == "navidrome":
                # Navidrome doesn't need cache preparation for incremental updates
                logger.info("Navidrome incremental update: no caching needed")
            
            # PERFORMANCE BREAKTHROUGH: For Jellyfin, use track-based incremental (much faster)
            if self.server_type == "jellyfin":
                return self._get_artists_for_jellyfin_track_incremental_update()
            elif self.server_type == "navidrome":
                # Navidrome: simple approach - get all artists and check what's new in database
                return self._get_artists_for_navidrome_incremental_update()

            # Plex uses album-based approach (established and working)
            recent_albums = self._get_recent_albums_for_server()
            if not recent_albums:
                logger.info("No recently added albums found")
                return []
            
            # Sort albums by added date (newest first) - handle None dates properly
            try:
                def get_sort_date(album):
                    date_val = getattr(album, 'addedAt', None)
                    if date_val is None:
                        return 0  # Fallback for albums with no date
                    return date_val
                
                recent_albums.sort(key=get_sort_date, reverse=True)
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
            
            # Improved approach: Album-level incremental update with smart stopping
            # Check entire albums at a time and use more robust stopping criteria
            albums_with_new_content = 0
            consecutive_complete_albums = 0
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
                    missing_tracks_count = 0
                    
                    # Check each individual track in this album
                    try:
                        tracks = list(album.tracks())
                        logger.debug(f"Checking {len(tracks)} tracks in album '{album_title}'")
                        
                        for track in tracks:
                            total_tracks_checked += 1
                            try:
                                # Handle both Plex (integer) and Jellyfin (string GUID) IDs
                                track_id = str(track.ratingKey)
                                track_title = getattr(track, 'title', 'Unknown Track')
                                
                                # Use server-aware track existence check
                                if hasattr(self.database, 'track_exists_by_server'):
                                    track_exists = self.database.track_exists_by_server(track_id, self.server_type)
                                else:
                                    # Fallback to generic check (works for string IDs)
                                    track_exists = self.database.track_exists(track_id)
                                
                                if not track_exists:
                                    missing_tracks_count += 1
                                    album_has_new_tracks = True
                                    logger.debug(f"📀 Track '{track_title}' is new - album needs processing")
                                else:
                                    logger.debug(f"✅ Track '{track_title}' already exists")
                                    
                            except Exception as track_error:
                                logger.debug(f"Error checking individual track: {track_error}")
                                album_has_new_tracks = True  # Assume needs processing if can't check
                                missing_tracks_count += 1
                                continue
                        
                        # Evaluate album completion status
                        if album_has_new_tracks:
                            albums_with_new_content += 1
                            consecutive_complete_albums = 0  # Reset counter
                            logger.info(f"📀 Album '{album_title}' has {missing_tracks_count} new tracks - needs processing")
                        else:
                            # Check if existing tracks have metadata changes (catches Plex corrections)
                            metadata_changed = self._check_for_metadata_changes(tracks)
                            if metadata_changed:
                                albums_with_new_content += 1
                                consecutive_complete_albums = 0  # Reset counter
                                logger.info(f"🔄 Album '{album_title}' has metadata changes - needs processing")
                                album_has_new_tracks = True  # Mark for artist processing
                            else:
                                consecutive_complete_albums += 1
                                logger.debug(f"✅ Album '{album_title}' is fully up-to-date (consecutive complete: {consecutive_complete_albums})")
                                
                                # Very conservative stopping criteria: 25 consecutive complete albums after metadata fixes
                                # This ensures we don't miss scattered updated content from manual corrections
                                if consecutive_complete_albums >= 25:
                                    logger.info(f"🛑 Found 25 consecutive complete albums - stopping incremental scan after checking {total_tracks_checked} tracks from {i+1} albums")
                                    stopped_early = True
                                    break
                            
                    except Exception as tracks_error:
                        logger.warning(f"Error getting tracks for album '{album_title}': {tracks_error}")
                        # Assume album needs processing if we can't check tracks
                        album_has_new_tracks = True
                        consecutive_complete_albums = 0  # Reset the correct variable
                    
                    # If album has new tracks, queue its artist for processing
                    if album_has_new_tracks:
                        try:
                            album_artist = album.artist()
                            if album_artist:
                                # Handle both Plex (integer) and Jellyfin (string GUID) artist IDs
                                artist_id = str(album_artist.ratingKey)
                                
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
                    consecutive_complete_albums = 0
                    continue
            
            result_msg = f"Smart incremental scan result: {len(artists_to_process)} artists to process from {albums_with_new_content} albums with new content"
            if stopped_early:
                result_msg += f" (stopped early after finding 25 consecutive complete albums)"
            else:
                result_msg += f" (checked all {total_tracks_checked} tracks from {len(recent_albums)} recent albums)"
            
            logger.info(f"📊 Incremental scan stats: {len(recent_albums)} recent albums examined, {albums_with_new_content} needed processing")
            
            logger.info(result_msg)
            return artists_to_process
            
        except Exception as e:
            logger.error(f"Error in smart incremental update: {e}")
            # Fallback to empty list - user can try full refresh
            return []

    def _get_artists_for_navidrome_incremental_update(self) -> List:
        """Get artists for Navidrome incremental update using smart early-stopping logic like Plex/Jellyfin"""
        try:
            logger.info("🎵 Navidrome incremental: Getting recent albums and checking for new content...")

            # Get recent albums from Navidrome (use the generic method that calls Navidrome-specific logic)
            recent_albums = self._get_recent_albums_for_server()
            if not recent_albums:
                logger.info("No recent albums found - nothing to process")
                return []

            logger.info(f"Found {len(recent_albums)} recent albums to check")

            # Sort albums by added date (newest first) - handle None dates properly
            try:
                def get_sort_date(album):
                    date_val = getattr(album, 'addedAt', None)
                    if date_val is None:
                        return 0  # Fallback for albums with no date
                    return date_val

                recent_albums.sort(key=get_sort_date, reverse=True)
                logger.info("Sorted albums by recently added date (newest first)")
            except Exception as e:
                logger.warning(f"Could not sort albums by date: {e}")

            # Extract artists from recent albums with early stopping logic (same as Plex/Jellyfin)
            artists_to_process = []
            processed_artist_ids = set()
            consecutive_complete_albums = 0
            total_tracks_checked = 0

            logger.info("Checking artists from recent albums (with early stopping)...")

            for i, album in enumerate(recent_albums):
                if self.should_stop:
                    break

                try:
                    # Ensure this is actually an album object
                    if not hasattr(album, 'tracks'):
                        logger.warning(f"Skipping invalid album object at index {i}: {type(album).__name__}")
                        continue

                    album_title = getattr(album, 'title', f'Album_{i}')
                    album_has_new_tracks = False

                    # Check if album's tracks are already in database
                    try:
                        album_tracks = album.tracks()
                        total_tracks_checked += len(album_tracks)

                        for track in album_tracks:
                            if not self.database.track_exists_by_server(track.ratingKey, self.server_type):
                                album_has_new_tracks = True
                                consecutive_complete_albums = 0  # Reset counter
                                break

                        # If no new tracks found, increment consecutive complete counter
                        if not album_has_new_tracks:
                            consecutive_complete_albums += 1
                            logger.debug(f"✅ Album '{album_title}' is up-to-date (consecutive: {consecutive_complete_albums})")

                            # Early stopping after 25 consecutive complete albums (same as Plex/Jellyfin)
                            if consecutive_complete_albums >= 25:
                                logger.info(f"🛑 Found 25 consecutive complete albums - stopping incremental scan after checking {total_tracks_checked} tracks from {i+1} albums")
                                break

                    except Exception as tracks_error:
                        logger.warning(f"Error getting tracks for album '{album_title}': {tracks_error}")
                        # Assume album needs processing if we can't check tracks
                        album_has_new_tracks = True
                        consecutive_complete_albums = 0

                    # If album has new tracks, queue its artist for processing
                    if album_has_new_tracks:
                        try:
                            album_artist = album.artist()
                            if album_artist:
                                artist_id = str(album_artist.ratingKey)

                                # Skip if we've already queued this artist
                                if artist_id not in processed_artist_ids:
                                    processed_artist_ids.add(artist_id)
                                    artists_to_process.append(album_artist)
                                    logger.info(f"✅ Added artist '{album_artist.title}' for processing (from album '{album_title}' with new tracks)")
                        except Exception as artist_error:
                            logger.warning(f"Error getting artist for album '{album_title}': {artist_error}")

                except Exception as e:
                    logger.warning(f"Error processing album at index {i}: {e}")
                    consecutive_complete_albums = 0  # Reset on error
                    continue

            logger.info(f"🎵 Navidrome incremental complete: {len(artists_to_process)} artists need processing (checked {total_tracks_checked} tracks from {len(recent_albums)} recent albums)")
            return artists_to_process

        except Exception as e:
            logger.error(f"Error in Navidrome incremental update: {e}")
            return []
    
    def _get_artists_for_jellyfin_track_incremental_update(self) -> List:
        """FAST Jellyfin incremental update using recent tracks directly (no caching needed)"""
        try:
            logger.info("🚀 FAST Jellyfin incremental: getting recent tracks directly...")
            
            # Get recent tracks directly from Jellyfin (FAST - 2 API calls)
            recent_added_tracks = self.media_client.get_recently_added_tracks(5000)
            recent_updated_tracks = self.media_client.get_recently_updated_tracks(5000)
            
            # Combine and deduplicate
            all_recent_tracks = recent_added_tracks[:]
            added_ids = {track.ratingKey for track in recent_added_tracks}
            unique_updated = [track for track in recent_updated_tracks if track.ratingKey not in added_ids]
            all_recent_tracks.extend(unique_updated)
            
            logger.info(f"Found {len(recent_added_tracks)} recent + {len(unique_updated)} updated = {len(all_recent_tracks)} tracks to check")
            
            if not all_recent_tracks:
                logger.info("No recent tracks found")
                return []
            
            # Check which tracks are actually new (FAST - database lookups only)
            new_tracks = []
            consecutive_existing_tracks = 0
            processed_artists = set()
            
            for i, track in enumerate(all_recent_tracks):
                try:
                    track_id = str(track.ratingKey)
                    
                    # Check if track exists in database
                    if hasattr(self.database, 'track_exists_by_server'):
                        track_exists = self.database.track_exists_by_server(track_id, self.server_type)
                    else:
                        track_exists = self.database.track_exists(track_id)
                    
                    if not track_exists:
                        new_tracks.append(track)
                        consecutive_existing_tracks = 0  # Reset counter
                        logger.debug(f"🎵 New track: {track.title}")
                    else:
                        consecutive_existing_tracks += 1
                        logger.debug(f"✅ Track exists: {track.title}")
                    
                    # Early stopping: if we find 100 consecutive existing tracks, we're done
                    if consecutive_existing_tracks >= 100:
                        logger.info(f"🛑 Found 100 consecutive existing tracks - stopping after checking {i+1} tracks")
                        break
                        
                except Exception as e:
                    logger.debug(f"Error checking track {getattr(track, 'title', 'Unknown')}: {e}")
                    continue
            
            logger.info(f"Found {len(new_tracks)} genuinely new tracks (early stopped after {consecutive_existing_tracks} consecutive existing)")
            
            if not new_tracks:
                logger.info("All recent tracks already exist - database is up to date")
                return []
            
            # Store new tracks for direct processing (avoid slow artist->album->track lookups)
            self._jellyfin_new_tracks = new_tracks
            
            # Extract unique artists from new tracks (FAST - no additional API calls needed)
            artists_to_process = []
            for track in new_tracks:
                try:
                    # Track already has artist info from the API call
                    track_artist = track.artist()  # This will make an API call, but only for new tracks
                    if track_artist:
                        artist_id = str(track_artist.ratingKey)
                        if artist_id not in processed_artists:
                            processed_artists.add(artist_id) 
                            artists_to_process.append(track_artist)
                            logger.info(f"✅ Added artist '{track_artist.title}' (from new track '{track.title}')")
                except Exception as e:
                    logger.debug(f"Error getting artist for track {getattr(track, 'title', 'Unknown')}: {e}")
                    continue
            
            logger.info(f"🚀 FAST incremental complete: {len(artists_to_process)} artists need processing (from {len(new_tracks)} new tracks)")
            return artists_to_process
            
        except Exception as e:
            logger.error(f"Error in fast Jellyfin incremental update: {e}")
            return []
    
    def _process_jellyfin_new_tracks_directly(self, artists_to_process):
        """Process new Jellyfin tracks directly without slow artist->album->track lookups"""
        try:
            new_tracks = getattr(self, '_jellyfin_new_tracks', [])
            if not new_tracks:
                logger.warning("No new tracks to process directly")
                return
                
            logger.info(f"🚀 FAST PROCESSING: Directly processing {len(new_tracks)} new tracks...")
            
            # Group tracks by album and artist for efficient processing
            tracks_by_album = {}
            albums_by_artist = {}
            
            for track in new_tracks:
                try:
                    # Track already has album and artist IDs from API response
                    album_id = str(track._album_id) if track._album_id else "unknown"
                    artist_id = str(track._artist_ids[0]) if track._artist_ids else "unknown"
                    
                    if album_id not in tracks_by_album:
                        tracks_by_album[album_id] = []
                    tracks_by_album[album_id].append(track)
                    
                    if artist_id not in albums_by_artist:
                        albums_by_artist[artist_id] = set()
                    albums_by_artist[artist_id].add(album_id)
                    
                except Exception as e:
                    logger.debug(f"Error grouping track {getattr(track, 'title', 'Unknown')}: {e}")
                    continue
            
            total_processed_tracks = 0
            total_processed_albums = 0
            total_processed_artists = 0
            
            # Process each artist
            for artist in artists_to_process:
                if self.should_stop:
                    break
                    
                try:
                    artist_id = str(artist.ratingKey)
                    artist_name = getattr(artist, 'title', 'Unknown Artist')
                    
                    # Insert/update the artist
                    artist_success = self.database.insert_or_update_media_artist(artist, server_source=self.server_type)
                    if artist_success:
                        total_processed_artists += 1
                    
                    # Process albums for this artist  
                    artist_album_ids = albums_by_artist.get(artist_id, set())
                    for album_id in artist_album_ids:
                        if self.should_stop:
                            break
                            
                        try:
                            # Get album from the first track (they all have the same album)
                            album_tracks = tracks_by_album[album_id]
                            if album_tracks:
                                album = album_tracks[0].album()  # Get album object
                                if album:
                                    # Insert/update album
                                    album_success = self.database.insert_or_update_media_album(album, artist_id, server_source=self.server_type)
                                    if album_success:
                                        total_processed_albums += 1
                                    
                                    # Process all tracks in this album
                                    for track in album_tracks:
                                        if self.should_stop:
                                            break
                                            
                                        try:
                                            track_success = self.database.insert_or_update_media_track(track, album_id, artist_id, server_source=self.server_type)
                                            if track_success:
                                                total_processed_tracks += 1
                                                logger.debug(f"✅ Processed new track: {track.title}")
                                        except Exception as e:
                                            logger.warning(f"Failed to process track '{getattr(track, 'title', 'Unknown')}': {e}")
                        except Exception as e:
                            logger.warning(f"Failed to process album {album_id}: {e}")
                    
                    # Emit progress for this artist
                    artist_albums = len(artist_album_ids)
                    artist_tracks = sum(len(tracks_by_album[aid]) for aid in artist_album_ids if aid in tracks_by_album)
                    self._emit_signal('artist_processed', artist_name, True, f"Processed {artist_albums} albums, {artist_tracks} tracks", artist_albums, artist_tracks)
                    
                except Exception as e:
                    logger.error(f"Error processing artist '{getattr(artist, 'title', 'Unknown')}': {e}")
                    self._emit_signal('artist_processed', getattr(artist, 'title', 'Unknown'), False, f"Error: {str(e)}", 0, 0)
            
            # Update totals
            with self.thread_lock:
                self.processed_artists += total_processed_artists
                self.processed_albums += total_processed_albums  
                self.processed_tracks += total_processed_tracks
                self.successful_operations += total_processed_artists  # Count successful artists
                
            logger.info(f"🚀 FAST PROCESSING COMPLETE: {total_processed_artists} artists, {total_processed_albums} albums, {total_processed_tracks} tracks")
            
            # Clean up
            delattr(self, '_jellyfin_new_tracks')
            
        except Exception as e:
            logger.error(f"Error in fast Jellyfin track processing: {e}")
    
    def _check_for_metadata_changes(self, media_tracks) -> bool:
        """Check if any tracks in the list have metadata changes compared to database"""
        try:
            if not self.database or not media_tracks:
                return False
            
            changes_detected = 0
            for track in media_tracks:
                try:
                    # Handle both Plex (integer) and Jellyfin (string GUID) IDs
                    track_id = str(track.ratingKey)
                    
                    # Get current data from database
                    db_track = self.database.get_track_by_id(track_id)
                    if not db_track:
                        continue  # Track doesn't exist in DB, not a metadata change
                    
                    # Compare key metadata fields that users commonly fix
                    current_title = track.title
                    current_artist = track.artist().title if track.artist() else "Unknown"
                    current_album = track.album().title if track.album() else "Unknown" 
                    
                    if (db_track.title != current_title or 
                        db_track.artist_name != current_artist or 
                        db_track.album_title != current_album):
                        logger.debug(f"🔄 Metadata change detected for track ID {track_id}:")
                        logger.debug(f"  Title: '{db_track.title}' → '{current_title}'")
                        logger.debug(f"  Artist: '{db_track.artist_name}' → '{current_artist}'")
                        logger.debug(f"  Album: '{db_track.album_title}' → '{current_album}'")
                        changes_detected += 1
                        
                except Exception as e:
                    logger.debug(f"Error checking metadata for track: {e}")
                    continue
            
            if changes_detected > 0:
                logger.info(f"🔄 Found {changes_detected} tracks with metadata changes")
                return True
                
            return False
            
        except Exception as e:
            logger.debug(f"Error checking for metadata changes: {e}")
            return False  # Assume no changes if we can't check
    
    def _get_recent_albums_for_server(self) -> List:
        """Get recently added albums using server-specific methods"""
        try:
            if self.server_type == "plex":
                return self._get_recent_albums_plex()
            elif self.server_type == "jellyfin":
                return self._get_recent_albums_jellyfin()
            elif self.server_type == "navidrome":
                return self._get_recent_albums_navidrome()
            else:
                logger.error(f"Unknown server type: {self.server_type}")
                return []
        except Exception as e:
            logger.error(f"Error getting recent albums for {self.server_type}: {e}")
            return []
    
    def _get_recent_albums_plex(self) -> List:
        """Get recently added and updated albums from Plex"""
        all_recent_content = []
        
        try:
            # Get recently added albums (up to 400 to catch more recent content)  
            try:
                recently_added = self.media_client.music_library.recentlyAdded(libtype='album', maxresults=400)
                all_recent_content.extend(recently_added)
                logger.info(f"Found {len(recently_added)} recently added albums")
            except:
                # Fallback to general recently added
                recently_added = self.media_client.music_library.recentlyAdded(maxresults=400)
                all_recent_content.extend(recently_added)
                logger.info(f"Found {len(recently_added)} recently added items (mixed types)")
            
            # Get recently updated albums (catches metadata corrections)
            try:
                recently_updated = self.media_client.music_library.search(sort='updatedAt:desc', libtype='album', limit=400)
                # Remove duplicates (items that are both recently added and updated)
                added_keys = {getattr(item, 'ratingKey', None) for item in all_recent_content}
                unique_updated = [item for item in recently_updated if getattr(item, 'ratingKey', None) not in added_keys]
                all_recent_content.extend(unique_updated)
                logger.info(f"Found {len(unique_updated)} additional recently updated albums (after deduplication)")
            except Exception as e:
                logger.warning(f"Could not get recently updated content: {e}")
            
            # Filter to only get Album objects and convert Artist objects to their albums
            recent_albums = []
            artist_count = 0
            album_count = 0
            
            for item in all_recent_content:
                try:
                    if hasattr(item, 'tracks') and hasattr(item, 'artist'):
                        # This is an Album - add directly
                        recent_albums.append(item)
                        album_count += 1
                    elif hasattr(item, 'albums'):
                        # This is an Artist - get their albums
                        try:
                            artist_albums = list(item.albums())
                            if artist_albums:
                                recent_albums.extend(artist_albums)
                                artist_count += 1
                        except Exception as albums_error:
                            logger.warning(f"Error getting albums from artist '{getattr(item, 'title', 'Unknown')}': {albums_error}")
                except Exception as e:
                    logger.warning(f"Error processing recently added item: {e}")
                    continue
            
            logger.info(f"Processed {artist_count} artists → albums, {album_count} direct albums")
            return recent_albums
            
        except Exception as e:
            logger.error(f"Error getting recent Plex albums: {e}")
            return []
    
    def _get_recent_albums_jellyfin(self) -> List:
        """Get recently added and updated albums from Jellyfin"""
        try:
            all_recent_albums = []
            
            # Get recently added albums
            recently_added = self.media_client.get_recently_added_albums(400)
            all_recent_albums.extend(recently_added)
            logger.info(f"Found {len(recently_added)} recently added albums")
            
            # Get recently updated albums
            recently_updated = self.media_client.get_recently_updated_albums(400)
            # Remove duplicates
            added_ids = {album.ratingKey for album in all_recent_albums}
            unique_updated = [album for album in recently_updated if album.ratingKey not in added_ids]
            all_recent_albums.extend(unique_updated)
            logger.info(f"Found {len(unique_updated)} additional recently updated albums (after deduplication)")
            
            return all_recent_albums
            
        except Exception as e:
            logger.error(f"Error getting recent Jellyfin albums: {e}")
            return []

    def _get_recent_albums_navidrome(self) -> List:
        """Get recently added albums from Navidrome using getAlbumList2 API"""
        try:
            logger.info("Getting recent albums from Navidrome...")
            recent_albums = self.media_client.get_recently_added_albums(400)
            logger.info(f"Found {len(recent_albums)} recently added albums from Navidrome")
            return recent_albums
        except Exception as e:
            logger.error(f"Error getting recent Navidrome albums: {e}")
            return []

    def _process_all_artists(self, artists: List):
        """Process all artists and their albums/tracks using thread pool"""
        total_artists = len(artists)
        logger.info(f"🎯 Processing {total_artists} artists with progress tracking")
        
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
                
                self._emit_signal('progress_updated',
                    f"Processing {artist_name}",
                    self.processed_artists,
                    total_artists,
                    progress_percent
                )
                logger.debug(f"🔄 Progress: {self.processed_artists}/{total_artists} ({progress_percent:.1f}%) - {artist_name}")
                
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
        
        # Process artists - use sequential processing in web server mode to avoid threading issues
        if not QT_AVAILABLE or self.force_sequential:
            # Sequential processing for web server mode
            for i, artist in enumerate(artists):
                if self.should_stop:
                    break

                result = process_single_artist(artist)
                if result is None:  # Task was cancelled
                    continue

                artist_name, success, details, album_count, track_count = result

                # Emit progress signal
                self._emit_signal('artist_processed', artist_name, success, details, album_count, track_count)
        else:
            # Process artists in parallel using ThreadPoolExecutor (Qt mode only)
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
                    self._emit_signal('artist_processed', artist_name, success, details, album_count, track_count)
    
    def _process_artist_with_content(self, media_artist) -> tuple[bool, str, int, int]:
        """Process an artist and all their albums and tracks with optimized API usage"""
        try:
            artist_name = getattr(media_artist, 'title', 'Unknown Artist')
            
            # 1. Insert/update the artist using server-agnostic method
            artist_success = self.database.insert_or_update_media_artist(media_artist, server_source=self.server_type)
            if not artist_success:
                return False, "Failed to update artist data", 0, 0
            
            artist_id = str(media_artist.ratingKey)
            
            # 2. Get all albums for this artist (cached from aggressive pre-population)
            try:
                albums = list(media_artist.albums())
            except Exception as e:
                logger.warning(f"Could not get albums for artist '{artist_name}': {e}")
                return True, "Artist updated (no albums accessible)", 0, 0
            
            album_count = 0
            track_count = 0
            
            # 3. Process albums in smaller batches to reduce memory usage
            batch_size = 10  # Process 10 albums at a time
            for i in range(0, len(albums), batch_size):
                if self.should_stop:
                    break
                    
                album_batch = albums[i:i + batch_size]
                
                for album in album_batch:
                    if self.should_stop:
                        break
                    
                    try:
                        # Insert/update album using server-agnostic method
                        album_success = self.database.insert_or_update_media_album(album, artist_id, server_source=self.server_type)
                        if album_success:
                            album_count += 1
                            album_id = str(album.ratingKey)
                            
                            # 4. Process tracks in this album (cached from aggressive pre-population)
                            try:
                                tracks = list(album.tracks())
                                
                                # Batch insert tracks for better database performance
                                track_batch = []
                                for track in tracks:
                                    if self.should_stop:
                                        break
                                    track_batch.append((track, album_id, artist_id))
                                
                                # Process track batch
                                for track, alb_id, art_id in track_batch:
                                    try:
                                        track_success = self.database.insert_or_update_media_track(track, alb_id, art_id, server_source=self.server_type)
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
            logger.error(f"Error processing artist '{getattr(media_artist, 'title', 'Unknown')}': {e}")
            return False, f"Processing error: {str(e)}", 0, 0

    def run_with_callback(self, completion_callback=None):
        """
        Run the database update with an optional completion callback.
        This is used by the web interface for automatic chaining of operations.
        """
        try:
            # Run the normal update process
            self.run()

            # Call completion callback if provided
            if completion_callback:
                try:
                    completion_callback()
                except Exception as e:
                    logger.error(f"Error in database update completion callback: {e}")

        except Exception as e:
            logger.error(f"Error in run_with_callback: {e}")

class DatabaseStatsWorker(QThread):
    """Simple worker for getting database statistics without blocking UI"""
    
    # Qt signals (only available when PyQt6 is installed)
    if QT_AVAILABLE:
        stats_updated = pyqtSignal(dict)  # Database statistics
    
    def __init__(self, database_path: str = "database/music_library.db"):
        super().__init__()
        self.database_path = database_path
        self.should_stop = False
        
        # Initialize signal callbacks for headless mode
        if not QT_AVAILABLE:
            self.callbacks = {
                'stats_updated': []
            }
    
    def stop(self):
        """Stop the worker"""
        self.should_stop = True
    
    def _emit_signal(self, signal_name: str, *args):
        """Emit a signal in both Qt and headless modes"""
        if QT_AVAILABLE and hasattr(self, signal_name):
            # Qt mode - use actual signal
            getattr(self, signal_name).emit(*args)
        elif not QT_AVAILABLE:
            # Headless mode - use callback system
            self.emit_signal(signal_name, *args)
    
    def connect_callback(self, signal_name: str, callback: Callable):
        """Connect a callback for headless mode"""
        if not QT_AVAILABLE:
            self.connect_signal(signal_name, callback)
        # In Qt mode, use the normal signal.connect() method
    
    def run(self):
        """Get database statistics and full info including last refresh"""
        try:
            if self.should_stop:
                return
                
            database = get_database(self.database_path)
            if self.should_stop:
                return
                
            # Get database info for active server (server-aware statistics)
            info = database.get_database_info_for_server()
            if not self.should_stop:
                self._emit_signal('stats_updated', info)
        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            if not self.should_stop:
                # Import here to avoid circular imports
                from config.settings import config_manager
                active_server = config_manager.get_active_media_server()
                
                self._emit_signal('stats_updated', {
                    'artists': 0,
                    'albums': 0, 
                    'tracks': 0,
                    'database_size_mb': 0.0,
                    'last_update': None,
                    'last_full_refresh': None,
                    'server_source': active_server
                })