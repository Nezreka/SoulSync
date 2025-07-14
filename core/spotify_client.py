import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
from typing import Dict, List, Optional, Any
import time
from dataclasses import dataclass
from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("spotify_client")

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
            user_info = self.sp.current_user()
            self.user_id = user_info['id']
            logger.info(f"Successfully authenticated with Spotify as {user_info['display_name']}")
            
        except Exception as e:
            logger.error(f"Failed to authenticate with Spotify: {e}")
            self.sp = None
    
    def is_authenticated(self) -> bool:
        return self.sp is not None and self.user_id is not None
    
    def get_user_playlists(self) -> List[Playlist]:
        if not self.is_authenticated():
            logger.error("Not authenticated with Spotify")
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
    
    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_authenticated():
            return None
        
        try:
            features = self.sp.audio_features(track_id)
            return features[0] if features else None
            
        except Exception as e:
            logger.error(f"Error fetching track features: {e}")
            return None
    
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        if not self.is_authenticated():
            return None
        
        try:
            return self.sp.current_user()
        except Exception as e:
            logger.error(f"Error fetching user info: {e}")
            return None