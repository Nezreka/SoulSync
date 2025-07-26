import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
from typing import Dict, List, Optional, Any
import time
import threading
from functools import wraps
from dataclasses import dataclass
from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("spotify_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 0.1  # 100ms between API calls

# Request queuing for burst handling
import queue
_request_queue = queue.Queue()
_queue_processor_running = False

def rate_limited(func):
    """Decorator to enforce rate limiting on Spotify API calls"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _last_api_call_time
        
        with _api_call_lock:
            current_time = time.time()
            time_since_last_call = current_time - _last_api_call_time
            
            if time_since_last_call < MIN_API_INTERVAL:
                sleep_time = MIN_API_INTERVAL - time_since_last_call
                time.sleep(sleep_time)
            
            _last_api_call_time = time.time()
            
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            # Implement exponential backoff for API errors
            if "rate limit" in str(e).lower() or "429" in str(e):
                logger.warning(f"Rate limit hit, backing off: {e}")
                time.sleep(1.0)  # Wait 1 second before retrying
            raise e
    return wrapper

@dataclass
class Track:
    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    popularity: int
    preview_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    
    @classmethod
    def from_spotify_track(cls, track_data: Dict[str, Any]) -> 'Track':
        return cls(
            id=track_data['id'],
            name=track_data['name'],
            artists=[artist['name'] for artist in track_data['artists']],
            album=track_data['album']['name'],
            duration_ms=track_data['duration_ms'],
            popularity=track_data['popularity'],
            preview_url=track_data.get('preview_url'),
            external_urls=track_data.get('external_urls')
        )

@dataclass
class Artist:
    id: str
    name: str
    popularity: int
    genres: List[str]
    followers: int
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    
    @classmethod
    def from_spotify_artist(cls, artist_data: Dict[str, Any]) -> 'Artist':
        # Get the largest image URL if available
        image_url = None
        if artist_data.get('images') and len(artist_data['images']) > 0:
            image_url = artist_data['images'][0]['url']
        
        return cls(
            id=artist_data['id'],
            name=artist_data['name'],
            popularity=artist_data.get('popularity', 0),
            genres=artist_data.get('genres', []),
            followers=artist_data.get('followers', {}).get('total', 0),
            image_url=image_url,
            external_urls=artist_data.get('external_urls')
        )

@dataclass
class Album:
    id: str
    name: str
    artists: List[str]
    release_date: str
    total_tracks: int
    album_type: str
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    
    @classmethod
    def from_spotify_album(cls, album_data: Dict[str, Any]) -> 'Album':
        # Get the largest image URL if available
        image_url = None
        if album_data.get('images') and len(album_data['images']) > 0:
            image_url = album_data['images'][0]['url']
        
        return cls(
            id=album_data['id'],
            name=album_data['name'],
            artists=[artist['name'] for artist in album_data['artists']],
            release_date=album_data.get('release_date', ''),
            total_tracks=album_data.get('total_tracks', 0),
            album_type=album_data.get('album_type', 'album'),
            image_url=image_url,
            external_urls=album_data.get('external_urls')
        )

@dataclass
class Playlist:
    id: str
    name: str
    description: Optional[str]
    owner: str
    public: bool
    collaborative: bool
    tracks: List[Track]
    total_tracks: int
    
    @classmethod
    def from_spotify_playlist(cls, playlist_data: Dict[str, Any], tracks: List[Track]) -> 'Playlist':
        return cls(
            id=playlist_data['id'],
            name=playlist_data['name'],
            description=playlist_data.get('description'),
            owner=playlist_data['owner']['display_name'],
            public=playlist_data['public'],
            collaborative=playlist_data['collaborative'],
            tracks=tracks,
            total_tracks=playlist_data['tracks']['total']
        )

class SpotifyClient:
    def __init__(self):
        self.sp: Optional[spotipy.Spotify] = None
        self.user_id: Optional[str] = None
        self._setup_client()
    
    def _setup_client(self):
        config = config_manager.get_spotify_config()
        
        if not config.get('client_id') or not config.get('client_secret'):
            logger.warning("Spotify credentials not configured")
            return
        
        try:
            auth_manager = SpotifyOAuth(
                client_id=config['client_id'],
                client_secret=config['client_secret'],
                redirect_uri="http://localhost:8888/callback",
                scope="user-library-read user-read-private playlist-read-private playlist-read-collaborative user-read-email",
                cache_path='.spotify_cache'
            )
            
            self.sp = spotipy.Spotify(auth_manager=auth_manager)
            # Don't fetch user info on startup - do it lazily to avoid blocking UI
            self.user_id = None
            logger.info("Spotify client initialized (user info will be fetched when needed)")
            
        except Exception as e:
            logger.error(f"Failed to authenticate with Spotify: {e}")
            self.sp = None
    
    def is_authenticated(self) -> bool:
        """Check if Spotify client is set up (fast check, no API calls)"""
        return self.sp is not None
    
    def _ensure_user_id(self) -> bool:
        """Ensure user_id is loaded (may make API call)"""
        if self.user_id is None and self.sp is not None:
            try:
                user_info = self.sp.current_user()
                self.user_id = user_info['id']
                logger.info(f"Successfully authenticated with Spotify as {user_info['display_name']}")
                return True
            except Exception as e:
                logger.error(f"Failed to fetch user info: {e}")
                return False
        return self.user_id is not None
    
    def get_user_playlists(self) -> List[Playlist]:
        if not self.is_authenticated():
            logger.error("Not authenticated with Spotify")
            return []
        
        if not self._ensure_user_id():
            logger.error("Failed to get user ID")
            return []
        
        playlists = []
        
        try:
            results = self.sp.current_user_playlists(limit=50)
            
            while results:
                for playlist_data in results['items']:
                    if playlist_data['owner']['id'] == self.user_id or playlist_data['collaborative']:
                        logger.info(f"Fetching tracks for playlist: {playlist_data['name']}")
                        tracks = self._get_playlist_tracks(playlist_data['id'])
                        playlist = Playlist.from_spotify_playlist(playlist_data, tracks)
                        playlists.append(playlist)
                
                results = self.sp.next(results) if results['next'] else None
            
            logger.info(f"Retrieved {len(playlists)} playlists")
            return playlists
            
        except Exception as e:
            logger.error(f"Error fetching user playlists: {e}")
            return []
    
    def get_user_playlists_metadata_only(self) -> List[Playlist]:
        """Get playlists without fetching all track details for faster loading"""
        if not self.is_authenticated():
            logger.error("Not authenticated with Spotify")
            return []
        
        if not self._ensure_user_id():
            logger.error("Failed to get user ID")
            return []
        
        playlists = []
        
        try:
            # Only fetch first batch initially for faster loading
            results = self.sp.current_user_playlists(limit=20)
            
            if results and 'items' in results:
                for playlist_data in results['items']:
                    if playlist_data['owner']['id'] == self.user_id or playlist_data['collaborative']:
                        # Create playlist with empty tracks list for now
                        playlist = Playlist.from_spotify_playlist(playlist_data, [])
                        playlists.append(playlist)
            
            logger.info(f"Retrieved {len(playlists)} playlist metadata (first batch)")
            return playlists
            
        except Exception as e:
            logger.error(f"Error fetching user playlists metadata: {e}")
            return []
    
    def _get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        if not self.is_authenticated():
            return []
        
        tracks = []
        
        try:
            results = self.sp.playlist_tracks(playlist_id, limit=100)
            
            while results:
                for item in results['items']:
                    if item['track'] and item['track']['id']:
                        track = Track.from_spotify_track(item['track'])
                        tracks.append(track)
                
                results = self.sp.next(results) if results['next'] else None
            
            return tracks
            
        except Exception as e:
            logger.error(f"Error fetching playlist tracks: {e}")
            return []
    
    def get_playlist_by_id(self, playlist_id: str) -> Optional[Playlist]:
        if not self.is_authenticated():
            return None
        
        try:
            playlist_data = self.sp.playlist(playlist_id)
            tracks = self._get_playlist_tracks(playlist_id)
            return Playlist.from_spotify_playlist(playlist_data, tracks)
            
        except Exception as e:
            logger.error(f"Error fetching playlist {playlist_id}: {e}")
            return None
    
    @rate_limited
    def search_tracks(self, query: str, limit: int = 20) -> List[Track]:
        if not self.is_authenticated():
            return []
        
        try:
            results = self.sp.search(q=query, type='track', limit=limit)
            tracks = []
            
            for track_data in results['tracks']['items']:
                track = Track.from_spotify_track(track_data)
                tracks.append(track)
            
            return tracks
            
        except Exception as e:
            logger.error(f"Error searching tracks: {e}")
            return []
    
    @rate_limited
    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        """Search for artists using Spotify API"""
        if not self.is_authenticated():
            return []
        
        try:
            results = self.sp.search(q=query, type='artist', limit=limit)
            artists = []
            
            for artist_data in results['artists']['items']:
                artist = Artist.from_spotify_artist(artist_data)
                artists.append(artist)
            
            return artists
            
        except Exception as e:
            logger.error(f"Error searching artists: {e}")
            return []
    
    @rate_limited
    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        """Search for albums using Spotify API"""
        if not self.is_authenticated():
            return []
        
        try:
            results = self.sp.search(q=query, type='album', limit=limit)
            albums = []
            
            for album_data in results['albums']['items']:
                album = Album.from_spotify_album(album_data)
                albums.append(album)
            
            return albums
            
        except Exception as e:
            logger.error(f"Error searching albums: {e}")
            return []
    
    @rate_limited
    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed track information including album data and track number"""
        if not self.is_authenticated():
            return None
        
        try:
            track_data = self.sp.track(track_id)
            
            # Enhance with additional useful metadata for our purposes
            if track_data:
                enhanced_data = {
                    'id': track_data['id'],
                    'name': track_data['name'],
                    'track_number': track_data['track_number'],
                    'disc_number': track_data['disc_number'],
                    'duration_ms': track_data['duration_ms'],
                    'explicit': track_data['explicit'],
                    'artists': [artist['name'] for artist in track_data['artists']],
                    'primary_artist': track_data['artists'][0]['name'] if track_data['artists'] else None,
                    'album': {
                        'id': track_data['album']['id'],
                        'name': track_data['album']['name'],
                        'total_tracks': track_data['album']['total_tracks'],
                        'release_date': track_data['album']['release_date'],
                        'album_type': track_data['album']['album_type'],
                        'artists': [artist['name'] for artist in track_data['album']['artists']]
                    },
                    'is_album_track': track_data['album']['total_tracks'] > 1,
                    'raw_data': track_data  # Keep original for fallback
                }
                return enhanced_data
            return track_data
            
        except Exception as e:
            logger.error(f"Error fetching track details: {e}")
            return None
    
    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_authenticated():
            return None
        
        try:
            features = self.sp.audio_features(track_id)
            return features[0] if features else None
            
        except Exception as e:
            logger.error(f"Error fetching track features: {e}")
            return None
    
    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album information including tracks"""
        if not self.is_authenticated():
            return None
        
        try:
            album_data = self.sp.album(album_id)
            return album_data
            
        except Exception as e:
            logger.error(f"Error fetching album: {e}")
            return None
    
    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album tracks"""
        if not self.is_authenticated():
            return None
        
        try:
            tracks_data = self.sp.album_tracks(album_id)
            return tracks_data
            
        except Exception as e:
            logger.error(f"Error fetching album tracks: {e}")
            return None
    
    @rate_limited
    def get_artist_albums(self, artist_id: str, album_type: str = 'album,single', limit: int = 50) -> List[Album]:
        """Get albums by artist ID"""
        if not self.is_authenticated():
            return []
        
        try:
            albums = []
            results = self.sp.artist_albums(artist_id, album_type=album_type, limit=limit)
            
            while results:
                for album_data in results['items']:
                    album = Album.from_spotify_album(album_data)
                    albums.append(album)
                
                # Get next batch if available
                results = self.sp.next(results) if results['next'] else None
            
            logger.info(f"Retrieved {len(albums)} albums for artist {artist_id}")
            return albums
            
        except Exception as e:
            logger.error(f"Error fetching artist albums: {e}")
            return []

    def get_user_info(self) -> Optional[Dict[str, Any]]:
        if not self.is_authenticated():
            return None
        
        try:
            return self.sp.current_user()
        except Exception as e:
            logger.error(f"Error fetching user info: {e}")
            return None