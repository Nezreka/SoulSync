from plexapi.server import PlexServer
from plexapi.library import LibrarySection, MusicSection
from plexapi.audio import Track as PlexTrack, Album as PlexAlbum, Artist as PlexArtist
from plexapi.playlist import Playlist as PlexPlaylist
from plexapi.exceptions import PlexApiException, NotFound
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import requests
from datetime import datetime, timedelta
import re
from utils.logging_config import get_logger
from config.settings import config_manager
import threading

logger = get_logger("plex_client")

@dataclass
class PlexTrackInfo:
    id: str
    title: str
    artist: str
    album: str
    duration: int
    track_number: Optional[int] = None
    year: Optional[int] = None
    rating: Optional[float] = None
    
    @classmethod
    def from_plex_track(cls, track: PlexTrack) -> 'PlexTrackInfo':
        # Gracefully handle tracks that might be missing artist or album metadata in Plex
        try:
            artist_title = track.artist().title if track.artist() else "Unknown Artist"
        except (NotFound, AttributeError):
            artist_title = "Unknown Artist"
            
        try:
            album_title = track.album().title if track.album() else "Unknown Album"
        except (NotFound, AttributeError):
            album_title = "Unknown Album"

        return cls(
            id=str(track.ratingKey),
            title=track.title,
            artist=artist_title,
            album=album_title,
            duration=track.duration,
            track_number=track.trackNumber,
            year=track.year,
            rating=track.userRating
        )

@dataclass
class PlexPlaylistInfo:
    id: str
    title: str
    description: Optional[str]
    duration: int
    leaf_count: int
    tracks: List[PlexTrackInfo]
    
    @classmethod
    def from_plex_playlist(cls, playlist: PlexPlaylist) -> 'PlexPlaylistInfo':
        tracks = []
        for item in playlist.items():
            if isinstance(item, PlexTrack):
                tracks.append(PlexTrackInfo.from_plex_track(item))
        
        return cls(
            id=str(playlist.ratingKey),
            title=playlist.title,
            description=playlist.summary,
            duration=playlist.duration,
            leaf_count=playlist.leafCount,
            tracks=tracks
        )

