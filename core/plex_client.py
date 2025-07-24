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
    
    def create_playlist(self, name: str, tracks: List[PlexTrackInfo]) -> bool:
        if not self.ensure_connection():
            logger.error("Not connected to Plex server")
            return False
        
        try:
            plex_tracks = []
            for track_info in tracks:
                plex_track = self._find_track(track_info.title, track_info.artist, track_info.album)
                if plex_track:
                    plex_tracks.append(plex_track)
                else:
                    logger.warning(f"Track not found in Plex: {track_info.title} by {track_info.artist}")
            
            if plex_tracks:
                playlist = self.server.createPlaylist(name, plex_tracks)
                logger.info(f"Created playlist '{name}' with {len(plex_tracks)} tracks")
                return True
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
        Searches for tracks in the Plex music library using a more robust, two-step method
        that is more compatible with different Plex server versions.
        """
        if not self.music_library:
            logger.warning("Plex music library not found. Cannot perform search.")
            return []

        try:
            # Step 1: Search for the artist first. This is generally reliable.
            artist_results = self.music_library.searchArtists(title=artist, limit=1)
            
            candidate_tracks = []
            if artist_results:
                # If artist is found, get all their tracks and filter by title in Python.
                # This avoids potential API filter issues where special characters in the title
                # might cause the search to fail.
                plex_artist = artist_results[0]
                all_artist_tracks = plex_artist.tracks()
                
                # Use a case-insensitive substring match to find potential tracks.
                # The matching engine will do the final, more precise comparison later.
                lower_title = title.lower()
                for track in all_artist_tracks:
                    if lower_title in track.title.lower():
                        candidate_tracks.append(track)
            else:
                # Fallback: If the artist wasn't found, search for the track title
                # across the entire library. This is less precise but better than nothing.
                logger.debug(f"Artist '{artist}' not found. Falling back to title search for '{title}'.")
                candidate_tracks = self.music_library.searchTracks(title=title, limit=limit)

            # Convert the raw Plex track objects to our simplified PlexTrackInfo dataclass.
            # Apply the limit here to the final list of candidates.
            tracks = [PlexTrackInfo.from_plex_track(track) for track in candidate_tracks[:limit]]
        
            if tracks:
                logger.debug(f"Plex search for title='{title}' by artist='{artist}' found {len(tracks)} potential matches.")
            
            return tracks
            
        except Exception as e:
            logger.error(f"Error searching Plex tracks for title='{title}', artist='{artist}': {e}")
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
