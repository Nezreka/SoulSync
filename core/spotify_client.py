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
MIN_API_INTERVAL = 0.2  # 200ms between API calls (more conservative to avoid bans)

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
                logger.warning(f"Rate limit hit, implementing backoff: {e}")
                # Use longer backoff to avoid getting banned
                time.sleep(3.0)  # Wait 3 seconds before retrying
            elif "503" in str(e) or "502" in str(e):
                logger.warning(f"Spotify service error, backing off: {e}")
                time.sleep(2.0)  # Wait 2 seconds for service errors
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
    image_url: Optional[str] = None

    @classmethod
    def from_spotify_track(cls, track_data: Dict[str, Any]) -> 'Track':
        # Extract album image (medium size preferred)
        album_image_url = None
        if 'album' in track_data and 'images' in track_data['album']:
            images = track_data['album']['images']
            if images:
                # Get medium size image (usually index 1), or largest if not available
                album_image_url = images[1]['url'] if len(images) > 1 else images[0]['url']

        return cls(
            id=track_data['id'],
            name=track_data['name'],
            artists=[artist['name'] for artist in track_data['artists']],
            album=track_data['album']['name'],
            duration_ms=track_data['duration_ms'],
            popularity=track_data.get('popularity', 0),
            preview_url=track_data.get('preview_url'),
            external_urls=track_data.get('external_urls'),
            image_url=album_image_url
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
            total_tracks=(playlist_data.get('tracks') or playlist_data.get('items') or {}).get('total', 0)
        )

