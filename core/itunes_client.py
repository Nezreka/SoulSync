import requests
from typing import Dict, List, Optional, Any
import time
import threading
from functools import wraps
from dataclasses import dataclass
from utils.logging_config import get_logger

logger = get_logger("itunes_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 3.0  # iTunes has ~20 calls/minute limit = 1 call per 3 seconds

def rate_limited(func):
    """Decorator to enforce rate limiting on iTunes API calls"""
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
            if "403" in str(e):
                logger.warning(f"Rate limit hit, implementing backoff: {e}")
                time.sleep(60.0)  # Wait 60 seconds for iTunes rate limit
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
    def from_itunes_track(cls, track_data: Dict[str, Any]) -> 'Track':
        # Extract album image (highest quality)
        album_image_url = None
        if 'artworkUrl100' in track_data:
            # Replace 100x100 with 600x600 for higher quality
            album_image_url = track_data['artworkUrl100'].replace('100x100bb', '600x600bb')
        
        # Get artist name(s)
        artists = [track_data.get('artistName', 'Unknown Artist')]
        
        # Build external URLs
        external_urls = {}
        if 'trackViewUrl' in track_data:
            external_urls['itunes'] = track_data['trackViewUrl']

        return cls(
            id=str(track_data.get('trackId', '')),
            name=track_data.get('trackName', ''),
            artists=artists,
            album=track_data.get('collectionName', ''),
            duration_ms=track_data.get('trackTimeMillis', 0),
            popularity=0,  # iTunes doesn't provide popularity
            preview_url=track_data.get('previewUrl'),
            external_urls=external_urls if external_urls else None,
            image_url=album_image_url
        )

@dataclass
class Artist:
    id: str
    name: str
    popularity: int  # iTunes doesn't provide this, will be 0
    genres: List[str]
    followers: int  # iTunes doesn't provide this, will be 0
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    
    @classmethod
    def from_itunes_artist(cls, artist_data: Dict[str, Any]) -> 'Artist':
        # iTunes artist search doesn't reliably return images
        image_url = None
        if 'artworkUrl100' in artist_data:
            image_url = artist_data['artworkUrl100'].replace('100x100bb', '600x600bb')
        
        # Build external URLs
        external_urls = {}
        if 'artistViewUrl' in artist_data:
            external_urls['itunes'] = artist_data['artistViewUrl']
        
        # Get genre
        genre = artist_data.get('primaryGenreName', '')
        genres = [genre] if genre else []
        
        return cls(
            id=str(artist_data.get('artistId', '')),
            name=artist_data.get('artistName', ''),
            popularity=0,  # iTunes doesn't provide popularity
            genres=genres,
            followers=0,  # iTunes doesn't provide follower count
            image_url=image_url,
            external_urls=external_urls if external_urls else None
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
    def from_itunes_album(cls, album_data: Dict[str, Any]) -> 'Album':
        # Get highest quality artwork
        image_url = None
        if album_data.get('artworkUrl100'):
            image_url = album_data['artworkUrl100'].replace('100x100bb', '600x600bb')
        
        # Build external URLs
        external_urls = {}
        if 'collectionViewUrl' in album_data:
            external_urls['itunes'] = album_data['collectionViewUrl']
        
        # Determine album type from collection type
        track_count = album_data.get('trackCount', 0)
        
        # iTunes doesn't clearly distinguish EPs, but we can infer:
        # Singles typically have 1-3 tracks, EPs have 4-6, Albums have 7+
        if track_count <= 3:
            album_type = 'single'
        elif track_count <= 6:
            album_type = 'single'  # iTunes calls EPs "albums" but we can mark shorter ones
        else:
            album_type = 'album'
        
        # Check if it's explicitly marked as compilation
        collection_type = album_data.get('collectionType', 'Album')
        if 'compilation' in collection_type.lower():
            album_type = 'compilation'
        
        return cls(
            id=str(album_data.get('collectionId', '')),
            name=album_data.get('collectionName', ''),
            artists=[album_data.get('artistName', 'Unknown Artist')],
            release_date=album_data.get('releaseDate', ''),
            total_tracks=track_count,
            album_type=album_type,
            image_url=image_url,
            external_urls=external_urls if external_urls else None
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
    def from_itunes_playlist(cls, playlist_data: Dict[str, Any], tracks: List[Track]) -> 'Playlist':
        # iTunes doesn't have playlists in the same way, but we maintain the structure
        return cls(
            id=playlist_data.get('id', ''),
            name=playlist_data.get('name', ''),
            description=playlist_data.get('description'),
            owner='iTunes',
            public=True,
            collaborative=False,
            tracks=tracks,
            total_tracks=len(tracks)
        )

class iTunesClient:
    """
    iTunes Search API client for music metadata.
    
    Provides full parity with SpotifyClient functionality.
    Free, no authentication required.
    Rate limit: ~20 calls/minute on /search, /lookup appears unlimited.
    """
    
    SEARCH_URL = "https://itunes.apple.com/search"
    LOOKUP_URL = "https://itunes.apple.com/lookup"
    
    def __init__(self, country: str = "US"):
        self.country = country
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json'
        })
        logger.info(f"iTunes client initialized for country: {country}")
    
    def is_authenticated(self) -> bool:
        """
        Check if iTunes client is available (always True since no auth required)
        """
        return True
    
    @rate_limited
    def _search(self, term: str, entity: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Generic search method for iTunes API"""
        try:
            params = {
                'term': term,
                'country': self.country,
                'media': 'music',
                'entity': entity,
                'limit': min(limit, 200)  # iTunes max is 200
            }
            
            response = self.session.get(
                self.SEARCH_URL,
                params=params,
                timeout=30
            )
            
            if response.status_code == 403:
                logger.warning("iTunes API rate limit hit")
                time.sleep(60)
                return []
            
            if response.status_code != 200:
                logger.error(f"iTunes search failed with status {response.status_code}")
                return []
            
            data = response.json()
            results = data.get('results', [])
            logger.info(f"iTunes search for '{term}' ({entity}) returned {len(results)} results")
            return results
            
        except Exception as e:
            logger.error(f"Error searching iTunes: {e}")
            return []
    
    def _lookup(self, **params) -> List[Dict[str, Any]]:
        """Generic lookup method (not rate limited)"""
        try:
            params['country'] = self.country
            
            response = self.session.get(
                self.LOOKUP_URL,
                params=params,
                timeout=30
            )
            
            if response.status_code != 200:
                logger.error(f"iTunes lookup failed with status {response.status_code}")
                return []
            
            data = response.json()
            return data.get('results', [])
            
        except Exception as e:
            logger.error(f"Error in iTunes lookup: {e}")
            return []
    
    # ==================== Track Methods ====================
    
    @rate_limited
    def search_tracks(self, query: str, limit: int = 20) -> List[Track]:
        """Search for tracks using iTunes API"""
        results = self._search(query, 'song', limit)
        tracks = []
        
        for track_data in results:
            if track_data.get('wrapperType') == 'track' and track_data.get('kind') == 'song':
                track = Track.from_itunes_track(track_data)
                tracks.append(track)
        
        return tracks
    
    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed track information including album data and track number"""
        results = self._lookup(id=track_id)
        
        for track_data in results:
            if track_data.get('wrapperType') == 'track':
                # Enhance with additional useful metadata
                enhanced_data = {
                    'id': str(track_data.get('trackId', '')),
                    'name': track_data.get('trackName', ''),
                    'track_number': track_data.get('trackNumber', 0),
                    'disc_number': track_data.get('discNumber', 1),
                    'duration_ms': track_data.get('trackTimeMillis', 0),
                    'explicit': track_data.get('trackExplicitness') == 'explicit',
                    'artists': [track_data.get('artistName', 'Unknown Artist')],
                    'primary_artist': track_data.get('artistName', 'Unknown Artist'),
                    'album': {
                        'id': str(track_data.get('collectionId', '')),
                        'name': track_data.get('collectionName', ''),
                        'total_tracks': track_data.get('trackCount', 0),
                        'release_date': track_data.get('releaseDate', ''),
                        'album_type': 'album',  # iTunes doesn't distinguish clearly
                        'artists': [track_data.get('artistName', 'Unknown Artist')]
                    },
                    'is_album_track': track_data.get('trackCount', 0) > 1,
                    'raw_data': track_data
                }
                return enhanced_data
        
        return None
    
    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        """
        Get track audio features (NOT SUPPORTED by iTunes API)
        Returns None as iTunes doesn't provide audio features like Spotify
        """
        logger.warning("iTunes API does not support audio features")
        return None
    
    # ==================== Album Methods ====================
    
    @rate_limited
    def search_albums(self, query: str, limit: int = 20) -> List[Album]:
        """Search for albums using iTunes API"""
        results = self._search(query, 'album', limit)
        albums = []
        
        for album_data in results:
            if album_data.get('wrapperType') == 'collection':
                album = Album.from_itunes_album(album_data)
                albums.append(album)
        
        return albums
    
    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album information - normalized to Spotify format"""
        results = self._lookup(id=album_id)

        for album_data in results:
            if album_data.get('wrapperType') == 'collection':
                # Normalize to Spotify-compatible format
                image_url = None
                if album_data.get('artworkUrl100'):
                    image_url = album_data['artworkUrl100'].replace('100x100bb', '600x600bb')

                # Build images array like Spotify (multiple sizes)
                images = []
                if image_url:
                    images = [
                        {'url': image_url, 'height': 600, 'width': 600},
                        {'url': album_data['artworkUrl100'].replace('100x100bb', '300x300bb'), 'height': 300, 'width': 300},
                        {'url': album_data['artworkUrl100'], 'height': 100, 'width': 100}
                    ]

                # Determine album type
                track_count = album_data.get('trackCount', 0)
                if track_count <= 3:
                    album_type = 'single'
                elif track_count <= 6:
                    album_type = 'single'  # EP treated as single
                else:
                    album_type = 'album'

                return {
                    'id': str(album_data.get('collectionId', '')),
                    'name': album_data.get('collectionName', ''),
                    'images': images,
                    'artists': [{'name': album_data.get('artistName', 'Unknown Artist'), 'id': str(album_data.get('artistId', ''))}],
                    'release_date': album_data.get('releaseDate', '')[:10] if album_data.get('releaseDate') else '',  # YYYY-MM-DD format
                    'total_tracks': track_count,
                    'album_type': album_type,
                    'external_urls': {'itunes': album_data.get('collectionViewUrl', '')},
                    'uri': f"itunes:album:{album_data.get('collectionId', '')}",
                    '_source': 'itunes',
                    '_raw_data': album_data
                }

        return None
    
    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album tracks - normalized to Spotify format"""
        results = self._lookup(id=album_id, entity='song')

        if not results:
            return None

        # First result is usually the album/collection info
        # Remaining results are tracks
        tracks = []
        for item in results:
            if item.get('wrapperType') == 'track' and item.get('kind') == 'song':
                # Normalize each track to Spotify-compatible format
                normalized_track = {
                    'id': str(item.get('trackId', '')),
                    'name': item.get('trackName', ''),
                    'artists': [{'name': item.get('artistName', 'Unknown Artist')}],  # List of dicts like Spotify
                    'duration_ms': item.get('trackTimeMillis', 0),
                    'track_number': item.get('trackNumber', 0),
                    'disc_number': item.get('discNumber', 1),
                    'explicit': item.get('trackExplicitness') == 'explicit',
                    'preview_url': item.get('previewUrl'),
                    'uri': f"itunes:track:{item.get('trackId', '')}",  # Synthetic URI
                    'external_urls': {'itunes': item.get('trackViewUrl', '')},
                    '_source': 'itunes'
                }
                tracks.append(normalized_track)

        # Sort by disc and track number
        tracks.sort(key=lambda t: (t.get('disc_number', 1), t.get('track_number', 0)))

        logger.info(f"Retrieved {len(tracks)} tracks for album {album_id}")

        return {
            'items': tracks,
            'total': len(tracks),
            'limit': len(tracks),
            'next': None
        }
    
    # ==================== Artist Methods ====================
    
    @rate_limited
    def search_artists(self, query: str, limit: int = 20) -> List[Artist]:
        """Search for artists using iTunes API"""
        results = self._search(query, 'musicArtist', limit)
        artists = []
        
        for artist_data in results:
            if artist_data.get('wrapperType') == 'artist':
                artist = Artist.from_itunes_artist(artist_data)
                artists.append(artist)
        
        return artists
    
    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full artist details - normalized to Spotify format.

        Args:
            artist_id: iTunes artist ID

        Returns:
            Dictionary with artist data matching Spotify's format
        """
        results = self._lookup(id=artist_id)

        for artist_data in results:
            if artist_data.get('wrapperType') == 'artist':
                # Build images array - iTunes artist search doesn't reliably return images
                # but we include the structure for compatibility
                images = []
                if artist_data.get('artworkUrl100'):
                    artwork_base = artist_data['artworkUrl100']
                    images = [
                        {'url': artwork_base.replace('100x100bb', '600x600bb'), 'height': 600, 'width': 600},
                        {'url': artwork_base.replace('100x100bb', '300x300bb'), 'height': 300, 'width': 300},
                        {'url': artwork_base, 'height': 100, 'width': 100}
                    ]

                # Get genre
                genres = []
                if artist_data.get('primaryGenreName'):
                    genres = [artist_data['primaryGenreName']]

                return {
                    'id': str(artist_data.get('artistId', '')),
                    'name': artist_data.get('artistName', ''),
                    'images': images,
                    'genres': genres,
                    'popularity': 0,  # iTunes doesn't provide this
                    'followers': {'total': 0},  # iTunes doesn't provide this
                    'external_urls': {'itunes': artist_data.get('artistViewUrl', '')},
                    'uri': f"itunes:artist:{artist_data.get('artistId', '')}",
                    '_source': 'itunes',
                    '_raw_data': artist_data
                }

        return None
    
    def get_artist_albums(self, artist_id: str, album_type: str = 'album,single', limit: int = 50) -> List[Album]:
        """
        Get albums by artist ID
        
        Note: iTunes doesn't support filtering by album_type in the same way as Spotify,
        so we fetch all albums and can filter client-side if needed.
        """
        results = self._lookup(id=artist_id, entity='album', limit=min(limit, 200))
        albums = []
        
        for album_data in results:
            if album_data.get('wrapperType') == 'collection':
                album = Album.from_itunes_album(album_data)
                
                # Filter by album_type if specified
                if album_type != 'album,single':
                    requested_types = album_type.split(',')
                    if album.album_type not in requested_types:
                        continue
                
                albums.append(album)
        
        logger.info(f"Retrieved {len(albums)} albums for artist {artist_id}")
        return albums[:limit]
    
    # ==================== Playlist Methods ====================
    
    def _get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        """
        Get playlist tracks (NOT SUPPORTED by iTunes API)
        Internal helper method to match Spotify client structure
        """
        logger.warning("iTunes API does not support playlists")
        return []
    
    def get_user_playlists(self) -> List[Playlist]:
        """
        Get user playlists (NOT SUPPORTED by iTunes API)
        iTunes doesn't have user playlists accessible via API
        """
        logger.warning("iTunes API does not support user playlists")
        return []
    
    def get_user_playlists_metadata_only(self) -> List[Playlist]:
        """
        Get playlists metadata only (NOT SUPPORTED by iTunes API)
        """
        logger.warning("iTunes API does not support user playlists")
        return []
    
    def get_saved_tracks_count(self) -> int:
        """
        Get saved tracks count (NOT SUPPORTED by iTunes API)
        """
        logger.warning("iTunes API does not support saved/liked tracks")
        return 0
    
    def get_saved_tracks(self) -> List[Track]:
        """
        Get saved/liked tracks (NOT SUPPORTED by iTunes API)
        """
        logger.warning("iTunes API does not support saved/liked tracks")
        return []
    
    def get_playlist_by_id(self, playlist_id: str) -> Optional[Playlist]:
        """
        Get playlist by ID (NOT SUPPORTED by iTunes API)
        """
        logger.warning("iTunes API does not support playlists")
        return None
    
    # ==================== User Methods ====================
    
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        """
        Get user info (NOT SUPPORTED by iTunes API - no authentication)
        """
        logger.warning("iTunes API does not support user authentication")
        return None
    
    def reload_config(self):
        """Reload configuration (no-op for iTunes since no auth required)"""
        logger.info("iTunes client config reload requested (no-op)")
        pass
