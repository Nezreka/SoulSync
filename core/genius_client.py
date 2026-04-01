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
MIN_API_INTERVAL = 1.5  # 1.5s between calls — Genius 429s at 500ms
_rate_limit_backoff = 0  # Extra backoff seconds after 429
_rate_limit_until = 0    # Timestamp until which all calls should wait


def rate_limited(func):
    """Decorator to enforce rate limiting on Genius API calls with exponential backoff on 429"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _last_api_call_time, _rate_limit_backoff, _rate_limit_until

        with _api_call_lock:
            current_time = time.time()

            # If in backoff period from a previous 429, wait it out
            if current_time < _rate_limit_until:
                wait = _rate_limit_until - current_time
                logger.debug(f"Genius rate limit backoff: waiting {wait:.1f}s")
                time.sleep(wait)

            time_since_last_call = time.time() - _last_api_call_time
            if time_since_last_call < MIN_API_INTERVAL:
                time.sleep(MIN_API_INTERVAL - time_since_last_call)

            _last_api_call_time = time.time()

        try:
            result = func(*args, **kwargs)
            # Success — gradually reduce backoff
            if _rate_limit_backoff > 0:
                _rate_limit_backoff = max(0, _rate_limit_backoff - 5)
            return result
        except Exception as e:
            if "429" in str(e) or "rate limit" in str(e).lower():
                # Exponential backoff: 30s → 60s → 120s (cap at 120s)
                _rate_limit_backoff = min(120, max(30, _rate_limit_backoff * 2) if _rate_limit_backoff else 30)
                _rate_limit_until = time.time() + _rate_limit_backoff
                logger.warning(f"Genius 429 rate limit — backing off {_rate_limit_backoff}s")
                time.sleep(_rate_limit_backoff)
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
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                # Re-raise 429s so the rate_limited decorator can handle backoff
                raise
            logger.error(f"Genius API request error ({endpoint}): {e}")
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

        # No confident match — let the worker mark as not_found and retry later
        logger.debug(f"No song match found in search results for: {artist_name} - {track_title}")
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
        artists = self.search_artists(artist_name, limit=10)
        if not artists:
            return None

        artist_lower = artist_name.lower().strip()

        for a in artists:
            a_name = (a.get('name') or '').lower()
            if artist_lower in a_name or a_name in artist_lower:
                logger.debug(f"Found artist: {a.get('name')}")
                return a

        # No confident match — let the worker mark as not_found and retry later
        logger.debug(f"No artist match found in search results for: {artist_name}")
        return None

    @rate_limited
    def search_artists(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search for artists by name. Extracts unique artists from song results.

        Returns:
            List of artist dicts with: id, name, url, image_url
        """
        hits = self.search(query, per_page=min(limit * 2, 20))
        if not hits:
            return []

        seen_ids = set()
        artists = []
        for hit in hits:
            result = hit.get('result', {})
            primary = result.get('primary_artist', {})
            if primary and primary.get('id') and primary['id'] not in seen_ids:
                seen_ids.add(primary['id'])
                artists.append(primary)
                if len(artists) >= limit:
                    break

        return artists

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
