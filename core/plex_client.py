from plexapi.server import PlexServer
from plexapi.library import LibrarySection, MusicSection
from plexapi.audio import Track as PlexTrack, Album as PlexAlbum, Artist as PlexArtist
from plexapi.playlist import Playlist as PlexPlaylist
from plexapi.exceptions import PlexApiException, NotFound
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import requests
from utils.logging_config import get_logger
from config.settings import config_manager

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
    
    def ensure_connection(self) -> bool:
        """Ensure connection to Plex server with lazy initialization."""
        if self._connection_attempted:
            return self.server is not None
        
        if self._is_connecting:
            return False
        
        self._is_connecting = True
        try:
            self._setup_client()
            return self.server is not None
        finally:
            self._is_connecting = False
            self._connection_attempted = True
    
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
            logger.info(f"Successfully connected to Plex server: {self.server.friendlyName}")
            
        except Exception as e:
            logger.error(f"Failed to connect to Plex server: {e}")
            self.server = None
    
    def _find_music_library(self):
        if not self.server:
            return
        
        try:
            for section in self.server.library.sections():
                if section.type == 'artist':
                    self.music_library = section
                    logger.info(f"Found music library: {section.title}")
                    break
            
            if not self.music_library:
                logger.warning("No music library found on Plex server")
                
        except Exception as e:
            logger.error(f"Error finding music library: {e}")
    
    def is_connected(self) -> bool:
        """Check if connected to Plex server with lazy initialization."""
        if not self._connection_attempted:
            # Try to connect on first call, but don't block if already connecting
            if not self._is_connecting:
                self.ensure_connection()
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
    
    def update_playlist(self, playlist_name: str, tracks: List[PlexTrackInfo]) -> bool:
        if not self.ensure_connection():
            return False
        
        try:
            existing_playlist = self.server.playlist(playlist_name)
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

            # --- Stage 3: Title-Only Fallback Search ---
            logger.debug(f"Stage 3: Performing title-only search for '{title}'")
            stage3_results = self.music_library.searchTracks(title=title, limit=limit)
            add_candidates(stage3_results)
            
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
            
            logger.info(f"Updated genres for {artist.title}: {genres}")
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