class SpotifyClient:
    def __init__(self):
        self.sp: Optional[spotipy.Spotify] = None
        self.user_id: Optional[str] = None
        self._itunes_client = None  # Lazy-loaded iTunes fallback
        self._setup_client()

    def _is_spotify_id(self, id_str: str) -> bool:
        """Check if an ID is a Spotify ID (alphanumeric) vs iTunes ID (numeric only)"""
        if not id_str:
            return False
        # Spotify IDs contain letters and numbers, iTunes IDs are purely numeric
        return not id_str.isdigit()

    def _is_itunes_id(self, id_str: str) -> bool:
        """Check if an ID is an iTunes ID (numeric only)"""
        if not id_str:
            return False
        return id_str.isdigit()

    @property
    def _itunes(self):
        """Lazy-load iTunes client for fallback when Spotify not authenticated"""
        if self._itunes_client is None:
            from core.itunes_client import iTunesClient
            self._itunes_client = iTunesClient()
            logger.info("iTunes fallback client initialized")
        return self._itunes_client

    def reload_config(self):
        """Reload configuration and re-initialize client"""
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
                redirect_uri=config.get('redirect_uri', "http://127.0.0.1:8888/callback"),
                scope="user-library-read user-read-private playlist-read-private playlist-read-collaborative user-read-email",
                cache_path='config/.spotify_cache'
            )
            
            self.sp = spotipy.Spotify(auth_manager=auth_manager)
            # Don't fetch user info on startup - do it lazily to avoid blocking UI
            self.user_id = None
            logger.info("Spotify client initialized (user info will be fetched when needed)")
            
        except Exception as e:
            logger.error(f"Failed to authenticate with Spotify: {e}")
            self.sp = None
    
    def is_authenticated(self) -> bool:
        """
        Check if client can service metadata requests.
        Returns True if Spotify is authenticated OR iTunes fallback is available.
        For Spotify-specific auth check, use is_spotify_authenticated().
        """
        # If Spotify is authenticated, we're good
        if self.is_spotify_authenticated():
            return True

        # iTunes fallback is always available
        return True

    def is_spotify_authenticated(self) -> bool:
        """Check if Spotify client is specifically authenticated (not just iTunes fallback)"""
        if self.sp is None:
            return False

        try:
            # Make a simple API call to verify authentication
            self.sp.current_user()
            return True
        except Exception as e:
            logger.debug(f"Spotify authentication check failed: {e}")
            return False
    
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
    
    @rate_limited
    def get_user_playlists(self) -> List[Playlist]:
        if not self.is_spotify_authenticated():
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
                    # Spotify API already returns all playlists the user has access to
                    # (owned + followed), so no need to filter
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
    
    @rate_limited
    def get_user_playlists_metadata_only(self) -> List[Playlist]:
        """Get playlists without fetching all track details for faster loading"""
        if not self.is_spotify_authenticated():
            logger.error("Not authenticated with Spotify")
            return []
        
        if not self._ensure_user_id():
            logger.error("Failed to get user ID")
            return []
        
        playlists = []
        
        try:
            # Fetch all playlists using pagination
            limit = 50  # Maximum allowed by Spotify API
            offset = 0
            total_fetched = 0
            
            logger.info("Beginning fetch of user playlists...")
            
            while True:
                results = self.sp.current_user_playlists(limit=limit, offset=offset)
                
                if not results or 'items' not in results:
                    break
                    
                # Log expected total on first page
                if offset == 0:
                    expected_total = results.get('total', 'Unknown')
                    logger.info(f"Spotify reports {expected_total} total playlists to fetch.")
                
                batch_count = 0
                for playlist_data in results['items']:
                    try:
                        # Spotify API already returns all playlists the user has access to
                        # (owned + followed), so no need to filter
                        
                        # Handle potential missing owner data safely
                        if not playlist_data.get('owner'):
                            playlist_data['owner'] = {'display_name': 'Unknown Owner', 'id': 'unknown'}
                        elif not playlist_data['owner'].get('display_name'):
                            playlist_data['owner']['display_name'] = 'Unknown'

                        # Create playlist with empty tracks list for now
                        playlist = Playlist.from_spotify_playlist(playlist_data, [])
                        playlists.append(playlist)
                        batch_count += 1
                        
                    except Exception as p_error:
                        p_name = playlist_data.get('name', 'Unknown') if playlist_data else 'None'
                        logger.warning(f"Skipping malformed playlist '{p_name}': {p_error}")
                
                total_fetched += batch_count
                logger.info(f"Retrieved {batch_count} playlists in batch (offset {offset}), total so far: {total_fetched}")
                
                # Check if we've fetched all playlists
                if len(results['items']) < limit or not results.get('next'):
                    break
                    
                offset += limit
            
            logger.info(f"Retrieved {len(playlists)} total playlist metadata")
            return playlists

        except Exception as e:
            logger.error(f"Error fetching user playlists metadata: {e}")
            # Return partial results if we crashed mid-way but have some data
            if playlists:
                 logger.info(f"Returning {len(playlists)} playlists fetched before error.")
                 return playlists
            return []

    @rate_limited
    def get_saved_tracks_count(self) -> int:
        """Get the total count of user's saved/liked songs without fetching all tracks"""
        if not self.is_spotify_authenticated():
            logger.error("Not authenticated with Spotify")
            return 0

        try:
            # Just fetch first page to get the total count
            results = self.sp.current_user_saved_tracks(limit=1)
            if results and 'total' in results:
                total_count = results['total']
                logger.info(f"User has {total_count} saved tracks")
                return total_count
            return 0
        except Exception as e:
            logger.error(f"Error fetching saved tracks count: {e}")
            return 0

    @rate_limited
    def get_saved_tracks(self) -> List[Track]:
        """Fetch all user's saved/liked songs from Spotify"""
        if not self.is_spotify_authenticated():
            logger.error("Not authenticated with Spotify")
            return []

        tracks = []

        try:
            limit = 50  # Maximum allowed by Spotify API
            offset = 0
            total_fetched = 0

            while True:
                results = self.sp.current_user_saved_tracks(limit=limit, offset=offset)

                if not results or 'items' not in results:
                    break

                batch_count = 0
                for item in results['items']:
                    if item['track'] and item['track']['id']:
                        track = Track.from_spotify_track(item['track'])
                        tracks.append(track)
                        batch_count += 1

                total_fetched += batch_count
                logger.info(f"Retrieved {batch_count} saved tracks in batch (offset {offset}), total: {total_fetched}")

                # Check if we've fetched all saved tracks
                if len(results['items']) < limit or not results.get('next'):
                    break

                offset += limit

            logger.info(f"Retrieved {len(tracks)} total saved tracks")
            return tracks

        except Exception as e:
            logger.error(f"Error fetching saved tracks: {e}")
            return []

    @rate_limited
    def _get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        if not self.is_spotify_authenticated():
            return []
        
        tracks = []
        
        try:
            results = self.sp.playlist_items(playlist_id, limit=100)

            while results:
                for item in results['items']:
                    # Handle both old API ('track') and new Feb 2026 API ('item') field names
                    track_data = item.get('track') or item.get('item')
                    if track_data and track_data.get('id'):
                        track = Track.from_spotify_track(track_data)
                        tracks.append(track)
                
                results = self.sp.next(results) if results['next'] else None
            
            return tracks
            
        except Exception as e:
            logger.error(f"Error fetching playlist tracks: {e}")
            return []
    
    @rate_limited
    def get_playlist_by_id(self, playlist_id: str) -> Optional[Playlist]:
        if not self.is_spotify_authenticated():
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
        """Search for tracks - falls back to iTunes if Spotify not authenticated"""
        if self.is_spotify_authenticated():
            try:
                results = self.sp.search(q=query, type='track', limit=limit)
                tracks = []

                for track_data in results['tracks']['items']:
                    track = Track.from_spotify_track(track_data)
                    tracks.append(track)

                return tracks

            except Exception as e:
                logger.error(f"Error searching tracks via Spotify: {e}")
                # Fall through to iTunes fallback

        # iTunes fallback
        logger.debug(f"Using iTunes fallback for track search: {query}")
        return self._itunes.search_tracks(query, limit)

    @rate_limited
    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        """Search for artists - falls back to iTunes if Spotify not authenticated"""
        if self.is_spotify_authenticated():
            try:
                results = self.sp.search(q=query, type='artist', limit=limit)
                artists = []

                for artist_data in results['artists']['items']:
                    artist = Artist.from_spotify_artist(artist_data)
                    artists.append(artist)

                return artists

            except Exception as e:
                logger.error(f"Error searching artists via Spotify: {e}")
                # Fall through to iTunes fallback

        # iTunes fallback
        logger.debug(f"Using iTunes fallback for artist search: {query}")
        return self._itunes.search_artists(query, limit)

    @rate_limited
    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        """Search for albums - falls back to iTunes if Spotify not authenticated"""
        if self.is_spotify_authenticated():
            try:
                results = self.sp.search(q=query, type='album', limit=limit)
                albums = []

                for album_data in results['albums']['items']:
                    album = Album.from_spotify_album(album_data)
                    albums.append(album)

                return albums

            except Exception as e:
                logger.error(f"Error searching albums via Spotify: {e}")
                # Fall through to iTunes fallback

        # iTunes fallback
        logger.debug(f"Using iTunes fallback for album search: {query}")
        return self._itunes.search_albums(query, limit)
    
    @rate_limited
    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed track information - falls back to iTunes if Spotify not authenticated"""
        if self.is_spotify_authenticated():
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
                logger.error(f"Error fetching track details via Spotify: {e}")
                # Fall through to iTunes fallback

        # iTunes fallback - only if ID is numeric (iTunes format)
        if self._is_itunes_id(track_id):
            logger.debug(f"Using iTunes fallback for track details: {track_id}")
            return self._itunes.get_track_details(track_id)
        else:
            logger.debug(f"Cannot use iTunes fallback for Spotify track ID: {track_id}")
            return None
    
    @rate_limited
    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_spotify_authenticated():
            return None
        
        try:
            features = self.sp.audio_features(track_id)
            return features[0] if features else None
            
        except Exception as e:
            logger.error(f"Error fetching track features: {e}")
            return None
    
    @rate_limited
    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album information - falls back to iTunes if Spotify not authenticated"""
        if self.is_spotify_authenticated():
            try:
                album_data = self.sp.album(album_id)
                return album_data

            except Exception as e:
                logger.error(f"Error fetching album via Spotify: {e}")
                # Fall through to iTunes fallback

        # iTunes fallback - only if ID is numeric (iTunes format)
        if self._is_itunes_id(album_id):
            logger.debug(f"Using iTunes fallback for album: {album_id}")
            return self._itunes.get_album(album_id)
        else:
            logger.debug(f"Cannot use iTunes fallback for Spotify album ID: {album_id}")
            return None
    
    @rate_limited
    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album tracks - falls back to iTunes if Spotify not authenticated"""
        if self.is_spotify_authenticated():
            try:
                # Get first page of tracks
                first_page = self.sp.album_tracks(album_id)
                if not first_page or 'items' not in first_page:
                    return None

                # Collect all tracks starting with first page
                all_tracks = first_page['items'][:]

                # Fetch remaining pages if they exist
                next_page = first_page
                while next_page.get('next'):
                    next_page = self.sp.next(next_page)
                    if next_page and 'items' in next_page:
                        all_tracks.extend(next_page['items'])

                # Log success
                logger.info(f"Retrieved {len(all_tracks)} tracks for album {album_id}")

                # Return structure with all tracks
                result = first_page.copy()
                result['items'] = all_tracks
                result['next'] = None  # No more pages
                result['limit'] = len(all_tracks)  # Update to reflect all tracks fetched

                return result

            except Exception as e:
                logger.error(f"Error fetching album tracks via Spotify: {e}")
                # Fall through to iTunes fallback

        # iTunes fallback - only if ID is numeric (iTunes format)
        if self._is_itunes_id(album_id):
            logger.debug(f"Using iTunes fallback for album tracks: {album_id}")
            return self._itunes.get_album_tracks(album_id)
        else:
            logger.debug(f"Cannot use iTunes fallback for Spotify album ID: {album_id}")
            return None
    
    @rate_limited
    def get_artist_albums(self, artist_id: str, album_type: str = 'album,single', limit: int = 50) -> List[Album]:
        """Get albums by artist ID - falls back to iTunes if Spotify not authenticated"""
        if self.is_spotify_authenticated():
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
                logger.error(f"Error fetching artist albums via Spotify: {e}")
                # Fall through to iTunes fallback

        # iTunes fallback - only if ID is numeric (iTunes format)
        if self._is_itunes_id(artist_id):
            logger.debug(f"Using iTunes fallback for artist albums: {artist_id}")
            return self._itunes.get_artist_albums(artist_id, album_type, limit)
        else:
            logger.debug(f"Cannot use iTunes fallback for Spotify artist ID: {artist_id}")
            return []

    @rate_limited
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        if not self.is_spotify_authenticated():
            return None

        try:
            return self.sp.current_user()
        except Exception as e:
            logger.error(f"Error fetching user info: {e}")
            return None

    @rate_limited
    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full artist details - falls back to iTunes if Spotify not authenticated.

        Args:
            artist_id: Artist ID (Spotify or iTunes depending on authentication)

        Returns:
            Dictionary with artist data including images, genres, popularity
        """
        if self.is_spotify_authenticated():
            try:
                return self.sp.artist(artist_id)
            except Exception as e:
                logger.error(f"Error fetching artist via Spotify: {e}")
                # Fall through to iTunes fallback

        # iTunes fallback - only if ID is numeric (iTunes format)
        if self._is_itunes_id(artist_id):
            logger.debug(f"Using iTunes fallback for artist: {artist_id}")
            return self._itunes.get_artist(artist_id)
        else:
            logger.debug(f"Cannot use iTunes fallback for Spotify artist ID: {artist_id}")
            return None