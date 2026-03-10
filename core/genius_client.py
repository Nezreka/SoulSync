import requests
import re
import time
import threading
from typing import Dict, Optional, Any, List
from functools import wraps
from utils.logging_config import get_logger

logger = get_logger("genius_client")

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 0.5  # 500ms between calls (Genius doesn't publish limits, be conservative)


def rate_limited(func):
    """Decorator to enforce rate limiting on Genius API calls"""
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
                logger.warning(f"Genius rate limit hit, implementing backoff: {e}")
                time.sleep(10.0)
            raise e
    return wrapper


class GeniusClient:
    """Client for interacting with the Genius API (metadata + lyrics scraping)"""

    BASE_URL = "https://api.genius.com"

    def __init__(self, access_token: str = ""):
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json'
        })
        # Separate session for web scraping (no auth header)
        self.scrape_session = requests.Session()
        self.scrape_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        logger.info("Genius client initialized")

    def _make_request(self, endpoint: str, params: Dict = None, timeout: int = 10) -> Optional[Dict]:
        """Make an authenticated request to the Genius API"""
        if not self.access_token:
            logger.warning("Genius access token not configured")
            return None

        headers = {
            'Authorization': f'Bearer {self.access_token}'
        }

        try:
            response = self.session.get(
                f"{self.BASE_URL}{endpoint}",
                params=params or {},
                headers=headers,
                timeout=timeout
            )

            if response.status_code == 401:
                logger.error("Genius API: Invalid access token")
                return None
            if response.status_code == 404:
                return None

            response.raise_for_status()

            data = response.json()
            meta = data.get('meta', {})
            if meta.get('status') != 200:
                logger.error(f"Genius API error: {meta}")
                return None

            return data.get('response')

        except requests.exceptions.Timeout:
            logger.warning(f"Genius API timeout for endpoint: {endpoint}")
            return None
        except Exception as e:
            logger.error(f"Genius API request error ({endpoint}): {e}")
            return None

    # ── Search Methods ──

    @rate_limited
    def search(self, query: str, per_page: int = 5) -> List[Dict[str, Any]]:
        """
        Search Genius for songs matching a query.

        Returns:
            List of hit dicts, each containing a 'result' with:
            id, title, artist_names, url, song_art_image_url, lyrics_state
        """
        data = self._make_request('/search', {
            'q': query,
            'per_page': per_page
        })
        if not data:
            return []

        hits = data.get('hits', [])
        return [h for h in hits if h.get('type') == 'song']

    @rate_limited
    def search_song(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for a specific song by artist and title.
        Returns the best matching song result.

        Returns:
            Song dict with: id, title, artist_names, url, song_art_image_url,
            primary_artist (id, name, url, image_url), album (id, name, url)
        """
        query = f"{artist_name} {track_title}"
        hits = self.search(query, per_page=5)

        if not hits:
            logger.debug(f"No results for: {query}")
            return None

        # Try to find best match by checking artist name
        artist_lower = artist_name.lower().strip()
        title_lower = track_title.lower().strip()

        for hit in hits:
            result = hit.get('result', {})
            result_artist = (result.get('artist_names') or '').lower()
            result_title = (result.get('title') or '').lower()

            # Check if artist and title match reasonably
            if artist_lower in result_artist or result_artist in artist_lower:
                if title_lower in result_title or result_title in title_lower:
                    logger.debug(f"Found song match: {result.get('title')} by {result.get('artist_names')}")
                    return result

        # Fall back to first result
        first = hits[0].get('result')
        if first:
            logger.debug(f"Using first result: {first.get('title')} by {first.get('artist_names')}")
            return first

        return None

    # ── Song Methods ──

    @rate_limited
    def get_song(self, song_id: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed song info by Genius song ID.

        Returns:
            Song dict with: id, title, artist_names, url, song_art_image_url,
            description (dom object), album, media, custom_performances,
            producer_artists, writer_artists, featured_artists
        """
        data = self._make_request(f'/songs/{song_id}', {
            'text_format': 'plain'
        })
        if not data:
            return None

        song = data.get('song')
        if song:
            logger.debug(f"Got song info for ID: {song_id}")
            return song

        return None

    # ── Artist Methods ──

    @rate_limited
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for an artist by name (via song search, extract primary_artist).

        Returns:
            Artist dict with: id, name, url, image_url
        """
        hits = self.search(artist_name, per_page=5)
        if not hits:
            return None

        artist_lower = artist_name.lower().strip()

        for hit in hits:
            result = hit.get('result', {})
            primary = result.get('primary_artist', {})
            if primary and artist_lower in (primary.get('name') or '').lower():
                logger.debug(f"Found artist: {primary.get('name')}")
                return primary

        # Fall back to first result's primary artist
        first_artist = hits[0].get('result', {}).get('primary_artist')
        if first_artist:
            return first_artist

        return None

    @rate_limited
    def get_artist(self, artist_id: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed artist info by Genius artist ID.

        Returns:
            Artist dict with: id, name, url, image_url, description,
            alternate_names, facebook_name, twitter_name
        """
        data = self._make_request(f'/artists/{artist_id}', {
            'text_format': 'plain'
        })
        if not data:
            return None

        artist = data.get('artist')
        if artist:
            logger.debug(f"Got artist info for ID: {artist_id}")
            return artist

        return None

    @rate_limited
    def get_artist_songs(self, artist_id: int, sort: str = 'popularity', per_page: int = 20) -> List[Dict[str, Any]]:
        """
        Get songs by an artist.

        Args:
            artist_id: Genius artist ID
            sort: Sort order ('popularity', 'title')
            per_page: Results per page

        Returns:
            List of song dicts
        """
        data = self._make_request(f'/artists/{artist_id}/songs', {
            'sort': sort,
            'per_page': per_page
        })
        if not data:
            return []

        return data.get('songs', [])

    # ── Lyrics Scraping ──

    @rate_limited
    def get_lyrics(self, song_url: str) -> Optional[str]:
        """
        Scrape lyrics from a Genius song page.
        The Genius API doesn't provide lyrics directly — they must be scraped from the web page.

        Args:
            song_url: Full Genius URL (e.g. https://genius.com/Artist-song-lyrics)

        Returns:
            Lyrics text or None
        """
        if not song_url:
            return None

        try:
            response = self.scrape_session.get(song_url, timeout=15)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch lyrics page: {response.status_code}")
                return None

            html = response.text

            # Extract lyrics from the page
            # Genius wraps lyrics in <div data-lyrics-container="true">
            lyrics_parts = []
            pattern = r'<div[^>]*data-lyrics-container="true"[^>]*>(.*?)</div>'
            matches = re.findall(pattern, html, re.DOTALL)

            if not matches:
                logger.debug(f"No lyrics containers found on page: {song_url}")
                return None

            for match in matches:
                # Clean HTML tags
                text = re.sub(r'<br\s*/?>', '\n', match)
                text = re.sub(r'<[^>]+>', '', text)
                # Decode HTML entities
                text = text.replace('&amp;', '&')
                text = text.replace('&lt;', '<')
                text = text.replace('&gt;', '>')
                text = text.replace('&#x27;', "'")
                text = text.replace('&quot;', '"')
                text = text.replace('&#39;', "'")
                lyrics_parts.append(text.strip())

            lyrics = '\n'.join(lyrics_parts).strip()
            if lyrics:
                logger.debug(f"Scraped {len(lyrics)} chars of lyrics from: {song_url}")
                return lyrics

            return None

        except Exception as e:
            logger.error(f"Error scraping lyrics from {song_url}: {e}")
            return None

    def search_and_get_lyrics(self, artist_name: str, track_title: str) -> Optional[str]:
        """
        Convenience method: search for a song and scrape its lyrics.

        Returns:
            Lyrics text or None
        """
        song = self.search_song(artist_name, track_title)
        if not song:
            return None

        url = song.get('url')
        if not url:
            return None

        # Check if lyrics are available
        lyrics_state = song.get('lyrics_state', '')
        if lyrics_state == 'unreleased':
            logger.debug(f"Lyrics unreleased for: {artist_name} - {track_title}")
            return None

        return self.get_lyrics(url)

    # ── Utility Methods ──

    def extract_description(self, description_data) -> Optional[str]:
        """
        Extract plain text description from Genius description object.
        When text_format=plain, description comes as {plain: "text"}.
        """
        if not description_data:
            return None

        if isinstance(description_data, dict):
            plain = description_data.get('plain', '')
            if plain and plain.strip() and plain.strip() != '?':
                return plain.strip()

        if isinstance(description_data, str) and description_data.strip():
            return description_data.strip()

        return None

    def validate_token(self) -> bool:
        """Test if the access token is valid by making a simple request"""
        if not self.access_token:
            return False

        data = self._make_request('/search', {'q': 'test', 'per_page': 1})
        return data is not None
