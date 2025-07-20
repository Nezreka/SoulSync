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
        return cls(
            id=str(track.ratingKey),
            title=track.title,
            artist=track.artist().title if track.artist() else "Unknown Artist",
            album=track.album().title if track.album() else "Unknown Album",
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
                # Use shorter timeout (5 seconds) to prevent app freezing
                self.server = PlexServer(config['base_url'], config['token'], timeout=5)
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
    
    def search_tracks(self, query: str, limit: int = 20) -> List[PlexTrackInfo]:
        if not self.music_library:
            return []
        
        try:
            results = self.music_library.search(query, limit=limit)
            tracks = []
            
            for result in results:
                if isinstance(result, PlexTrack):
                    tracks.append(PlexTrackInfo.from_plex_track(result))
            
            return tracks
            
        except Exception as e:
            logger.error(f"Error searching tracks: {e}")
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