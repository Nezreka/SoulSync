import requests
import time
import threading
from typing import Dict, Optional, Any
from functools import wraps
from utils.logging_config import get_logger

logger = get_logger("audiodb_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 2.0  # 2 seconds between API calls (30 req/min free tier)

def rate_limited(func):
    """Decorator to enforce rate limiting on AudioDB API calls"""
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
                logger.warning(f"AudioDB rate limit hit, implementing backoff: {e}")
                time.sleep(4.0)
            raise e
    return wrapper


class AudioDBClient:
    """Client for interacting with TheAudioDB API"""

    BASE_URL = "https://www.theaudiodb.com/api/v1/json/2"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json'
        })
        logger.info("AudioDB client initialized")

    @rate_limited
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for an artist by name.

        Args:
            artist_name: Name of the artist to search for

        Returns:
            Artist dict from AudioDB or None if not found
        """
        try:
            response = self.session.get(
                f"{self.BASE_URL}/search.php",
                params={'s': artist_name},
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            artists = data.get('artists')

            if artists and len(artists) > 0:
                logger.debug(f"Found artist for query: {artist_name}")
                return artists[0]

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
            Album dict from AudioDB or None if not found
        """
        try:
            response = self.session.get(
                f"{self.BASE_URL}/searchalbum.php",
                params={'s': artist_name, 'a': album_title},
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            albums = data.get('album')

            if albums and len(albums) > 0:
                logger.debug(f"Found album for query: {artist_name} - {album_title}")
                return albums[0]

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
            Track dict from AudioDB or None if not found
        """
        try:
            response = self.session.get(
                f"{self.BASE_URL}/searchtrack.php",
                params={'s': artist_name, 't': track_title},
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            tracks = data.get('track')

            if tracks and len(tracks) > 0:
                logger.debug(f"Found track for query: {artist_name} - {track_title}")
                return tracks[0]

            logger.debug(f"No track found for query: {artist_name} - {track_title}")
            return None

        except Exception as e:
            logger.error(f"Error searching for track '{artist_name} - {track_title}': {e}")
            return None
