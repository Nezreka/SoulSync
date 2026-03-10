import requests
import time
import threading
from typing import Dict, Optional, Any, List
from functools import wraps
from utils.logging_config import get_logger

logger = get_logger("lastfm_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 0.2  # 200ms between calls (Last.fm allows 5 req/sec)


def rate_limited(func):
    """Decorator to enforce rate limiting on Last.fm API calls"""
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
                logger.warning(f"Last.fm rate limit hit, implementing backoff: {e}")
                time.sleep(5.0)
            raise e
    return wrapper


class LastFMClient:
    """Client for interacting with the Last.fm API (read-only metadata)"""

    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json'
        })
        logger.info("Last.fm client initialized")

    def _make_request(self, method: str, params: Dict = None, timeout: int = 10, raise_on_transient: bool = False) -> Optional[Dict]:
        """Make a request to the Last.fm API.

        Args:
            raise_on_transient: If True, raise exceptions on transient errors (timeouts, HTTP errors)
                instead of returning None. Used by get_*_info methods so the worker can distinguish
                'not found' (mark not_found, retry in 30 days) from 'API failed' (mark error, retry in 7 days).
        """
        if not self.api_key:
            logger.warning("Last.fm API key not configured")
            return None

        request_params = {
            'method': method,
            'api_key': self.api_key,
            'format': 'json'
        }
        if params:
            request_params.update(params)

        try:
            response = self.session.get(
                self.BASE_URL,
                params=request_params,
                timeout=timeout
            )
            response.raise_for_status()

            data = response.json()

            # Last.fm returns errors inside the JSON
            if 'error' in data:
                error_code = data.get('error')
                error_msg = data.get('message', 'Unknown error')
                # Error 6 = "Artist/Album/Track not found" — not a real error
                if error_code == 6:
                    return None
                # Transient errors: 11=Service Offline, 16=Temporarily Unavailable, 29=Rate Limit
                if raise_on_transient and error_code in (11, 16, 29):
                    raise Exception(f"Last.fm transient error ({error_code}): {error_msg}")
                logger.error(f"Last.fm API error ({error_code}): {error_msg}")
                return None

            return data

        except requests.exceptions.Timeout:
            logger.warning(f"Last.fm API timeout for method: {method}")
            if raise_on_transient:
                raise
            return None
        except Exception as e:
            logger.error(f"Last.fm API request error ({method}): {e}")
            if raise_on_transient:
                raise
            return None

    # ── Artist Methods ──

    @rate_limited
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for an artist by name.

        Returns:
            Artist dict with: name, mbid, url, listeners, image
        """
        data = self._make_request('artist.search', {
            'artist': artist_name,
            'limit': 5
        })
        if not data:
            return None

        results = data.get('results', {}).get('artistmatches', {}).get('artist', [])
        if results and len(results) > 0:
            logger.debug(f"Found artist for query: {artist_name}")
            return results[0]

        logger.debug(f"No artist found for query: {artist_name}")
        return None

    @rate_limited
    def get_artist_info(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed artist info including bio, tags, stats.

        Returns:
            Artist dict with: name, mbid, url, image, stats (listeners, playcount),
            similar (artists), tags (tag list), bio (summary, content)
        """
        data = self._make_request('artist.getinfo', {
            'artist': artist_name,
            'autocorrect': 1
        }, raise_on_transient=True)
        if not data:
            return None

        artist = data.get('artist')
        if artist:
            logger.debug(f"Got artist info for: {artist_name}")
            return artist

        return None

    @rate_limited
    def get_artist_top_tags(self, artist_name: str) -> List[Dict[str, Any]]:
        """
        Get top tags for an artist (genres, styles, moods).

        Returns:
            List of tag dicts with: name, count, url
        """
        data = self._make_request('artist.gettoptags', {
            'artist': artist_name,
            'autocorrect': 1
        })
        if not data:
            return []

        tags = data.get('toptags', {}).get('tag', [])
        return tags if isinstance(tags, list) else [tags] if tags else []

    @rate_limited
    def get_similar_artists(self, artist_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get similar artists.

        Returns:
            List of artist dicts with: name, mbid, match (similarity score), url, image
        """
        data = self._make_request('artist.getsimilar', {
            'artist': artist_name,
            'autocorrect': 1,
            'limit': limit
        })
        if not data:
            return []

        artists = data.get('similarartists', {}).get('artist', [])
        return artists if isinstance(artists, list) else [artists] if artists else []

    # ── Album Methods ──

    @rate_limited
    def search_album(self, artist_name: str, album_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for an album.

        Returns:
            Album dict with: name, artist, url, image
        """
        data = self._make_request('album.search', {
            'album': f"{artist_name} {album_title}",
            'limit': 5
        })
        if not data:
            return None

        results = data.get('results', {}).get('albummatches', {}).get('album', [])
        if results and len(results) > 0:
            logger.debug(f"Found album for query: {artist_name} - {album_title}")
            return results[0]

        logger.debug(f"No album found for query: {artist_name} - {album_title}")
        return None

    @rate_limited
    def get_album_info(self, artist_name: str, album_title: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed album info including tags, tracks, wiki.

        Returns:
            Album dict with: name, artist, mbid, url, image, listeners, playcount,
            tracks (track list), tags (tag list), wiki (summary, content)
        """
        data = self._make_request('album.getinfo', {
            'artist': artist_name,
            'album': album_title,
            'autocorrect': 1
        }, raise_on_transient=True)
        if not data:
            return None

        album = data.get('album')
        if album:
            logger.debug(f"Got album info for: {artist_name} - {album_title}")
            return album

        return None

    # ── Track Methods ──

    @rate_limited
    def search_track(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for a track.

        Returns:
            Track dict with: name, artist, url, listeners
        """
        data = self._make_request('track.search', {
            'track': track_title,
            'artist': artist_name,
            'limit': 5
        })
        if not data:
            return None

        results = data.get('results', {}).get('trackmatches', {}).get('track', [])
        if results and len(results) > 0:
            logger.debug(f"Found track for query: {artist_name} - {track_title}")
            return results[0]

        logger.debug(f"No track found for query: {artist_name} - {track_title}")
        return None

    @rate_limited
    def get_track_info(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed track info including tags, wiki, play stats.

        Returns:
            Track dict with: name, mbid, url, duration, listeners, playcount,
            artist, album, toptags (tag list), wiki (summary, content)
        """
        data = self._make_request('track.getinfo', {
            'artist': artist_name,
            'track': track_title,
            'autocorrect': 1
        }, raise_on_transient=True)
        if not data:
            return None

        track = data.get('track')
        if track:
            logger.debug(f"Got track info for: {artist_name} - {track_title}")
            return track

        return None

    # ── Utility Methods ──

    def get_best_image(self, images: List) -> Optional[str]:
        """
        Extract the best quality image URL from Last.fm image array.
        Last.fm returns images as [{#text: url, size: small/medium/large/extralarge/mega}]
        """
        if not images or not isinstance(images, list):
            return None

        # Prefer largest
        for size in ['mega', 'extralarge', 'large', 'medium', 'small']:
            for img in images:
                if isinstance(img, dict) and img.get('size') == size:
                    url = img.get('#text', '')
                    if url:
                        return url

        return None

    def extract_tags(self, tags_data, max_tags: int = 10) -> List[str]:
        """
        Extract tag names from Last.fm tags response.
        Filters out low-count tags and normalizes.
        """
        if not tags_data:
            return []

        tag_list = tags_data if isinstance(tags_data, list) else tags_data.get('tag', [])
        if not isinstance(tag_list, list):
            tag_list = [tag_list] if tag_list else []

        tags = []
        for tag in tag_list[:max_tags]:
            if isinstance(tag, dict):
                name = tag.get('name', '').strip()
                if name and len(name) > 1:
                    tags.append(name)
            elif isinstance(tag, str):
                tags.append(tag.strip())

        return tags

    def validate_api_key(self) -> bool:
        """Test if the API key is valid by making a simple request"""
        if not self.api_key:
            return False

        data = self._make_request('chart.gettopartists', {'limit': 1})
        return data is not None
