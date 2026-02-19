import requests
import time
import threading
from typing import Dict, Optional, Any
from functools import wraps
from utils.logging_config import get_logger

logger = get_logger("deezer_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 1.0  # 1 second between API calls (Deezer soft limit: 50 req/5s)

def rate_limited(func):
    """Decorator to enforce rate limiting on Deezer API calls"""
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
            if "rate limit" in str(e).lower() or "429" in str(e):
                logger.warning(f"Deezer rate limit hit, implementing backoff: {e}")
                time.sleep(4.0)
            raise e
    return wrapper


class DeezerClient:
    """Client for interacting with the Deezer API"""

    BASE_URL = "https://api.deezer.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json'
        })
        logger.info("Deezer client initialized")

    @rate_limited
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for an artist by name.

        Args:
            artist_name: Name of the artist to search for

        Returns:
            Artist dict from Deezer or None if not found
        """
        try:
            response = self.session.get(
                f"{self.BASE_URL}/search/artist",
                params={'q': artist_name, 'strict': 'on'},
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error searching artist '{artist_name}': {data['error']}")
                return None

            results = data.get('data', [])
            if results and len(results) > 0:
                logger.debug(f"Found artist for query: {artist_name}")
                return results[0]

            logger.debug(f"No artist found for query: {artist_name}")
            return None

        except Exception as e:
            logger.error(f"Error searching for artist '{artist_name}': {e}")
            return None

    @rate_limited
    def search_album(self, artist_name: str, album_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for an album by artist name and album title.

        Args:
            artist_name: Name of the artist
            album_title: Title of the album

        Returns:
            Album dict from Deezer or None if not found
        """
        try:
            query = f"{artist_name} {album_title}"
            response = self.session.get(
                f"{self.BASE_URL}/search/album",
                params={'q': query},
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error searching album '{query}': {data['error']}")
                return None

            results = data.get('data', [])
            if results and len(results) > 0:
                logger.debug(f"Found album for query: {artist_name} - {album_title}")
                return results[0]

            logger.debug(f"No album found for query: {artist_name} - {album_title}")
            return None

        except Exception as e:
            logger.error(f"Error searching for album '{artist_name} - {album_title}': {e}")
            return None

    @rate_limited
    def search_track(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for a track by artist name and track title.

        Args:
            artist_name: Name of the artist
            track_title: Title of the track

        Returns:
            Track dict from Deezer or None if not found
        """
        try:
            query = f'artist:"{artist_name}" track:"{track_title}"'
            response = self.session.get(
                f"{self.BASE_URL}/search",
                params={'q': query},
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error searching track '{query}': {data['error']}")
                return None

            results = data.get('data', [])
            if results and len(results) > 0:
                logger.debug(f"Found track for query: {artist_name} - {track_title}")
                return results[0]

            logger.debug(f"No track found for query: {artist_name} - {track_title}")
            return None

        except Exception as e:
            logger.error(f"Error searching for track '{artist_name} - {track_title}': {e}")
            return None

    @rate_limited
    def get_album(self, album_id: int) -> Optional[Dict[str, Any]]:
        """
        Get full album details by ID.

        Args:
            album_id: Deezer album ID

        Returns:
            Full album dict with label, genres, explicit flag or None
        """
        try:
            response = self.session.get(
                f"{self.BASE_URL}/album/{album_id}",
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error getting album {album_id}: {data['error']}")
                return None

            logger.debug(f"Got full album details for ID: {album_id}")
            return data

        except Exception as e:
            logger.error(f"Error getting album {album_id}: {e}")
            return None

    @rate_limited
    def get_track(self, track_id: int) -> Optional[Dict[str, Any]]:
        """
        Get full track details by ID (includes BPM).

        Args:
            track_id: Deezer track ID

        Returns:
            Full track dict with BPM or None
        """
        try:
            response = self.session.get(
                f"{self.BASE_URL}/track/{track_id}",
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error getting track {track_id}: {data['error']}")
                return None

            logger.debug(f"Got full track details for ID: {track_id}")
            return data

        except Exception as e:
            logger.error(f"Error getting track {track_id}: {e}")
            return None
