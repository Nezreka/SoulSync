import requests
import time
import threading
from typing import Dict, List, Optional, Any
from functools import wraps
from utils.logging_config import get_logger

logger = get_logger("musicbrainz_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 1.0  # 1 second between API calls (MusicBrainz requirement)

def rate_limited(func):
    """Decorator to enforce rate limiting on MusicBrainz API calls"""
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
            if "rate limit" in str(e).lower() or "503" in str(e):
                logger.warning(f"MusicBrainz rate limit hit, implementing backoff: {e}")
                time.sleep(2.0)  # Wait 2 seconds before retrying
            raise e
    return wrapper

class MusicBrainzClient:
    """Client for interacting with MusicBrainz API"""
    
    BASE_URL = "https://musicbrainz.org/ws/2"
    
    def __init__(self, app_name: str = "SoulSync", app_version: str = "1.0", contact_email: str = ""):
        """
        Initialize MusicBrainz client
        
        Args:
            app_name: Name of the application
            app_version: Version of the application
            contact_email: Contact email (optional but recommended)
        """
        self.user_agent = f"{app_name}/{app_version}"
        if contact_email:
            self.user_agent += f" ( {contact_email} )"
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self.user_agent,
            'Accept': 'application/json'
        })
        
        logger.info(f"MusicBrainz client initialized with user agent: {self.user_agent}")
    
    @rate_limited
    def search_artist(self, artist_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for artists by name
        
        Args:
            artist_name: Name of the artist to search for
            limit: Maximum number of results to return
            
        Returns:
            List of artist results with id, name, score, etc.
        """
        try:
            # Escape quotes and backslashes for Lucene query
            safe_name = artist_name.replace('\\', '\\\\').replace('"', '\\"')
            
            params = {
                'query': f'artist:"{safe_name}"',
                'fmt': 'json',
                'limit': limit
            }
            
            response = self.session.get(
                f"{self.BASE_URL}/artist",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            artists = data.get('artists', [])
            
            logger.debug(f"Found {len(artists)} artists for query: {artist_name}")
            return artists
            
        except Exception as e:
            logger.error(f"Error searching for artist '{artist_name}': {e}")
            return []
    
    @rate_limited
    def search_release(self, album_name: str, artist_name: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for releases (albums) by name
        
        Args:
            album_name: Name of the album to search for
            artist_name: Optional artist name to narrow search
            limit: Maximum number of results to return
            
        Returns:
            List of release results
        """
        try:
            # Escape quotes and backslashes for Lucene query
            safe_album = album_name.replace('\\', '\\\\').replace('"', '\\"')
            query = f'release:"{safe_album}"'
            
            if artist_name:
                safe_artist = artist_name.replace('\\', '\\\\').replace('"', '\\"')
                query += f' AND artist:"{safe_artist}"'
            
            params = {
                'query': query,
                'fmt': 'json',
                'limit': limit
            }
            
            response = self.session.get(
                f"{self.BASE_URL}/release",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            releases = data.get('releases', [])
            
            logger.debug(f"Found {len(releases)} releases for query: {album_name}")
            return releases
            
        except Exception as e:
            logger.error(f"Error searching for release '{album_name}': {e}")
            return []
    
    @rate_limited
    def search_recording(self, track_name: str, artist_name: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for recordings (tracks) by name
        
        Args:
            track_name: Name of the track to search for
            artist_name: Optional artist name to narrow search
            limit: Maximum number of results to return
            
        Returns:
            List of recording results
        """
        try:
            # Escape quotes and backslashes for Lucene query
            safe_track = track_name.replace('\\', '\\\\').replace('"', '\\"')
            query = f'recording:"{safe_track}"'
            
            if artist_name:
                safe_artist = artist_name.replace('\\', '\\\\').replace('"', '\\"')
                query += f' AND artist:"{safe_artist}"'
            
            params = {
                'query': query,
                'fmt': 'json',
                'limit': limit
            }
            
            response = self.session.get(
                f"{self.BASE_URL}/recording",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            recordings = data.get('recordings', [])
            
            logger.debug(f"Found {len(recordings)} recordings for query: {track_name}")
            return recordings
            
        except Exception as e:
            logger.error(f"Error searching for recording '{track_name}': {e}")
            return []
    
    @rate_limited
    def get_artist(self, mbid: str, includes: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Get full artist details by MusicBrainz ID
        
        Args:
            mbid: MusicBrainz ID of the artist
            includes: Optional list of additional data to include (e.g., 'url-rels', 'genres')
            
        Returns:
            Artist data or None if not found
        """
        try:
            params = {'fmt': 'json'}
            if includes:
                params['inc'] = '+'.join(includes)
            
            response = self.session.get(
                f"{self.BASE_URL}/artist/{mbid}",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching artist {mbid}: {e}")
            return None
    
    @rate_limited
    def get_release(self, mbid: str, includes: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Get full release details by MusicBrainz ID
        
        Args:
            mbid: MusicBrainz ID of the release
            includes: Optional list of additional data to include
            
        Returns:
            Release data or None if not found
        """
        try:
            params = {'fmt': 'json'}
            if includes:
                params['inc'] = '+'.join(includes)
            
            response = self.session.get(
                f"{self.BASE_URL}/release/{mbid}",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching release {mbid}: {e}")
            return None
    
    @rate_limited
    def get_recording(self, mbid: str, includes: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Get full recording details by MusicBrainz ID
        
        Args:
            mbid: MusicBrainz ID of the recording
            includes: Optional list of additional data to include
            
        Returns:
            Recording data or None if not found
        """
        try:
            params = {'fmt': 'json'}
            if includes:
                params['inc'] = '+'.join(includes)
            
            response = self.session.get(
                f"{self.BASE_URL}/recording/{mbid}",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching recording {mbid}: {e}")
            return None