class PlexClient:
    def __init__(self):
        self.server: Optional[PlexServer] = None
        self.music_library: Optional[MusicSection] = None
        self._connection_attempted = False
        self._is_connecting = False
        self._last_connection_check = 0  # Cache connection checks
        self._connection_check_interval = 30  # Check every 30 seconds max
    
    def ensure_connection(self) -> bool:
        """Ensure connection to Plex server with lazy initialization."""
        # If we've successfully connected before and server object exists, return immediately
        if self._connection_attempted and self.server is not None:
            return True

        # Prevent concurrent connection attempts
        if self._is_connecting:
            return False

        self._is_connecting = True
        try:
            self._setup_client()
            connection_successful = self.server is not None

            # Only mark as attempted if connection succeeded
            # This allows retries if connection fails
            if connection_successful:
                self._connection_attempted = True
            else:
                # Reset flag to allow retry on next call
                self._connection_attempted = False

            return connection_successful
        finally:
            self._is_connecting = False

    def reset_connection(self):
        """Reset connection state to force reconnection on next ensure_connection() call.
        Useful when config changes or connection needs to be refreshed."""
        logger.info("Resetting Plex connection state")
        self.server = None
        self.music_library = None
        self._connection_attempted = False
        self._last_connection_check = 0

    def _setup_client(self):
        config = config_manager.get_plex_config()
        
        if not config.get('base_url'):
            logger.warning("Plex server URL not configured")
            return
        
        try:
            if config.get('token'):
                # Use a longer timeout (15 seconds) to prevent read timeouts on slow servers
                self.server = PlexServer(config['base_url'], config['token'], timeout=15)
            else:
                logger.error("Plex token not configured")
                return
            
            self._find_music_library()
            logger.debug(f"Successfully connected to Plex server: {self.server.friendlyName}")
            
        except Exception as e:
            logger.error(f"Failed to connect to Plex server: {e}")
            self.server = None
    
    def get_available_music_libraries(self) -> List[Dict[str, str]]:
        """Get list of all available music libraries on the Plex server"""
        if not self.ensure_connection() or not self.server:
            return []

        try:
            music_libraries = []
            for section in self.server.library.sections():
                if section.type == 'artist':
                    music_libraries.append({
                        'title': section.title,
                        'key': str(section.key)
                    })

            logger.debug(f"Found {len(music_libraries)} music libraries")
            return music_libraries
        except Exception as e:
            logger.error(f"Error getting music libraries: {e}")
            return []

    def set_music_library_by_name(self, library_name: str) -> bool:
        """Set the active music library by name"""
        if not self.server:
            return False

        try:
            for section in self.server.library.sections():
                if section.type == 'artist' and section.title == library_name:
                    self.music_library = section
                    logger.info(f"Set music library to: {library_name}")

                    # Store preference in database
                    from database.music_database import MusicDatabase
                    db = MusicDatabase()
                    db.set_preference('plex_music_library', library_name)

                    return True

            logger.warning(f"Music library '{library_name}' not found")
            return False
        except Exception as e:
            logger.error(f"Error setting music library: {e}")
            return False

    def _find_music_library(self):
        if not self.server:
            return

        try:
            music_sections = []

            # Collect all music libraries
            for section in self.server.library.sections():
                if section.type == 'artist':
                    music_sections.append(section)

            if not music_sections:
                logger.warning("No music library found on Plex server")
                return

            # Check if user has a saved preference
            try:
                from database.music_database import MusicDatabase
                db = MusicDatabase()
                preferred_library = db.get_preference('plex_music_library')

                if preferred_library:
                    # Try to find the preferred library
                    for section in music_sections:
                        if section.title == preferred_library:
                            self.music_library = section
                            logger.debug(f"Using user-selected music library: {section.title}")
                            return
            except Exception as e:
                logger.debug(f"Could not check library preference: {e}")

            # Priority order for common library names
            priority_names = ['Music', 'music', 'Audio', 'audio', 'Songs', 'songs']

            # First, try to find a library with a priority name
            for priority_name in priority_names:
                for section in music_sections:
                    if section.title == priority_name:
                        self.music_library = section
                        logger.debug(f"Found preferred music library: {section.title}")
                        return

            # If no priority match found, use the first one
            self.music_library = music_sections[0]
            logger.debug(f"Found music library (first available): {self.music_library.title}")

            # Log other available libraries if multiple exist
            if len(music_sections) > 1:
                other_libraries = [s.title for s in music_sections[1:]]
                logger.info(f"Other music libraries available: {', '.join(other_libraries)}")

        except Exception as e:
            logger.error(f"Error finding music library: {e}")
    
    def is_connected(self) -> bool:
        """Check if connected to Plex server with cached connection checks."""
        import time

        current_time = time.time()

        # Always attempt connection if not connected OR cache expired
        # This ensures we retry failed connections and detect disconnections
        should_check = (
            self.server is None or  # Not connected - always try
            current_time - self._last_connection_check > self._connection_check_interval  # Cache expired
        )

        if should_check:
            self._last_connection_check = current_time

            # Try to connect, but don't block if already connecting
            if not self._is_connecting:
                self.ensure_connection()

        # For status checks, only verify server connection, not music library
        # Music library might be None if user hasn't selected one yet
        return self.server is not None

    def is_fully_configured(self) -> bool:
        """Check if both server is connected AND music library is selected."""
        return self.server is not None and self.music_library is not None
    
    def get_all_playlists(self) -> List[PlexPlaylistInfo]:
        if not self.ensure_connection():
            logger.error("Not connected to Plex server")
            return []
        
        playlists = []
        
        try:
            for playlist in self.server.playlists():
                if playlist.playlistType == 'audio':
                    playlist_info = PlexPlaylistInfo.from_plex_playlist(playlist)
                    playlists.append(playlist_info)
            
            logger.info(f"Retrieved {len(playlists)} audio playlists")
            return playlists
            
        except Exception as e:
            logger.error(f"Error fetching playlists: {e}")
            return []
    
    def get_playlist_by_name(self, name: str) -> Optional[PlexPlaylistInfo]:
        if not self.ensure_connection():
            return None
        
        try:
            playlist = self.server.playlist(name)
            if playlist.playlistType == 'audio':
                return PlexPlaylistInfo.from_plex_playlist(playlist)
            return None
            
        except NotFound:
            logger.info(f"Playlist '{name}' not found")
            return None
        except Exception as e:
            logger.error(f"Error fetching playlist '{name}': {e}")
            return None
    
    def create_playlist(self, name: str, tracks) -> bool:
        if not self.ensure_connection():
            logger.error("Not connected to Plex server")
            return False
        
        try:
            # Handle both PlexTrackInfo objects and actual Plex track objects
            plex_tracks = []
            for track in tracks:
                if hasattr(track, 'ratingKey'):
                    # This is already a Plex track object
                    plex_tracks.append(track)
                elif hasattr(track, '_original_plex_track'):
                    # This is a PlexTrackInfo object with stored original track reference
                    original_track = track._original_plex_track
                    if original_track is not None:
                        plex_tracks.append(original_track)
                        logger.debug(f"Using stored track reference for: {track.title} by {track.artist} (ratingKey: {original_track.ratingKey})")
                    else:
                        logger.warning(f"Stored track reference is None for: {track.title} by {track.artist}")
                elif hasattr(track, 'title'):
                    # Fallback: This is a PlexTrackInfo object, need to find the actual track
                    plex_track = self._find_track(track.title, track.artist, track.album)
                    if plex_track:
                        plex_tracks.append(plex_track)
                    else:
                        logger.warning(f"Track not found in Plex: {track.title} by {track.artist}")
            
            logger.info(f"Processed {len(tracks)} input tracks, resulting in {len(plex_tracks)} valid Plex tracks for playlist '{name}'")
            
            if plex_tracks:
                # Additional validation
                valid_tracks = [t for t in plex_tracks if t is not None and hasattr(t, 'ratingKey')]
                logger.info(f"Final validation: {len(valid_tracks)} valid tracks with ratingKeys")
                
                if valid_tracks:
                    # Debug the track objects before creating playlist
                    logger.debug(f"About to create playlist with tracks:")
                    for i, track in enumerate(valid_tracks):
                        logger.debug(f"  Track {i+1}: {track.title} (type: {type(track)}, ratingKey: {track.ratingKey})")
                    
                    try:
                        playlist = self.server.createPlaylist(name, valid_tracks)
                        logger.info(f"Created playlist '{name}' with {len(valid_tracks)} tracks")
                        return True
                    except Exception as create_error:
                        logger.error(f"CreatePlaylist failed: {create_error}")
                        # Try alternative approach - pass items as list
                        try:
                            playlist = self.server.createPlaylist(name, items=valid_tracks)
                            logger.info(f"Created playlist '{name}' with {len(valid_tracks)} tracks (using items parameter)")
                            return True
                        except Exception as alt_error:
                            logger.error(f"Alternative createPlaylist also failed: {alt_error}")
                            # Try creating empty playlist first, then adding tracks
                            try:
                                logger.debug("Trying to create empty playlist first, then add tracks...")
                                playlist = self.server.createPlaylist(name, [])
                                playlist.addItems(valid_tracks)
                                logger.info(f"Created empty playlist and added {len(valid_tracks)} tracks")
                                return True
                            except Exception as empty_error:
                                logger.error(f"Empty playlist approach also failed: {empty_error}")
                                # Final attempt: Create with first item, then add the rest
                                try:
                                    logger.debug("Trying to create playlist with first track, then add remaining...")
                                    playlist = self.server.createPlaylist(name, valid_tracks[0])
                                    if len(valid_tracks) > 1:
                                        playlist.addItems(valid_tracks[1:])
                                    logger.info(f"Created playlist with first track and added {len(valid_tracks)-1} more tracks")
                                    return True
                                except Exception as final_error:
                                    logger.error(f"Final playlist creation attempt failed: {final_error}")
                                    raise create_error
                else:
                    logger.error(f"No valid tracks with ratingKeys for playlist '{name}'")
                    return False
            else:
                logger.error(f"No tracks found for playlist '{name}'")
                return False
                
        except Exception as e:
            logger.error(f"Error creating playlist '{name}': {e}")
            return False
    
    def copy_playlist(self, source_name: str, target_name: str) -> bool:
        """Copy a playlist to create a backup"""
        if not self.ensure_connection():
            return False
        
        try:
            # Get the source playlist
            source_playlist = self.server.playlist(source_name)
            
            # Get all tracks from source playlist
            source_tracks = source_playlist.items()
            logger.debug(f"Retrieved {len(source_tracks) if source_tracks else 0} tracks from source playlist")
            
            # Validate tracks
            if not source_tracks:
                logger.warning(f"Source playlist '{source_name}' has no tracks to copy")
                return False
                
            # Filter for valid track objects
            valid_tracks = [track for track in source_tracks if hasattr(track, 'ratingKey')]
            logger.debug(f"Found {len(valid_tracks)} valid tracks with ratingKeys")
            
            if not valid_tracks:
                logger.error(f"No valid tracks found in source playlist '{source_name}'")
                return False
            
            # Delete target playlist if it exists (for overwriting backup)
            try:
                target_playlist = self.server.playlist(target_name)
                target_playlist.delete()
                logger.info(f"Deleted existing backup playlist '{target_name}'")
            except NotFound:
                pass  # Target doesn't exist, which is fine
            
            # Create new playlist with copied tracks
            try:
                self.server.createPlaylist(target_name, items=valid_tracks)
                logger.info(f"✅ Created backup playlist '{target_name}' with {len(valid_tracks)} tracks")
                return True
            except Exception as create_error:
                logger.error(f"Failed to create backup playlist: {create_error}")
                # Try alternative method
                try:
                    new_playlist = self.server.createPlaylist(target_name)
                    new_playlist.addItems(valid_tracks)
                    logger.info(f"✅ Created backup playlist '{target_name}' with {len(valid_tracks)} tracks (alternative method)")
                    return True
                except Exception as alt_error:
                    logger.error(f"Alternative backup creation also failed: {alt_error}")
                    return False
                
        except NotFound:
            logger.error(f"Source playlist '{source_name}' not found")
            return False
        except Exception as e:
            logger.error(f"Error copying playlist '{source_name}' to '{target_name}': {e}")
            return False

    def update_playlist(self, playlist_name: str, tracks: List[PlexTrackInfo]) -> bool:
        if not self.ensure_connection():
            return False
        
        try:
            existing_playlist = self.server.playlist(playlist_name)
            
            # Check if backup is enabled in config
            from config.settings import config_manager
            create_backup = config_manager.get('playlist_sync.create_backup', True)
            
            if create_backup:
                backup_name = f"{playlist_name} Backup"
                logger.info(f"🛡️ Creating backup playlist '{backup_name}' before sync")
                
                if self.copy_playlist(playlist_name, backup_name):
                    logger.info(f"✅ Backup created successfully")
                else:
                    logger.warning(f"⚠️ Failed to create backup, continuing with sync")
            
            # Delete original and recreate
            existing_playlist.delete()
            return self.create_playlist(playlist_name, tracks)
            
        except NotFound:
            logger.info(f"Playlist '{playlist_name}' not found, creating new one")
            return self.create_playlist(playlist_name, tracks)
        except Exception as e:
            logger.error(f"Error updating playlist '{playlist_name}': {e}")
            return False
    
    def _find_track(self, title: str, artist: str, album: str) -> Optional[PlexTrack]:
        if not self.music_library:
            return None
        
        try:
            search_results = self.music_library.search(title=title, artist=artist, album=album)
            
            for result in search_results:
                if isinstance(result, PlexTrack):
                    if (result.title.lower() == title.lower() and 
                        result.artist().title.lower() == artist.lower() and
                        result.album().title.lower() == album.lower()):
                        return result
            
            broader_search = self.music_library.search(title=title, artist=artist)
            for result in broader_search:
                if isinstance(result, PlexTrack):
                    if (result.title.lower() == title.lower() and 
                        result.artist().title.lower() == artist.lower()):
                        return result
            
            return None
            
        except Exception as e:
            logger.error(f"Error searching for track '{title}' by '{artist}': {e}")
            return None
    
    def search_tracks(self, title: str, artist: str, limit: int = 15) -> List[PlexTrackInfo]:
        """
        Searches for tracks using an efficient, multi-stage "early exit" strategy.
        It stops and returns results as soon as candidates are found.
        """
        if not self.music_library:
            logger.warning("Plex music library not found. Cannot perform search.")
            return []

        try:
            candidate_tracks = []
            found_track_keys = set()

            def add_candidates(tracks):
                """Helper function to add unique tracks to the main candidate list."""
                for track in tracks:
                    if track.ratingKey not in found_track_keys:
                        candidate_tracks.append(track)
                        found_track_keys.add(track.ratingKey)

            # --- Stage 1: High-Precision Search (Artist -> then filter by Title) ---
            if artist:
                logger.debug(f"Stage 1: Searching for artist '{artist}'")
                artist_results = self.music_library.searchArtists(title=artist, limit=1)
                if artist_results:
                    plex_artist = artist_results[0]
                    all_artist_tracks = plex_artist.tracks()
                    lower_title = title.lower()
                    stage1_results = [track for track in all_artist_tracks if lower_title in track.title.lower()]
                    add_candidates(stage1_results)
                    logger.debug(f"Stage 1 found {len(stage1_results)} candidates.")
            
            # --- Early Exit: If Stage 1 found results, stop here ---
            if candidate_tracks:
                logger.info(f"Found {len(candidate_tracks)} candidates in Stage 1. Exiting early.")
                tracks = [PlexTrackInfo.from_plex_track(track) for track in candidate_tracks[:limit]]
                # Store references to original tracks for playlist creation
                for i, track_info in enumerate(tracks):
                    if i < len(candidate_tracks):
                        track_info._original_plex_track = candidate_tracks[i]
                        logger.debug(f"Stored original track reference for '{track_info.title}' (ratingKey: {candidate_tracks[i].ratingKey})")
                    else:
                        logger.warning(f"Index mismatch: cannot store original track for '{track_info.title}'")
                return tracks

            # --- Stage 2: Flexible Keyword Search (Artist + Title combined) ---
            search_query = f"{artist} {title}".strip()
            logger.debug(f"Stage 2: Performing keyword search for '{search_query}'")
            stage2_results = self.music_library.search(title=search_query, libtype='track', limit=limit)
            add_candidates(stage2_results)

            # --- Early Exit: If Stage 2 found results, stop here ---
            if candidate_tracks:
                logger.info(f"Found {len(candidate_tracks)} candidates in Stage 2. Exiting early.")
                tracks = [PlexTrackInfo.from_plex_track(track) for track in candidate_tracks[:limit]]
                # Store references to original tracks for playlist creation
                for i, track_info in enumerate(tracks):
                    if i < len(candidate_tracks):
                        track_info._original_plex_track = candidate_tracks[i]
                        logger.debug(f"Stored original track reference for '{track_info.title}' (ratingKey: {candidate_tracks[i].ratingKey})")
                    else:
                        logger.warning(f"Index mismatch: cannot store original track for '{track_info.title}'")
                return tracks

            # --- Stage 3: Title-Only Fallback REMOVED ---
            # Removed to prevent false positives where tracks with same title 
            # but different artists are incorrectly matched
            
            tracks = [PlexTrackInfo.from_plex_track(track) for track in candidate_tracks[:limit]]
    
            # Store references to original tracks for playlist creation
            for i, track_info in enumerate(tracks):
                if i < len(candidate_tracks):
                    track_info._original_plex_track = candidate_tracks[i]
                    logger.debug(f"Stored original track reference for '{track_info.title}' (ratingKey: {candidate_tracks[i].ratingKey})")
                else:
                    logger.warning(f"Index mismatch: cannot store original track for '{track_info.title}'")
    
            if tracks:
                logger.info(f"Found {len(tracks)} total potential matches for '{title}' by '{artist}' after all stages.")
            
            return tracks
            
        except Exception as e:
            logger.error(f"Error during multi-stage search for title='{title}', artist='{artist}': {e}")
            import traceback
            traceback.print_exc()
            return []




    def get_library_stats(self) -> Dict[str, int]:
        if not self.music_library:
            return {}
        
        try:
            return {
                'artists': len(self.music_library.searchArtists()),
                'albums': len(self.music_library.searchAlbums()),
                'tracks': len(self.music_library.searchTracks())
            }
        except Exception as e:
            logger.error(f"Error getting library stats: {e}")
            return {}
    
    def get_all_artists(self) -> List[PlexArtist]:
        """Get all artists from the music library"""
        if not self.ensure_connection() or not self.music_library:
            logger.error("Not connected to Plex server or no music library")
            return []
        
        try:
            artists = self.music_library.searchArtists()
            logger.info(f"Found {len(artists)} artists in Plex library")
            return artists
        except Exception as e:
            logger.error(f"Error getting all artists: {e}")
            return []
    
    def update_artist_genres(self, artist: PlexArtist, genres: List[str]):
        """Update artist genres"""
        try:
            # Clear existing genres first
            for genre in artist.genres:
                artist.removeGenre(genre)
            
            # Add new genres
            for genre in genres:
                artist.addGenre(genre)
            
            # Use safe logging to avoid Unicode encoding errors
            try:
                logger.info(f"Updated genres for {artist.title}: {len(genres)} genres")
            except UnicodeEncodeError:
                logger.info(f"Updated genres for artist (ID: {artist.ratingKey}): {len(genres)} genres")
            return True
        except Exception as e:
            logger.error(f"Error updating genres for {artist.title}: {e}")
            return False
    
    def update_artist_poster(self, artist: PlexArtist, image_data: bytes):
        """Update artist poster image"""
        try:
            # Upload poster using Plex API
            upload_url = f"{self.server._baseurl}/library/metadata/{artist.ratingKey}/posters"
            headers = {
                'X-Plex-Token': self.server._token,
                'Content-Type': 'image/jpeg'
            }
            
            response = requests.post(upload_url, data=image_data, headers=headers)
            response.raise_for_status()
            
            # Refresh artist to see changes
            artist.refresh()
            logger.info(f"Updated poster for {artist.title}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating poster for {artist.title}: {e}")
            return False
    
    def update_album_poster(self, album, image_data: bytes):
        """Update album poster image"""
        try:
            # Upload poster using Plex API
            upload_url = f"{self.server._baseurl}/library/metadata/{album.ratingKey}/posters"
            headers = {
                'X-Plex-Token': self.server._token,
                'Content-Type': 'image/jpeg'
            }
            
            response = requests.post(upload_url, data=image_data, headers=headers)
            response.raise_for_status()
            
            # Refresh album to see changes
            album.refresh()
            logger.info(f"Updated poster for album '{album.title}' by '{album.parentTitle}'")
            return True
            
        except Exception as e:
            logger.error(f"Error updating poster for album '{album.title}': {e}")
            return False
    
    def parse_update_timestamp(self, artist: PlexArtist) -> Optional[datetime]:
        """Parse the last update timestamp from artist summary"""
        try:
            # Get artist summary which stores our timestamp
            summary = getattr(artist, 'summary', '') or ''
            
            # Look for timestamp pattern: -updatedAtYYYY-MM-DD
            pattern = r'-updatedAt(\d{4}-\d{2}-\d{2})'
            match = re.search(pattern, summary)
            
            if match:
                date_str = match.group(1)
                parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
                return parsed_date
            
            return None
            
        except Exception as e:
            logger.debug(f"Error parsing timestamp for {artist.title}: {e}")
            return None
    
    def is_artist_ignored(self, artist: PlexArtist) -> bool:
        """Check if artist is manually marked to be ignored"""
        try:
            # Check summary field where we store timestamps and ignore flags
            summary = getattr(artist, 'summary', '') or ''
            return '-IgnoreUpdate' in summary
        except Exception as e:
            logger.debug(f"Error checking ignore status for {artist.title}: {e}")
            return False
    
    def needs_update_by_age(self, artist: PlexArtist, refresh_interval_days: int) -> bool:
        """Check if artist needs updating based on age threshold"""
        try:
            # First check if artist is manually ignored
            if self.is_artist_ignored(artist):
                logger.debug(f"Artist {artist.title} is manually ignored")
                return False
            
            # If refresh_interval_days is 0, always update (full refresh)
            if refresh_interval_days == 0:
                return True
            
            last_update = self.parse_update_timestamp(artist)
            
            # If no timestamp found, needs update
            if last_update is None:
                return True
            
            # Check if last update is older than threshold
            threshold_date = datetime.now() - timedelta(days=refresh_interval_days)
            return last_update < threshold_date
            
        except Exception as e:
            logger.debug(f"Error checking update age for {artist.title}: {e}")
            return True  # Default to needing update if error
    
    def update_artist_biography(self, artist: PlexArtist) -> bool:
        """Update artist summary with current timestamp"""
        try:
            # Get current summary/biography
            current_summary = getattr(artist, 'summary', '') or ''
            
            # Preserve any IgnoreUpdate flag
            ignore_flag = ''
            if '-IgnoreUpdate' in current_summary:
                ignore_flag = '-IgnoreUpdate'
                # Remove IgnoreUpdate flag temporarily for processing
                current_summary = current_summary.replace('-IgnoreUpdate', '').strip()
            
            # Remove existing timestamp if present (ensures only one timestamp)
            pattern = r'\s*-updatedAt\d{4}-\d{2}-\d{2}\s*'
            clean_summary = re.sub(pattern, '', current_summary).strip()
            
            # Build new summary with timestamp
            today = datetime.now().strftime('%Y-%m-%d')
            
            # Add timestamp to summary field
            new_summary = clean_summary
            if ignore_flag:
                new_summary = f"{new_summary}\n\n{ignore_flag}".strip()
            new_summary = f"{new_summary}\n\n-updatedAt{today}".strip()
            
            # Use the correct Plex API syntax with .value
            artist.edit(**{
                'summary.value': new_summary
            })
            
            # Add a small delay to let the edit process
            import time
            time.sleep(0.5)
            
            # Reload to see the changes
            artist.reload()
            
            # Check if edit worked
            updated_summary = getattr(artist, 'summary', '') or ''
            
            if updated_summary and '-updatedAt' in updated_summary:
                logger.info(f"Updated summary timestamp for {artist.title}")
                return True
            else:
                return False
            
        except Exception as e:
            logger.error(f"Error updating summary for {artist.title}: {e}")
            return False
    
    def update_track_metadata(self, track_id: str, metadata: Dict[str, Any]) -> bool:
        if not self.ensure_connection():
            return False
        
        try:
            track = self.server.fetchItem(int(track_id))
            if isinstance(track, PlexTrack):
                edits = {}
                if 'title' in metadata:
                    edits['title'] = metadata['title']
                if 'artist' in metadata:
                    edits['artist'] = metadata['artist']
                if 'album' in metadata:
                    edits['album'] = metadata['album']
                if 'year' in metadata:
                    edits['year'] = metadata['year']
                
                if edits:
                    track.edit(**edits)
                    logger.info(f"Updated metadata for track: {track.title}")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error updating track metadata: {e}")
            return False
    
    def trigger_library_scan(self, library_name: str = "Music") -> bool:
        """Trigger Plex library scan for the specified library"""
        if not self.ensure_connection():
            return False
            
        try:
            library = self.server.library.section(library_name)
            library.update()  # Non-blocking scan request
            logger.info(f"🎵 Triggered Plex library scan for '{library_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to trigger library scan for '{library_name}': {e}")
            return False
    
    def is_library_scanning(self, library_name: str = "Music") -> bool:
        """Check if Plex library is currently scanning"""
        if not self.ensure_connection():
            logger.debug(f"🔍 DEBUG: Not connected to Plex, cannot check scan status")
            return False
            
        try:
            library = self.server.library.section(library_name)
            
            # Check if library has a scanning attribute or is refreshing
            # The Plex API exposes this through the library's refreshing property
            refreshing = hasattr(library, 'refreshing') and library.refreshing
            logger.debug(f"🔍 DEBUG: Library.refreshing = {refreshing}")
            
            if refreshing:
                logger.debug(f"🔍 DEBUG: Library is refreshing")
                return True
            
            # Alternative method: Check server activities for scanning
            try:
                activities = self.server.activities()
                logger.debug(f"🔍 DEBUG: Found {len(activities)} server activities")
                
                for activity in activities:
                    # Look for library scan activities
                    activity_type = getattr(activity, 'type', 'unknown')
                    activity_title = getattr(activity, 'title', 'unknown')
                    logger.debug(f"🔍 DEBUG: Activity - type: {activity_type}, title: {activity_title}")
                    
                    if (activity_type in ['library.scan', 'library.refresh'] and
                        library_name.lower() in activity_title.lower()):
                        logger.debug(f"🔍 DEBUG: Found matching scan activity: {activity_title}")
                        return True
            except Exception as activities_error:
                logger.debug(f"Could not check server activities: {activities_error}")
            
            logger.debug(f"🔍 DEBUG: No scan activity detected")
            return False
            
        except Exception as e:
            logger.debug(f"Error checking if library is scanning: {e}")
            return False
    
    def search_albums(self, album_name: str = "", artist_name: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        """Search for albums in Plex library"""
        if not self.ensure_connection() or not self.music_library:
            return []
        
        try:
            albums = []
            
            # Perform search - different approaches based on what we're searching for
            search_results = []
            
            if album_name and artist_name:
                # Search for albums by specific artist and title
                try:
                    # First try searching for the artist, then filter their albums
                    artist_results = self.music_library.searchArtists(title=artist_name, limit=3)
                    for artist in artist_results:
                        try:
                            artist_albums = artist.albums()
                            for album in artist_albums:
                                if album_name.lower() in album.title.lower():
                                    search_results.append(album)
                        except Exception as e:
                            logger.debug(f"Error getting albums for artist {artist.title}: {e}")
                except Exception as e:
                    logger.debug(f"Artist search failed, trying general search: {e}")
                    # Fallback to general album search
                    try:
                        search_results = self.music_library.search(title=album_name)
                        # Filter to only albums
                        search_results = [r for r in search_results if isinstance(r, PlexAlbum)]
                    except Exception as e2:
                        logger.debug(f"General search also failed: {e2}")
                        
            elif album_name:
                # Search for albums by title only
                try:
                    search_results = self.music_library.search(title=album_name)
                    # Filter to only albums  
                    search_results = [r for r in search_results if isinstance(r, PlexAlbum)]
                except Exception as e:
                    logger.debug(f"Album title search failed: {e}")
                    
            elif artist_name:
                # Search for all albums by artist
                try:
                    artist_results = self.music_library.searchArtists(title=artist_name, limit=1)
                    if artist_results:
                        search_results = artist_results[0].albums()
                except Exception as e:
                    logger.debug(f"Artist album search failed: {e}")
            else:
                # Get all albums if no search terms
                try:
                    search_results = self.music_library.albums()
                except Exception as e:
                    logger.debug(f"Get all albums failed: {e}")
            
            # Process results and convert to standardized format
            if search_results:
                for result in search_results:
                    if isinstance(result, PlexAlbum):
                        try:
                            # Get album info
                            album_info = {
                                'id': str(result.ratingKey),
                                'title': result.title,
                                'artist': result.artist().title if result.artist() else "Unknown Artist",
                                'year': result.year,
                                'track_count': len(result.tracks()) if hasattr(result, 'tracks') else 0,
                                'plex_album': result  # Keep reference to original object
                            }
                            albums.append(album_info)
                            
                            if len(albums) >= limit:
                                break
                                
                        except Exception as e:
                            logger.debug(f"Error processing album {result.title}: {e}")
                            continue
            
            logger.debug(f"Found {len(albums)} albums matching query: album='{album_name}', artist='{artist_name}'")
            return albums
            
        except Exception as e:
            logger.error(f"Error searching albums: {e}")
            return []
    
    def get_album_by_name_and_artist(self, album_name: str, artist_name: str) -> Optional[Dict[str, Any]]:
        """Get a specific album by name and artist"""
        albums = self.search_albums(album_name, artist_name, limit=5)
        
        # Look for exact matches first
        for album in albums:
            if (album['title'].lower() == album_name.lower() and 
                album['artist'].lower() == artist_name.lower()):
                return album
        
        # Return first result if no exact match
        return albums[0] if albums else None
