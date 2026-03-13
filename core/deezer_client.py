import re
import requests
import time
import threading
from typing import Dict, List, Optional, Any
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

    @rate_limited
    def get_playlist(self, playlist_id) -> Optional[Dict[str, Any]]:
        """
        Get a playlist with all its tracks by ID.

        Fetches playlist metadata and tracks, paginating if the playlist
        contains more tracks than a single response returns (400 per page).

        Args:
            playlist_id: Deezer playlist ID (string or int)

        Returns:
            Dict with id, name, description, track_count, image_url, owner,
            and tracks list, or None on error
        """
        try:
            playlist_id = str(playlist_id)

            response = self.session.get(
                f"{self.BASE_URL}/playlist/{playlist_id}",
                timeout=15
            )
            response.raise_for_status()

            data = response.json()
            if 'error' in data:
                logger.error(f"Deezer API error getting playlist {playlist_id}: {data['error']}")
                return None

            total_tracks = data.get('nb_tracks', 0)
            raw_tracks = data.get('tracks', {}).get('data', [])

            # Paginate if we didn't get all tracks
            while len(raw_tracks) < total_tracks:
                index = len(raw_tracks)
                logger.debug(f"Paginating playlist {playlist_id} tracks at index {index}")
                page_response = self.session.get(
                    f"{self.BASE_URL}/playlist/{playlist_id}/tracks",
                    params={'index': index, 'limit': 400},
                    timeout=15
                )
                page_response.raise_for_status()

                page_data = page_response.json()
                if 'error' in page_data:
                    logger.warning(f"Error paginating playlist tracks at index {index}: {page_data['error']}")
                    break

                page_tracks = page_data.get('data', [])
                if not page_tracks:
                    break

                raw_tracks.extend(page_tracks)

            # Normalize tracks
            tracks: List[Dict[str, Any]] = []
            for i, t in enumerate(raw_tracks, start=1):
                artist_name = t.get('artist', {}).get('name', 'Unknown Artist')
                # Some tracks list multiple artists separated by commas or slashes
                tracks.append({
                    'id': str(t.get('id', '')),
                    'name': t.get('title', ''),
                    'artists': [artist_name],
                    'album': t.get('album', {}).get('title', ''),
                    'duration_ms': t.get('duration', 0) * 1000,
                    'track_number': i,
                })

            result = {
                'id': str(data.get('id', '')),
                'name': data.get('title', ''),
                'description': data.get('description', ''),
                'track_count': total_tracks,
                'image_url': data.get('picture_medium', ''),
                'owner': data.get('creator', {}).get('name', ''),
                'tracks': tracks,
            }

            logger.info(f"Fetched playlist '{result['name']}' with {len(tracks)} tracks")
            return result

        except Exception as e:
            logger.error(f"Error getting playlist {playlist_id}: {e}")
            return None

    @staticmethod
    def parse_playlist_url(url: str) -> Optional[str]:
        """
        Extract a Deezer playlist ID from a URL or raw numeric string.

        Supported formats:
            https://www.deezer.com/playlist/1234567890
            https://www.deezer.com/en/playlist/1234567890
            https://deezer.com/playlist/1234567890
            1234567890

        Args:
            url: Deezer playlist URL or numeric ID

        Returns:
            Playlist ID as a string, or None if the input is invalid
        """
        if not url or not isinstance(url, str):
            return None

        url = url.strip()

        # Raw numeric ID
        if url.isdigit():
            return url

        # URL pattern: optional www, optional locale segment, /playlist/{id}
        match = re.match(
            r'https?://(?:www\.)?deezer\.com/(?:[a-z]{2}/)?playlist/(\d+)',
            url
        )
        if match:
            return match.group(1)

        return None
